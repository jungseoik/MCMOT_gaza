"""Multi-channel throughput for YOLO26 + ByteTrack/BoT-SORT (single GPU).
Mirror of bench/bench_multiproc.py but for the Ultralytics stack, so the result
is directly comparable to the YOLOX-X+BoostTrack numbers.

Each process = 1 channel running model.track on sample1 (person-only, imgsz=640).
Reports per-channel + aggregate fps at several concurrency levels.

Run: python docs/reports/yolo_compare/bench_yolo_multiproc.py   (GPU via BENCH_GPU, default 1)
"""
import os
import sys
import time
import json
import multiprocessing as mp
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SAMPLE = str(ROOT / "assets/sample1.mp4")
W = HERE / "weights"
GPU = os.environ.get("BENCH_GPU", "1")
# Fair-comparison defaults (match YOLOX-X ~57 people/frame). Override via env.
IMGSZ = int(os.environ.get("IMGSZ", "1600"))
CONF = float(os.environ.get("CONF", "0.1"))
MAXDET = int(os.environ.get("MAXDET", "1000"))
TAG = os.environ.get("TAG", f"sz{IMGSZ}_c{CONF}")
WARMUP = 10
COMBOS = [("yolo26m", "bytetrack.yaml"), ("yolo26m", "botsort.yaml"),
          ("yolo26l", "bytetrack.yaml"), ("yolo26l", "botsort.yaml")]
PROC_LEVELS = [1, 4, 8]


def worker(model_file, tracker, ret):
    os.environ["CUDA_VISIBLE_DEVICES"] = GPU
    from ultralytics import YOLO
    model = YOLO(str(W / model_file))
    n, t0 = 0, None
    for _ in model.track(source=SAMPLE, tracker=tracker, classes=[0], imgsz=IMGSZ,
                         conf=CONF, max_det=MAXDET,
                         stream=True, device=0, verbose=False):
        if n == WARMUP:
            t0 = time.perf_counter()
        n += 1
    dt = time.perf_counter() - t0
    ret.put((n - WARMUP) / dt)


def run_level(model_file, tracker, P):
    ctx = mp.get_context("spawn")
    ret = ctx.Queue()
    procs = [ctx.Process(target=worker, args=(model_file, tracker, ret)) for _ in range(P)]
    for p in procs: p.start()
    fps = [ret.get() for _ in range(P)]
    for p in procs: p.join()
    fps.sort()
    return {"procs": P, "per_ch_fps_mean": round(sum(fps)/len(fps), 2),
            "per_ch_fps_min": round(fps[0], 2), "aggregate_fps": round(sum(fps), 1)}


def main():
    print(f"YOLO26 multi-channel — GPU={GPU}, imgsz={IMGSZ} conf={CONF} max_det={MAXDET}\n")
    results = []
    for mdl, trk in COMBOS:
        name = f"{mdl}_{trk.split('.')[0]}"
        entry = {"combo": name, "levels": []}
        print(f"=== {name} ===")
        for P in PROC_LEVELS:
            r = run_level(f"{mdl}.pt", trk, P)
            entry["levels"].append(r)
            print(f"  {P:2d}ch | per-ch {r['per_ch_fps_mean']:6.2f} fps | "
                  f"aggregate {r['aggregate_fps']:7.1f} fps")
        results.append(entry)
    (HERE / f"results_yolo_multiproc_{TAG}.json").write_text(json.dumps(results, indent=2))
    print(f"\nSaved results_yolo_multiproc_{TAG}.json")


if __name__ == "__main__":
    main()
