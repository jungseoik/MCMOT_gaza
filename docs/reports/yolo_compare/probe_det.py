"""Check whether YOLO26 detections were capped (max_det) or thresholded (conf) /
under-resolved (imgsz) on sample1 — i.e. is the comparison fair?

Counts people/frame for several settings and compares to the YOLOX-X detector
used by the current pipeline.
Run: CUDA_VISIBLE_DEVICES=1 python docs/reports/yolo_compare/probe_det.py
"""
import os
import sys
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "1")
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SAMPLE = str(ROOT / "assets/sample1.mp4")
W = HERE / "weights"


def yolo_counts(model_file, conf, max_det, imgsz):
    from ultralytics import YOLO
    m = YOLO(str(W / model_file))
    counts = []
    for r in m.predict(source=SAMPLE, classes=[0], conf=conf, max_det=max_det,
                       imgsz=imgsz, stream=True, device=0, verbose=False):
        counts.append(len(r.boxes))
    c = np.array(counts)
    hit = int((c >= max_det).sum())
    return c, hit


def yolox_counts():
    """Per-frame detection count from the current YOLOX-X TRT detector."""
    sys.path.insert(0, str(ROOT))
    import cv2, torch
    from dataset import preproc
    from src.inference_trt import TRTDetector
    det = TRTDetector(str(ROOT / "external/weights/trt/yolox_mot20_fp16.engine"))
    cap = cv2.VideoCapture(SAMPLE)
    counts = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        padded, _ = preproc(fr, (896, 1600), mean=None, std=None)
        t = torch.from_numpy(padded).unsqueeze(0).cuda()
        pred = det.detect(t)
        # apply the pipeline det_thresh=0.4 for a fair "what the tracker sees" count
        if pred is None:
            counts.append(0)
        else:
            counts.append(int((pred[:, 4] >= 0.4).sum().item()))
    cap.release()
    return np.array(counts)


def stat(name, c, extra=""):
    print(f"  {name:38s} min {c.min():3d} | mean {c.mean():6.1f} | "
          f"max {c.max():3d}  {extra}")


def main():
    print("사람/프레임 검출 수 (sample1, 646프레임)\n")
    print("[YOLO26m] 설정별:")
    for conf, md, sz in [(0.25, 300, 640),       # 영상에 쓴 기본값
                         (0.10, 1000, 640),
                         (0.10, 1000, 1280),
                         (0.10, 1000, 1600)]:
        c, hit = yolo_counts("yolo26m.pt", conf, md, sz)
        stat(f"conf={conf} max_det={md} imgsz={sz}", c,
             f"| max_det 도달 프레임 {hit}")
    print("\n[현재 YOLOX-X @896x1600, det_thresh=0.4]:")
    cx = yolox_counts()
    stat("YOLOX-X (tracker가 보는 수)", cx)


if __name__ == "__main__":
    main()
