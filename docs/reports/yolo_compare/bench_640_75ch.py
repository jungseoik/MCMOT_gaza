"""640 입력 동일 조건에서 YOLOX-X(현재 모델) vs YOLO11m 배치 추론시간 비교.
가정: GPU 1개당 75채널(=배치75), 2GPU면 150채널. 배치 전체 추론시간 + fps.

- YOLOX-X @640×640: TensorRT FP16 (dyn_640x640_b75 엔진), FP32 입력 바인딩
- YOLO11m @640    : PyTorch FP16 forward (TRT로 export하면 더 빨라짐 → YOLO11 수치는 보수적)
검출 forward만 측정 (전처리·NMS·추적 제외).

Run: CUDA_VISIBLE_DEVICES=1 python docs/reports/yolo_compare/bench_640_75ch.py
"""
import os
import sys
import time
import json
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "1")
from pathlib import Path
import torch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from src.inference_trt import TRTEngine

YOLOX_ENG = ROOT / "external/weights/trt/yolox_mot20_fp16_dyn_640x640_b75.engine"
YOLO11M = HERE / "weights/yolo11m.pt"
SZ = 640
BATCHES = [1, 8, 16, 32, 50, 64, 75]


def measure(fn, warmup=5, iters=15):
    for _ in range(warmup):
        fn()
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(iters):
        fn()
    torch.cuda.synchronize()
    return (time.perf_counter() - t0) / iters * 1000.0


def run_curve(label, callable_factory):
    print(f"=== {label} ===")
    rows = []
    for b in BATCHES:
        try:
            torch.cuda.reset_peak_memory_stats()
            fn, cleanup = callable_factory(b)
            ms = measure(fn)
            vram = torch.cuda.max_memory_allocated() / 1e9
            rows.append({"batch": b, "ms_per_batch": round(ms, 1),
                         "ms_per_frame": round(ms / b, 2),
                         "fps": round(1000.0 * b / ms, 1), "vram_GB": round(vram, 1)})
            print(f"  batch={b:3d} | {ms:8.1f} ms (전체) | {ms/b:5.2f} ms/frame | "
                  f"{1000.0*b/ms:6.1f} fps | VRAM {vram:.1f}GB")
            cleanup()
            torch.cuda.empty_cache()
        except RuntimeError as e:
            print(f"  batch={b:3d} | OOM/실패: {str(e)[:60]}")
            torch.cuda.empty_cache(); break
    return rows


def yolox_factory(b):
    x = torch.randn(b, 3, SZ, SZ, device="cuda")     # FP32 input binding
    return (lambda: eng_yolox(x)), (lambda: None)


def yolo11_factory(b):
    x = torch.randn(b, 3, SZ, SZ, device="cuda", dtype=torch.float16)
    return (lambda: net11(x)), (lambda: None)


if __name__ == "__main__":
    print(f"640 입력 배치 비교 (GPU={os.environ['CUDA_VISIBLE_DEVICES']})\n")
    results = {}

    eng_yolox = TRTEngine(str(YOLOX_ENG))
    results["yolox_x_640_trt"] = run_curve("YOLOX-X @640 (TRT FP16, 현재 모델, 99M)", yolox_factory)
    del eng_yolox; torch.cuda.empty_cache()

    from ultralytics import YOLO
    net11 = YOLO(str(YOLO11M)).model.eval().cuda().half()
    with torch.no_grad():
        results["yolo11m_640_torch"] = run_curve("YOLO11m @640 (PyTorch FP16, 20M)", yolo11_factory)

    (HERE / "results_640_75ch.json").write_text(json.dumps(results, indent=2))
    print("\nSaved results_640_75ch.json")
