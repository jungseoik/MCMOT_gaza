"""Detection-resolution scaling experiment.

For each YOLOX input resolution, measure (on ONE GPU, to isolate the resolution
effect and avoid the concurrent VRAM user on the other GPU):
  - single-stream fps (full pipeline: detect+track+ReID)
  - parallel N-channel aggregate fps on the SAME single GPU (per-GPU ceiling)

→ shows how much throughput a lower detection resolution buys, hence how the
required GPU count for 150ch changes. (Accuracy trade-off is NOT measured here.)

GPU is chosen by env BENCH_GPU (default "1", the free GPU on this host).
Run: BENCH_GPU=1 python docs/reports/bench/bench_resolution.py
"""
import os
import sys
import time
import json
import multiprocessing as mp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GPU = os.environ.get("BENCH_GPU", "1")

# (H, W, engine filename). Baseline reuses the existing fixed-batch engine.
RES = [
    (896, 1600, "yolox_mot20_fp16.engine"),
    (640, 1088, "yolox_mot20_fp16_640x1088.engine"),
    (576, 1024, "yolox_mot20_fp16_576x1024.engine"),
]
PROC_LEVELS = [1, 4, 8]      # processes on the single GPU
WARMUP = 10
MEASURE = 120


def worker(h, w, engine, ret):
    os.environ["CUDA_VISIBLE_DEVICES"] = GPU
    sys.path.insert(0, str(ROOT))
    import torch
    from src.inference_gpu import BoostTrackGPUInference
    sample = str(ROOT / "assets/sample1.mp4")
    eng = str(ROOT / "external/weights/trt" / engine)

    model = BoostTrackGPUInference(yolox_engine=eng, input_size=(h, w))
    n, t0, done = 0, None, False
    while not done:
        for item in model.stream(sample, draw=False):
            if n == WARMUP:
                torch.cuda.synchronize(); t0 = time.perf_counter()
            n += 1
            if n >= WARMUP + MEASURE:
                done = True; break
    torch.cuda.synchronize()
    ret.put(MEASURE / (time.perf_counter() - t0))


def run_level(h, w, engine, P):
    ctx = mp.get_context("spawn")
    ret = ctx.Queue()
    procs = [ctx.Process(target=worker, args=(h, w, engine, ret)) for _ in range(P)]
    for p in procs: p.start()
    fps = [ret.get() for _ in range(P)]
    for p in procs: p.join()
    fps.sort()
    return {"procs": P, "per_ch_fps_mean": round(sum(fps)/len(fps), 2),
            "per_ch_fps_min": round(fps[0], 2),
            "aggregate_fps": round(sum(fps), 1)}


def main():
    print(f"Resolution scaling — single GPU (CUDA_VISIBLE_DEVICES={GPU})\n")
    results = []
    for h, w, engine in RES:
        if not (ROOT / "external/weights/trt" / engine).exists():
            print(f"[MISSING engine] {engine} — run build_resolution_engines.py first")
            continue
        mp_per = w * h / 1e6
        entry = {"h": h, "w": w, "megapixels": round(mp_per, 3),
                 "engine": engine, "levels": []}
        print(f"=== {h}x{w} ({mp_per:.2f} Mpx) ===")
        for P in PROC_LEVELS:
            r = run_level(h, w, engine, P)
            entry["levels"].append(r)
            print(f"  {P:2d}ch | per-ch {r['per_ch_fps_mean']:5.2f} fps | "
                  f"aggregate {r['aggregate_fps']:6.1f} fps")
        results.append(entry)
    out = Path(__file__).resolve().parent / "results_resolution.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
