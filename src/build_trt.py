"""Export YOLOX & FastReID to ONNX and build TensorRT engines."""

import argparse
import os
import sys
import warnings
from pathlib import Path

import numpy as np
import torch
import tensorrt as trt

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

TRT_LOGGER = trt.Logger(trt.Logger.WARNING)


# ──────────────────────────────────────────────
# ONNX export helpers
# ──────────────────────────────────────────────

def export_yolox_onnx(weights: str, dataset: str, onnx_path: str, input_size=(896, 1600)):
    """Export raw YOLOX backbone to ONNX (without NMS)."""
    from external.adaptors.yolox_adaptor import Exp
    from yolox.utils import fuse_model

    exp = Exp(dataset)
    model = exp.get_model()
    ckpt = torch.load(weights, weights_only=False)
    model.load_state_dict(ckpt["model"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = fuse_model(model)
    model.cuda().eval().float()

    dummy = torch.randn(1, 3, *input_size, device="cuda", dtype=torch.float32)
    with torch.no_grad():
        torch.onnx.export(
            model, dummy, onnx_path,
            input_names=["images"],
            output_names=["output"],
            opset_version=17,
            do_constant_folding=True,
            dynamo=False,
        )
    print(f"[YOLOX] ONNX saved: {onnx_path}")


def export_fastreid_onnx(weights: str, onnx_path: str):
    """Export FastReID model to ONNX with dynamic batch."""
    from external.adaptors.fastreid_adaptor import setup_cfg
    from fast_reid.fastreid.modeling.meta_arch import build_model
    from fast_reid.fastreid.utils.checkpoint import Checkpointer

    config_file = "external/fast_reid/configs/MOT17/sbs_S50.yml"
    cfg = setup_cfg(config_file, ['MODEL.WEIGHTS', weights])
    model = build_model(cfg)
    Checkpointer(model).load(weights)
    model.eval().cuda().float()

    dummy = torch.randn(1, 3, 384, 128, device="cuda", dtype=torch.float32)
    with torch.no_grad():
        torch.onnx.export(
            model, dummy, onnx_path,
            input_names=["images"],
            output_names=["features"],
            dynamic_axes={"images": {0: "batch"}, "features": {0: "batch"}},
            opset_version=17,
            do_constant_folding=True,
            dynamo=False,
        )
    print(f"[FastReID] ONNX saved: {onnx_path}")


# ──────────────────────────────────────────────
# TensorRT engine builder
# ──────────────────────────────────────────────

def build_engine(onnx_path: str, engine_path: str, fp16: bool = False,
                 dynamic_batch: bool = False, max_batch: int = 256):
    """Build TensorRT engine from ONNX."""
    builder = trt.Builder(TRT_LOGGER)
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
    parser = trt.OnnxParser(network, TRT_LOGGER)

    with open(onnx_path, "rb") as f:
        if not parser.parse(f.read()):
            for i in range(parser.num_errors):
                print(f"  ONNX parse error: {parser.get_error(i)}")
            raise RuntimeError("Failed to parse ONNX")

    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 4 << 30)  # 4GB

    if fp16:
        config.set_flag(trt.BuilderFlag.FP16)

    if dynamic_batch:
        profile = builder.create_optimization_profile()
        inp = network.get_input(0)
        shape = inp.shape  # e.g. (-1, 3, 384, 128)
        min_shape = (1, *shape[1:])
        opt_shape = (32, *shape[1:])
        max_shape = (max_batch, *shape[1:])
        profile.set_shape(inp.name, min_shape, opt_shape, max_shape)
        config.add_optimization_profile(profile)

    precision = "FP16" if fp16 else "FP32"
    print(f"Building TRT engine ({precision}): {engine_path} ...")

    serialized = builder.build_serialized_network(network, config)
    if serialized is None:
        raise RuntimeError("TRT engine build failed")

    with open(engine_path, "wb") as f:
        f.write(serialized)
    print(f"  Engine saved: {engine_path} ({os.path.getsize(engine_path) / 1e6:.1f} MB)")


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build TensorRT engines")
    parser.add_argument("--detector_weights", default="external/weights/bytetrack_x_mot20.tar")
    parser.add_argument("--reid_weights", default="external/weights/mot20_sbs_S50.pth")
    parser.add_argument("--output_dir", default="external/weights/trt")
    parser.add_argument("--fp16", action="store_true", help="Build FP16 engines (in addition to FP32)")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # Step 1: ONNX export
    yolox_onnx = os.path.join(args.output_dir, "yolox_mot20.onnx")
    reid_onnx = os.path.join(args.output_dir, "fastreid_sbs_s50.onnx")

    if not os.path.exists(yolox_onnx):
        export_yolox_onnx(args.detector_weights, "mot20", yolox_onnx)
    if not os.path.exists(reid_onnx):
        export_fastreid_onnx(args.reid_weights, reid_onnx)

    # Step 2: Build FP32 engines
    yolox_fp32 = os.path.join(args.output_dir, "yolox_mot20_fp32.engine")
    reid_fp32 = os.path.join(args.output_dir, "fastreid_sbs_s50_fp32.engine")

    if not os.path.exists(yolox_fp32):
        build_engine(yolox_onnx, yolox_fp32, fp16=False)
    if not os.path.exists(reid_fp32):
        build_engine(reid_onnx, reid_fp32, fp16=False, dynamic_batch=True)

    # Step 3: Build FP16 engines
    if args.fp16:
        yolox_fp16 = os.path.join(args.output_dir, "yolox_mot20_fp16.engine")
        reid_fp16 = os.path.join(args.output_dir, "fastreid_sbs_s50_fp16.engine")

        if not os.path.exists(yolox_fp16):
            build_engine(yolox_onnx, yolox_fp16, fp16=True)
        if not os.path.exists(reid_fp16):
            build_engine(reid_onnx, reid_fp16, fp16=True, dynamic_batch=True)

    print("\nDone. Built engines:")
    for f in sorted(Path(args.output_dir).glob("*.engine")):
        print(f"  {f}  ({f.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
