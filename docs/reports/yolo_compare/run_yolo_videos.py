"""Run YOLO26-m / -l with ByteTrack / BoT-SORT on sample1.mp4 → 4 annotated videos.
Person-only (COCO class 0). GPU1. Also reports single-stream fps per combo.

Run: CUDA_VISIBLE_DEVICES=1 python docs/reports/yolo_compare/run_yolo_videos.py
"""
import os
import sys
import time
import json
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "1")
from ultralytics import YOLO

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SAMPLE = str(ROOT / "assets/sample1.mp4")
W = HERE / "weights"
# Fair-comparison defaults: match the YOLOX-X(896×1600, det 0.4 → ~57 people/frame).
# Override via env. imgsz=640/conf=0.25 (ultralytics default) under-detects (~11/frame).
IMGSZ = int(os.environ.get("IMGSZ", "1600"))
CONF = float(os.environ.get("CONF", "0.1"))
MAXDET = int(os.environ.get("MAXDET", "1000"))
TAG = os.environ.get("TAG", f"sz{IMGSZ}_c{CONF}")

COMBOS = [("yolo26m", "bytetrack.yaml"), ("yolo26m", "botsort.yaml"),
          ("yolo26l", "bytetrack.yaml"), ("yolo26l", "botsort.yaml")]


def main():
    print(f"settings: imgsz={IMGSZ} conf={CONF} max_det={MAXDET} tag={TAG}")
    results = []
    for mdl, trk in COMBOS:
        name = f"{mdl}_{trk.split('.')[0]}_{TAG}"
        model = YOLO(str(W / f"{mdl}.pt"))
        n, t0 = 0, time.perf_counter()
        for _ in model.track(source=SAMPLE, tracker=trk, classes=[0],
                             imgsz=IMGSZ, conf=CONF, max_det=MAXDET,
                             stream=True, device=0, verbose=False,
                             save=True, project=str(HERE / "runs"),
                             name=name, exist_ok=True):
            n += 1
        dt = time.perf_counter() - t0
        fps = n / dt
        results.append({"combo": name, "model": mdl, "tracker": trk,
                        "imgsz": IMGSZ, "conf": CONF, "max_det": MAXDET,
                        "frames": n, "fps": round(fps, 2)})
        print(f"{name:32s} {n} frames  {fps:6.2f} fps")
    (HERE / f"videos_fps_{TAG}.json").write_text(json.dumps(results, indent=2))
    print(f"Saved videos_fps_{TAG}.json")


if __name__ == "__main__":
    main()
