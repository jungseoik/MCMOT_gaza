"""YOLOX-X (current detector) batched inference time up to batch 150, at its
REAL condition: 896×1600, TensorRT FP16. Total batch latency + per-frame + VRAM.

Mirrors yolo_compare/bench_yolo_batch.py but for the model actually used in the
pipeline — so we can compare "150 channels as one batch" on the real detector.
NOTE conditions differ from YOLO26 bench (896×1600 TRT vs 640 PyTorch).

Run: CUDA_VISIBLE_DEVICES=1 python docs/reports/bench/bench_yolox_batch150.py
"""
import os
import sys
import time
import json
from pathlib import Path
import torch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.inference_trt import TRTEngine          # noqa: E402

ENGINE = ROOT / "external/weights/trt/yolox_mot20_fp16_dynb32.engine"
HW = (896, 1600)
BATCHES = [1, 4, 8, 16, 24, 32]


def main():
    print(f"YOLOX-X 배치 추론시간 (TRT FP16, {HW[0]}x{HW[1]}), GPU={os.environ.get('CUDA_VISIBLE_DEVICES')}\n")
    eng = TRTEngine(str(ENGINE))
    rows = []
    for b in BATCHES:
        try:
            torch.cuda.reset_peak_memory_stats()
            x = torch.randn(b, 3, *HW, device="cuda")   # FP32 input (engine binding)
            # warmup
            for _ in range(3):
                eng(x)
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            iters = 10
            for _ in range(iters):
                eng(x)
            torch.cuda.synchronize()
            ms = (time.perf_counter() - t0) / iters * 1000.0
            vram = torch.cuda.max_memory_allocated() / 1e9
            rows.append({"batch": b, "ms_per_batch": round(ms, 1),
                         "ms_per_frame": round(ms / b, 2),
                         "fps": round(1000.0 * b / ms, 1),
                         "peak_vram_GB": round(vram, 1)})
            print(f"  batch={b:3d} | {ms:9.1f} ms/batch (전체) | {ms/b:6.2f} ms/frame | "
                  f"{1000.0*b/ms:6.1f} fps | VRAM {vram:.1f}GB")
            del x; torch.cuda.empty_cache()
        except RuntimeError as e:
            print(f"  batch={b:3d} | OOM/실패: {str(e)[:70]}")
            torch.cuda.empty_cache()
            rows.append({"batch": b, "error": "OOM"})
            break
    out = Path(__file__).resolve().parent / "results_yolox_batch150.json"
    out.write_text(json.dumps(rows, indent=2))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
