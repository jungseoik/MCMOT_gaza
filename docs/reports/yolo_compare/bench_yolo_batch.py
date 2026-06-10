"""Pure detection inference-time vs batch size for YOLO26 m / l / x.
Measures the GPU forward pass only (no preprocess, no NMS), FP16, imgsz=640,
so we can verify whether batching actually lowers per-frame inference time.

Run: CUDA_VISIBLE_DEVICES=1 python docs/reports/yolo_compare/bench_yolo_batch.py
"""
import os
import sys
import time
import json
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "1")
from pathlib import Path
import torch
from ultralytics import YOLO

HERE = Path(__file__).resolve().parent
W = HERE / "weights"
IMGSZ = int(os.environ.get("IMGSZ", "640"))
BATCHES = [int(b) for b in os.environ.get("BATCHES", "1,8,16,32,64,100,150").split(",")]
MODELS = os.environ.get("MODELS", "yolo26m,yolo26l,yolo26x").split(",")


def timeit(net, x, warmup=5, iters=20):
    with torch.no_grad():
        for _ in range(warmup):
            net(x)
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        for _ in range(iters):
            net(x)
        torch.cuda.synchronize()
    return (time.perf_counter() - t0) / iters * 1000.0   # ms/batch


def main():
    print(f"순수 검출 forward (FP16, imgsz={IMGSZ}), GPU={os.environ['CUDA_VISIBLE_DEVICES']}\n")
    results = []
    for mdl in MODELS:
        m = YOLO(str(W / f"{mdl}.pt"))
        net = m.model.eval().cuda().half()
        params = sum(p.numel() for p in net.parameters()) / 1e6
        row = {"model": mdl, "params_M": round(params, 1), "batches": []}
        print(f"=== {mdl} ({params:.1f}M) ===")
        for b in BATCHES:
            try:
                x = torch.randn(b, 3, IMGSZ, IMGSZ, device="cuda", dtype=torch.float16)
                ms = timeit(net, x)
                vram = torch.cuda.max_memory_allocated() / 1e9
                row["batches"].append({"batch": b, "ms_per_batch": round(ms, 2),
                                       "ms_per_frame": round(ms / b, 2),
                                       "fps": round(1000.0 * b / ms, 1),
                                       "peak_vram_GB": round(vram, 1)})
                print(f"  batch={b:3d} | {ms:8.2f} ms/batch (전체) | {ms/b:6.2f} ms/frame | "
                      f"{1000.0*b/ms:7.1f} fps | VRAM {vram:.1f}GB")
                del x; torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats()
            except RuntimeError as e:
                print(f"  batch={b:3d} | OOM/실패: {str(e)[:60]}")
                torch.cuda.empty_cache()
                break
        results.append(row)
    (HERE / "results_yolo_batch.json").write_text(json.dumps(results, indent=2))
    print("\nSaved results_yolo_batch.json")


if __name__ == "__main__":
    main()
