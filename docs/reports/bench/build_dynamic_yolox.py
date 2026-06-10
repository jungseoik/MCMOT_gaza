"""Re-export YOLOX to ONNX with a DYNAMIC batch axis and build a dynamic-batch
TRT FP16 engine, so we can measure cross-channel batched detection latency.

Originals (yolox_mot20_fp16.engine, fixed batch=1) are left untouched.
Output: external/weights/trt/yolox_mot20_fp16_dynamic.engine  (batch 1..16)
"""
import os
import sys
import warnings
from pathlib import Path

import torch
import tensorrt as trt

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

TRT_LOGGER = trt.Logger(trt.Logger.WARNING)
OUT_DIR = ROOT / "external/weights/trt"
MAX_BATCH = int(os.environ.get("MAX_BATCH", "16"))
OPT_BATCH = int(os.environ.get("OPT_BATCH", "8"))
INPUT_H = int(os.environ.get("INPUT_H", "896"))
INPUT_W = int(os.environ.get("INPUT_W", "1600"))
_SQ = f"{INPUT_H}x{INPUT_W}"
# default 896x1600 keeps legacy names; other sizes get size-tagged names
if INPUT_H == 896 and INPUT_W == 1600:
    ONNX_DYN = OUT_DIR / "yolox_mot20_dynamic.onnx"
    ENGINE_DYN = OUT_DIR / (f"yolox_mot20_fp16_dynamic.engine" if MAX_BATCH == 16
                            else f"yolox_mot20_fp16_dynb{MAX_BATCH}.engine")
else:
    ONNX_DYN = OUT_DIR / f"yolox_mot20_dyn_{_SQ}.onnx"
    ENGINE_DYN = OUT_DIR / f"yolox_mot20_fp16_dyn_{_SQ}_b{MAX_BATCH}.engine"


def export_dynamic_onnx(weights, input_size=(INPUT_H, INPUT_W)):
    from external.adaptors.yolox_adaptor import Exp
    from yolox.utils import fuse_model
    exp = Exp("mot20")
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
            model, dummy, str(ONNX_DYN),
            input_names=["images"], output_names=["output"],
            dynamic_axes={"images": {0: "batch"}, "output": {0: "batch"}},
            opset_version=17, do_constant_folding=True, dynamo=False,
        )
    print(f"[YOLOX] dynamic ONNX saved: {ONNX_DYN}")


def build_engine():
    builder = trt.Builder(TRT_LOGGER)
    flags = 0
    if hasattr(trt.NetworkDefinitionCreationFlag, "EXPLICIT_BATCH"):
        flags = 1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH)
    network = builder.create_network(flags)
    parser = trt.OnnxParser(network, TRT_LOGGER)
    with open(ONNX_DYN, "rb") as f:
        if not parser.parse(f.read()):
            for i in range(parser.num_errors):
                print("ONNX parse error:", parser.get_error(i))
            raise RuntimeError("parse failed")
    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 8 << 30)
    config.set_flag(trt.BuilderFlag.FP16)
    profile = builder.create_optimization_profile()
    inp = network.get_input(0)
    shape = inp.shape  # (-1,3,896,1600)
    profile.set_shape(inp.name, (1, *shape[1:]), (OPT_BATCH, *shape[1:]),
                      (MAX_BATCH, *shape[1:]))
    config.add_optimization_profile(profile)
    print(f"Building dynamic FP16 engine (max_batch={MAX_BATCH}) ...")
    serialized = builder.build_serialized_network(network, config)
    if serialized is None:
        raise RuntimeError("engine build failed")
    with open(ENGINE_DYN, "wb") as f:
        f.write(serialized)
    print(f"  saved: {ENGINE_DYN} ({os.path.getsize(ENGINE_DYN)/1e6:.1f} MB)")


if __name__ == "__main__":
    if not ONNX_DYN.exists():
        export_dynamic_onnx(str(ROOT / "external/weights/bytetrack_x_mot20.tar"))
    build_engine()
    print("DONE")
