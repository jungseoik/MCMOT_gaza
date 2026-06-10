"""REAL parallel multi-channel throughput: spawn P independent pipeline
processes (each = 1 channel) across the 2 GPUs and measure sustained per-channel
fps under contention. Answers "지금 15채널 동시 병렬 실시간 처리량이 얼마냐".

Each worker runs the FULL current pipeline (detect+track+ReID) on sample1,
looping the file, and reports its own sustained fps (frames / processing time)
after warmup — robust to staggered starts since all overlap during measurement.

Run: python docs/reports/bench/bench_multiproc.py
(do NOT set CUDA_VISIBLE_DEVICES globally; workers pick a GPU by rank)
"""
import sys
import time
import json
import multiprocessing as mp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

PROC_LEVELS = [1, 4, 8, 15]
NGPU = 2
WARMUP = 10
MEASURE = 120


def worker(rank, ngpu, ret):
    import os
    os.environ["CUDA_VISIBLE_DEVICES"] = str(rank % ngpu)
    sys.path.insert(0, str(ROOT))
    import torch
    from src.inference_gpu import BoostTrackGPUInference
    sample = str(ROOT / "assets/sample1.mp4")

    model = BoostTrackGPUInference()
    n = 0
    t0 = None
    done = False
    # loop the file until we've measured MEASURE frames after WARMUP
    while not done:
        for item in model.stream(sample, draw=False):
            if n == WARMUP:
                torch.cuda.synchronize()
                t0 = time.perf_counter()
            if n >= WARMUP:
                pass
            n += 1
            if n >= WARMUP + MEASURE:
                done = True
                break
    torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    ret.put((rank, MEASURE / dt, dt))


def run_level(P):
    ctx = mp.get_context("spawn")
    ret = ctx.Queue()
    procs = [ctx.Process(target=worker, args=(r, NGPU, ret)) for r in range(P)]
    t0 = time.perf_counter()
    for p in procs:
        p.start()
    fps = []
    for _ in range(P):
        rank, f, dt = ret.get()
        fps.append(f)
    for p in procs:
        p.join()
    wall = time.perf_counter() - t0
    fps.sort()
    mean = sum(fps) / len(fps)
    agg = sum(fps)
    return {"procs": P, "per_ch_fps_mean": round(mean, 2),
            "per_ch_fps_min": round(fps[0], 2),
            "per_ch_fps_max": round(fps[-1], 2),
            "aggregate_fps": round(agg, 1), "wall_s": round(wall, 1)}


def main():
    print(f"Parallel pipeline throughput across {NGPU} GPUs "
          f"(each process = 1 channel, full pipeline)\n")
    results = []
    for P in PROC_LEVELS:
        r = run_level(P)
        results.append(r)
        print(f"  {P:2d} ch | per-channel {r['per_ch_fps_mean']:5.2f} fps "
              f"(min {r['per_ch_fps_min']:.2f}, max {r['per_ch_fps_max']:.2f}) | "
              f"aggregate {r['aggregate_fps']:6.1f} fps")
    out = Path(__file__).resolve().parent / "results_multiproc.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
