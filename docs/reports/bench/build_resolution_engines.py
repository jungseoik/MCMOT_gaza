"""Build fixed-batch(=1) FP16 YOLOX TRT engines at several input resolutions,
for the detection-resolution scaling experiment.

A TRT engine bakes its input H×W at build time, so each resolution needs its
own engine (you cannot just feed a smaller tensor into the 896×1600 engine).

Baseline 896×1600 reuses the existing yolox_mot20_fp16.engine (not rebuilt).
Outputs: external/weights/trt/yolox_mot20_fp16_{H}x{W}.engine

Run: CUDA_VISIBLE_DEVICES=1 python docs/reports/bench/build_resolution_engines.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.build_trt import export_yolox_onnx, build_engine   # noqa: E402

TRT = ROOT / "external/weights/trt"
WEIGHTS = str(ROOT / "external/weights/bytetrack_x_mot20.tar")

# (H, W), all divisible by 32. Baseline + two smaller (≈half / ≈native 540p).
RESOLUTIONS = [(640, 1088), (576, 1024)]


def build(h, w):
    onnx = TRT / f"yolox_mot20_{h}x{w}.onnx"
    engine = TRT / f"yolox_mot20_fp16_{h}x{w}.engine"
    if engine.exists():
        print(f"[skip] {engine.name} exists")
        return
    if not onnx.exists():
        export_yolox_onnx(WEIGHTS, "mot20", str(onnx), input_size=(h, w))
    build_engine(str(onnx), str(engine), fp16=True)   # fixed batch=1
    print(f"[done] {engine.name}")


if __name__ == "__main__":
    for h, w in RESOLUTIONS:
        build(h, w)
    print("ALL DONE")
