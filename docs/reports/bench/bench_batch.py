"""Empirical batched-inference latency benchmark for MACS multi-channel scaling.

Measures, on THIS machine, how detection / ReID latency grows when N channels'
frames are batched into one GPU call (1..15 channels), plus the per-frame CPU
cost that does NOT batch (preproc) and the real single-stream pipeline FPS.

Run:  CUDA_VISIBLE_DEVICES=1 python docs/reports/bench/bench_batch.py
Output: prints tables + writes results JSON next to this file.
"""
import os
import sys
import json
import time
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.inference_trt import TRTEngine          # noqa: E402
from dataset import preproc                       # noqa: E402

TRT = ROOT / "external/weights/trt"
YOLOX_FIXED = TRT / "yolox_mot20_fp16.engine"
YOLOX_DYN = TRT / "yolox_mot20_fp16_dynamic.engine"
REID = TRT / "fastreid_sbs_s50_fp16.engine"
SAMPLE = ROOT / "assets/sample1.mp4"
INPUT_HW = (896, 1600)

CHANNELS = [1, 2, 4, 8, 12, 15]      # batched channels (detection)
REID_BATCH = [1, 8, 16, 32, 50, 64, 128, 256]
PEOPLE_PER_FRAME = 40                 # assumed crops/channel for the combined model


def timeit(fn, warmup=10, iters=50):
    for _ in range(warmup):
        fn()
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(iters):
        fn()
    torch.cuda.synchronize()
    return (time.perf_counter() - t0) / iters * 1000.0   # ms/call


def bench_detection():
    print("\n=== [A] YOLOX detection latency vs batched channels (896x1600 FP16) ===")
    rows = []
    # batch=1 on the fixed engine (baseline, identical to production)
    eng1 = TRTEngine(str(YOLOX_FIXED))
    x1 = torch.randn(1, 3, *INPUT_HW, device="cuda")
    ms1 = timeit(lambda: eng1(x1))
    rows.append({"channels": 1, "engine": "fixed",
                 "batch_ms": round(ms1, 2), "per_frame_ms": round(ms1, 2)})
    print(f"  ch= 1 (fixed engine): {ms1:6.2f} ms/batch  {ms1:6.2f} ms/frame")
    del eng1, x1
    torch.cuda.empty_cache()

    if YOLOX_DYN.exists():
        engd = TRTEngine(str(YOLOX_DYN))
        for b in CHANNELS:
            try:
                x = torch.randn(b, 3, *INPUT_HW, device="cuda")
                ms = timeit(lambda: engd(x), warmup=5, iters=30)
                rows.append({"channels": b, "engine": "dynamic",
                             "batch_ms": round(ms, 2),
                             "per_frame_ms": round(ms / b, 2)})
                print(f"  ch={b:2d} (dynamic)     : {ms:6.2f} ms/batch  "
                      f"{ms/b:6.2f} ms/frame  (x{ms1*b/ms:.2f} throughput vs serial)")
                del x
                torch.cuda.empty_cache()
            except Exception as e:
                print(f"  ch={b:2d}: FAILED {e}")
    else:
        print("  (dynamic engine not built yet — only batch=1 measured)")
    return rows


def bench_reid():
    print("\n=== [B] FastReID latency vs batch (crops, 384x128 FP16, dynamic engine) ===")
    eng = TRTEngine(str(REID))
    rows = []
    for m in REID_BATCH:
        x = torch.randn(m, 3, 384, 128, device="cuda")
        ms = timeit(lambda: eng(x))
        rows.append({"crops": m, "batch_ms": round(ms, 3),
                     "per_crop_ms": round(ms / m, 4)})
        print(f"  crops={m:3d}: {ms:6.3f} ms/batch  {ms/m:6.4f} ms/crop")
        del x
        torch.cuda.empty_cache()
    return rows


def bench_preproc():
    print("\n=== [C] CPU preprocessing (letterbox resize) per frame — does NOT batch ===")
    import cv2
    cap = cv2.VideoCapture(str(SAMPLE))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        print("  (could not read sample frame)")
        return {}
    # warmup
    for _ in range(5):
        preproc(frame, INPUT_HW, mean=None, std=None)
    t0 = time.perf_counter()
    N = 50
    for _ in range(N):
        preproc(frame, INPUT_HW, mean=None, std=None)
    ms = (time.perf_counter() - t0) / N * 1000.0
    print(f"  preproc: {ms:.2f} ms/frame  (frame {frame.shape[1]}x{frame.shape[0]})")
    return {"preproc_ms": round(ms, 2), "frame_w": frame.shape[1], "frame_h": frame.shape[0]}


def bench_pipeline():
    print("\n=== [D] Full single-stream pipeline FPS on sample1.mp4 (real, this machine) ===")
    try:
        from src.inference_gpu import BoostTrackGPUInference
    except Exception as e:
        print(f"  import failed: {e}")
        return {}
    model = BoostTrackGPUInference()
    n, t0, warm = 0, None, 10
    try:
        for i, item in enumerate(model.stream(str(SAMPLE), draw=False)):
            if i == warm:
                torch.cuda.synchronize()
                t0 = time.perf_counter()
            if i >= warm:
                n += 1
            if i >= warm + 150:
                break
    except Exception as e:
        print(f"  stream error after {n} frames: {e}")
    torch.cuda.synchronize()
    if t0 and n:
        dt = time.perf_counter() - t0
        fps = n / dt
        print(f"  {n} frames in {dt:.2f}s -> {fps:.2f} FPS  ({1000/fps:.1f} ms/frame)")
        return {"frames": n, "fps": round(fps, 2), "ms_per_frame": round(1000/fps, 1)}
    return {}


def main():
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    results = {}
    results["gpu"] = torch.cuda.get_device_name(0)
    try:
        results["detection"] = bench_detection()
    except Exception as e:
        print("detection bench failed:", e)
    try:
        results["reid"] = bench_reid()
    except Exception as e:
        print("reid bench failed:", e)
    try:
        results["preproc"] = bench_preproc()
    except Exception as e:
        print("preproc bench failed:", e)
    try:
        results["pipeline"] = bench_pipeline()
    except Exception as e:
        print("pipeline bench failed:", e)

    out = Path(__file__).resolve().parent / "results.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
