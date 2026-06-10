"""MPS (Multi-Process Service) effect on single-GPU multi-channel throughput.

Runs the full pipeline (detect+track+ReID) as N parallel processes on ONE GPU
and reports per-channel + aggregate fps. Run this TWICE — once without the MPS
daemon, once with it running — to measure how much MPS recovers the
context-switching loss seen at high channel counts.

GPU chosen by env BENCH_GPU (default "1"). Engine = baseline 896×1600.
Run: BENCH_GPU=1 python docs/reports/bench/bench_mps.py [tag]
  tag (optional) labels the output, e.g. "nompS" or "mps".
"""
import os
import sys
import time
import json
import multiprocessing as mp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GPU = os.environ.get("BENCH_GPU", "1")
TAG = sys.argv[1] if len(sys.argv) > 1 else "run"
ENGINE = "yolox_mot20_fp16.engine"          # baseline 896×1600
HW = (896, 1600)
PROC_LEVELS = [1, 4, 8, 12]
WARMUP = 10
MEASURE = 120


def worker(ret):
    os.environ["CUDA_VISIBLE_DEVICES"] = GPU
    sys.path.insert(0, str(ROOT))
    import torch
    from src.inference_gpu import BoostTrackGPUInference
    sample = str(ROOT / "assets/sample1.mp4")
    eng = str(ROOT / "external/weights/trt" / ENGINE)
    model = BoostTrackGPUInference(yolox_engine=eng, input_size=HW)
    n, t0, done = 0, None, False
    while not done:
        for _ in model.stream(sample, draw=False):
            if n == WARMUP:
                torch.cuda.synchronize(); t0 = time.perf_counter()
            n += 1
            if n >= WARMUP + MEASURE:
                done = True; break
    torch.cuda.synchronize()
    ret.put(MEASURE / (time.perf_counter() - t0))


def run_level(P):
    ctx = mp.get_context("spawn")
    ret = ctx.Queue()
    procs = [ctx.Process(target=worker, args=(ret,)) for _ in range(P)]
    for p in procs: p.start()
    fps = [ret.get() for _ in range(P)]
    for p in procs: p.join()
    fps.sort()
    return {"procs": P, "per_ch_fps_mean": round(sum(fps)/len(fps), 2),
            "per_ch_fps_min": round(fps[0], 2),
            "aggregate_fps": round(sum(fps), 1)}


def main():
    mps = os.environ.get("CUDA_MPS_PIPE_DIRECTORY", "")
    print(f"[{TAG}] single GPU={GPU}  MPS_pipe={mps or '(none)'}\n")
    results = []
    for P in PROC_LEVELS:
        r = run_level(P)
        results.append(r)
        print(f"  {P:2d}ch | per-ch {r['per_ch_fps_mean']:5.2f} fps | "
              f"aggregate {r['aggregate_fps']:6.1f} fps")
    out = Path(__file__).resolve().parent / f"results_mps_{TAG}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
