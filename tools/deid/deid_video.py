#!/usr/bin/env python3
"""영상 비식별화 — 사람 머리 영역을 모자이크+블러로 가린다(전 구간, 프레임 수 보존).

발표 영상의 카메라 그리드처럼 **원본 대신 틀어도 되는 사본**을 만든다. 원본은 건드리지
않는다. 프레임 수·fps 를 원본과 똑같이 맞추므로(디코드한 프레임을 하나도 빼지 않고 그대로
인코드) 시간 동기가 필요한 합성(tools/demo_viz.py --clip-root)에 그대로 쓸 수 있다.

가리는 영역 — "웬만하면 다 가린다"가 목표라 세 검출을 **합집합**으로 쓴다:
  1. 머리 검출(주)   real_head_v0_l_yv11 (PIA, YOLO11-L, class head) — 머리 박스를 사방 30% 키움
  2. 사람 검출(보조)  yolo26m person — 박스 위쪽 32% 띠 (머리 검출이 놓친 먼/가려진 사람)
  3. 자세 추정(보조)  yolo11x-pose — 코·눈·귀 키포인트 둘레
  + 시간 확장: 앞뒤 ±HOLD 프레임에서 찾은 영역도 함께 가린다(한두 프레임 검출 누락 보완).
신뢰도 하한은 표본 처리(06_비식별화_폐기증빙, 사람 0.2·자세 0.2/키포인트 0.3)보다 낮췄다
— 오탐(사람 아닌 곳 블러)은 감수하고 놓침을 줄인다.

사용:
  python tools/deid/deid_video.py IN.mp4 [IN2.mp4 ...] --out-root OUT --in-root ROOT [--gpu 0]
    → OUT/<IN 의 ROOT 기준 상대경로>  (예: ROOT/scenario_16F/cam10.mp4 → OUT/scenario_16F/cam10.mp4)
  --preview N : 처리 대신 N 초 간격 표본 프레임에 가린 영역을 그려 OUT/_preview/ 에 저장
모델 경로는 아래 상수(레포·캐시, git 미포함). 사용법·기록은 tools/deid/README.md.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
HEAD_W = Path.home() / "seoik/.cache/pia_models/real_head_v0_l_yv11/real_head_v0_l_yv11.pt"
if not HEAD_W.exists():
    HEAD_W = Path("/home/pia/seoik/.cache/pia_models/real_head_v0_l_yv11/real_head_v0_l_yv11.pt")
DET_W = ROOT / "docs/reports/yolo_compare/weights/yolo26m.pt"
POSE_W = ROOT / "external/reference/reid/_weights/pose/yolo11x-pose.pt"

IMGSZ = 1280
CONF_HEAD = 0.10
CONF_DET = 0.12
CONF_POSE = 0.12
KP_CONF = 0.20
HEAD_PAD = 0.30      # 머리 박스 사방 확장 비율
TOP_BAND = 0.32      # 사람 박스 위쪽 몇 % 를 머리로 보나
HOLD = 3             # 앞뒤 ±프레임 영역 유지
BATCH = 16


def load_models(gpu: int):
    from ultralytics import YOLO
    dev = f"cuda:{gpu}"
    return YOLO(str(HEAD_W)), YOLO(str(DET_W)), YOLO(str(POSE_W)), dev


def regions(models, frames):
    """프레임 묶음 → 프레임별 가릴 사각형 리스트 [(x1,y1,x2,y2,src)]."""
    head, det, pose, dev = models
    kw = dict(imgsz=IMGSZ, verbose=False, device=dev)
    out = [[] for _ in frames]
    for i, r in enumerate(head(frames, conf=CONF_HEAD, **kw)):
        for x1, y1, x2, y2 in r.boxes.xyxy.cpu().numpy():
            w, h = x2 - x1, y2 - y1
            out[i].append((x1 - HEAD_PAD * w, y1 - HEAD_PAD * h, x2 + HEAD_PAD * w, y2 + HEAD_PAD * h, "h"))
    for i, r in enumerate(det(frames, classes=[0], conf=CONF_DET, **kw)):
        for x1, y1, x2, y2 in r.boxes.xyxy.cpu().numpy():
            w, h = x2 - x1, y2 - y1
            out[i].append((x1 - 0.1 * w, y1 - 0.06 * h, x2 + 0.1 * w, y1 + TOP_BAND * h, "p"))
    for i, r in enumerate(pose(frames, conf=CONF_POSE, **kw)):
        if r.keypoints is None or not len(r.keypoints):
            continue
        for kp, cf, bx in zip(r.keypoints.xy.cpu().numpy(), r.keypoints.conf.cpu().numpy(),
                              r.boxes.xyxy.cpu().numpy()):
            pts = kp[:5][cf[:5] > KP_CONF]
            if len(pts):
                x1, y1 = pts.min(0); x2, y2 = pts.max(0)
                s = max(x2 - x1, y2 - y1, (bx[2] - bx[0]) * 0.40)
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                out[i].append((cx - s, cy - s * 1.2, cx + s, cy + s, "k"))
    return out


def blur(f, rs):
    H, W = f.shape[:2]
    for x1, y1, x2, y2, *_ in rs:
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(W, int(np.ceil(x2))), min(H, int(np.ceil(y2)))
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        roi = f[y1:y2, x1:x2]
        k = max(3, ((x2 - x1) // 6) | 1)
        small = cv2.resize(roi, (max(2, min(6, (x2 - x1) // 6)), max(2, min(6, (y2 - y1) // 6))),
                           interpolation=cv2.INTER_AREA)
        f[y1:y2, x1:x2] = cv2.GaussianBlur(
            cv2.resize(small, (x2 - x1, y2 - y1), interpolation=cv2.INTER_NEAREST), (k, k), 0)
    return f


def process(models, src: Path, dst: Path) -> dict:
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W, H = int(cap.get(3)), int(cap.get(4))
    n_src = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix(".part.mp4")
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
         "-r", f"{fps:g}", "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-r", f"{fps:g}", str(part)], stdin=subprocess.PIPE)
    # 앞뒤 HOLD 프레임 영역을 합치려면 출력이 HOLD 만큼 늦게 나간다.
    pend = collections.deque()            # (frame, regions)
    hist = collections.deque(maxlen=2 * HOLD + 1)
    n = nreg = 0
    src_cnt = collections.Counter()
    t0 = time.time()

    def emit(final=False):
        nonlocal n
        # pend[0] 의 영역 = 자기 + 앞 HOLD(hist) + 뒤 HOLD(pend[1..HOLD])
        while pend and (final or len(pend) > HOLD):
            f, rs = pend.popleft()
            hist.append(rs)
            allr = [r for h in hist for r in h] + [r for _, rr in list(pend)[:HOLD] for r in rr]
            enc.stdin.write(blur(f, allr).tobytes())
            n += 1
            if len(hist) > HOLD + 1:
                hist.popleft()

    done = False
    while not done:
        buf = []
        for _ in range(BATCH):
            ok, f = cap.read()
            if not ok:
                done = True
                break
            buf.append(f)
        if not buf:
            break
        for f, rs in zip(buf, regions(models, buf)):
            pend.append((f, rs))
            nreg += len(rs)
            src_cnt.update(r[4] for r in rs)
        emit()
        if n and n % (BATCH * 120) < BATCH:
            el = time.time() - t0
            print(f"  {src.name} {n}/{n_src} · {n / el:.1f} fps", flush=True)
    emit(final=True)
    cap.release()
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError(f"ffmpeg 실패: {src}")
    part.replace(dst)
    chk = int(subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0",
                              "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(dst)],
                             capture_output=True, text=True).stdout.strip() or 0)
    return {"src": str(src), "dst": str(dst), "frames_src": n_src, "frames_written": n,
            "frames_out": chk, "fps": fps, "regions": nreg, "by_source": dict(src_cnt),
            "sec": round(time.time() - t0, 1)}


def preview(models, src: Path, out: Path, every: float):
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out.mkdir(parents=True, exist_ok=True)
    col = {"h": (0, 255, 0), "p": (0, 160, 255), "k": (255, 0, 255)}
    for t in np.arange(every / 2, n / fps, every):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, f = cap.read()
        if not ok:
            continue
        rs = regions(models, [f])[0]
        b = blur(f.copy(), rs)
        o = f.copy()
        for x1, y1, x2, y2, s in rs:
            cv2.rectangle(o, (int(x1), int(y1)), (int(x2), int(y2)), col[s], 2)
        cv2.imwrite(str(out / f"{src.parent.name}_{src.stem}_{int(t):04d}.jpg"), np.hstack([o, b]),
                    [cv2.IMWRITE_JPEG_QUALITY, 85])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("--in-root", type=Path, required=True)
    ap.add_argument("--out-root", type=Path, required=True)
    ap.add_argument("--gpu", type=int, default=0)
    ap.add_argument("--preview", type=float, default=0)
    ap.add_argument("--skip-existing", action="store_true")
    a = ap.parse_args()
    models = load_models(a.gpu)
    for src in a.inputs:
        rel = src.resolve().relative_to(a.in_root.resolve())
        if a.preview:
            preview(models, src, a.out_root / "_preview", a.preview)
            print("preview", rel, flush=True)
            continue
        dst = a.out_root / rel
        if a.skip_existing and dst.exists():
            print("skip", rel); continue
        info = process(models, src, dst)
        (dst.with_suffix(".deid.json")).write_text(json.dumps(
            dict(info, params=dict(imgsz=IMGSZ, conf_head=CONF_HEAD, conf_det=CONF_DET,
                                   conf_pose=CONF_POSE, kp_conf=KP_CONF, head_pad=HEAD_PAD,
                                   top_band=TOP_BAND, hold=HOLD,
                                   models=[HEAD_W.name, DET_W.name, POSE_W.name])),
            ensure_ascii=False, indent=2))
        ok = info["frames_out"] == info["frames_src"]
        print(f"done {rel} frames {info['frames_out']}/{info['frames_src']} {'OK' if ok else 'MISMATCH'} "
              f"regions {info['regions']} {info['by_source']} {info['sec']}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
