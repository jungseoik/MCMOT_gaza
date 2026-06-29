#!/usr/bin/env python3
"""
extract_sprites.py — 레포 자체 검출기(YOLOX MOT20)+트래커로 샘플 영상에서
실제 보행자 크롭을 뽑아 grabcut 매팅 → RGBA 사람 스프라이트 풀을 만든다.

왜 이렇게 하나:
  - 합성 테스트 영상이 "검출기에 실제로 잡혀야" 파이프라인 전체(검출→추적→호모그래피
    →속도→밀도→이벤트) 테스트가 성립한다.
  - 인터넷이 막혀 외부 사람 이미지를 받을 수 없으므로, 같은 검출기가 이미 잘 잡는
    기존 샘플 영상의 실제 사람을 오려 스프라이트로 재활용한다(완전 오프라인).

출력: cad/synth/assets_sprites/person_XXX.png (RGBA, 발끝이 이미지 하단)
      cad/synth/assets_sprites/index.json
"""
import argparse, json, os, sys
from pathlib import Path

import cv2
import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[2]   # repo root
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dataset import preproc
from default_settings import GeneralSettings
from external.adaptors.detector import Detector
from tracker.boost_track import BoostTrack, KalmanBoxTracker


def grabcut_rgba(crop_bgr):
    """타이트한 사람 박스 크롭에 grabcut → RGBA(배경 투명). 실패 시 None."""
    h, w = crop_bgr.shape[:2]
    if h < 40 or w < 16:
        return None
    mask = np.zeros((h, w), np.uint8)
    # 박스 안쪽을 전경 후보로
    mx, my = int(w * 0.08), int(h * 0.04)
    rect = (mx, my, w - 2 * mx, h - 2 * my)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(crop_bgr, mask, rect, bgd, fgd, 4, cv2.GC_INIT_WITH_RECT)
    except Exception:
        return None
    fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    # 가장 큰 연결요소만 유지(노이즈 제거)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(fg, 8)
    if n <= 1:
        return None
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    fg = np.where(lab == biggest, 255, 0).astype(np.uint8)
    if fg.sum() / 255 < 0.12 * h * w:   # 전경 너무 작으면 실패
        return None
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    fg = cv2.GaussianBlur(fg, (3, 3), 0)
    rgba = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2BGRA)
    rgba[:, :, 3] = fg
    return rgba


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=str(ROOT / "assets" / "sample1.mp4"))
    ap.add_argument("--weights", default=str(ROOT / "external" / "weights" / "bytetrack_x_mot20.tar"))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "assets_sprites"))
    ap.add_argument("--max_frames", type=int, default=200)
    ap.add_argument("--max_sprites", type=int, default=24)
    ap.add_argument("--det_thresh", type=float, default=0.6)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    GeneralSettings.values['dataset'] = 'mot20'
    GeneralSettings.values['test_dataset'] = True
    GeneralSettings.values['use_embedding'] = False
    GeneralSettings.values['use_ecc'] = False
    GeneralSettings.values['det_thresh'] = args.det_thresh

    det = Detector(model_type="yolox", path=args.weights, dataset="mot20")
    det.initialize_model()
    KalmanBoxTracker.count = 0
    tracker = BoostTrack()
    input_size = (896, 1600)

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        sys.exit(f"영상 못 엶: {args.video}")

    # track_id -> 가장 좋은(면적 큰, 사람 비율) 크롭
    best = {}
    fi = 0
    while fi < args.max_frames:
        ret, frame = cap.read()
        if not ret:
            break
        fi += 1
        padded, r = preproc(frame, input_size, mean=None, std=None)
        tensor = torch.from_numpy(padded).unsqueeze(0).cuda()
        with torch.no_grad():
            pred = det.detect(tensor)
        targets = tracker.update(pred, tensor, frame, f"sprite:{fi}")
        if targets is None or len(targets) == 0:
            continue
        H, W = frame.shape[:2]
        for t in targets:
            x1, y1, x2, y2 = [int(v) for v in t[:4]]
            tid = int(t[4])
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(W, x2), min(H, y2)
            bw, bh = x2 - x1, y2 - y1
            if bw <= 0 or bh <= 0:
                continue
            ar = bh / bw
            if ar < 1.6 or ar > 4.5:     # 서있는 사람 비율만
                continue
            if bh < 90:                  # 너무 작으면 매팅 품질↓
                continue
            area = bw * bh
            if tid not in best or area > best[tid][0]:
                best[tid] = (area, frame[y1:y2, x1:x2].copy())
    cap.release()
    det.cache.clear()

    # 면적 큰 순으로 매팅
    items = sorted(best.items(), key=lambda kv: -kv[1][0])
    saved, index = 0, []
    for tid, (area, crop) in items:
        if saved >= args.max_sprites:
            break
        rgba = grabcut_rgba(crop)
        if rgba is None:
            continue
        name = f"person_{saved:03d}.png"
        cv2.imwrite(os.path.join(args.out, name), rgba)
        index.append({"file": name, "src_track": tid, "h": rgba.shape[0], "w": rgba.shape[1]})
        saved += 1

    with open(os.path.join(args.out, "index.json"), "w") as f:
        json.dump(index, f, indent=2)
    print(f"[sprites] {saved}장 저장 → {args.out}  (검출 track {len(best)}개 중)")


if __name__ == "__main__":
    main()
