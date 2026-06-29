#!/usr/bin/env python3
"""
compositor.py — 배경 위에 사람 스프라이트를 원근에 맞게 합성한다.
  - 발끝(z=0)과 머리(z=1.7m)를 투영해 픽셀 키를 정확히 계산 → 원근 스케일.
  - painter's order(먼→가까운)로 겹침 처리(추천1).
  - scene_zbuf를 주면 벽 뒤 픽셀을 가린다(추천2의 진짜 occlusion).
반환: 합성 프레임 + 에이전트별 픽셀 GT(발끝, bbox, 가시성).
"""
import glob, os
import numpy as np
import cv2

PERSON_H_MM = 1700.0


def load_sprites(sprite_dir):
    sprites = []
    for f in sorted(glob.glob(os.path.join(sprite_dir, "person_*.png"))):
        im = cv2.imread(f, cv2.IMREAD_UNCHANGED)
        if im is not None and im.shape[2] == 4:
            sprites.append(im)
    return sprites


def assign_sprites(n_agents, sprites, seed=0):
    """에이전트별 스프라이트 고정 배정(키 큰=세로 긴 것 우선)."""
    order = sorted(range(len(sprites)), key=lambda k: -sprites[k].shape[0])
    rng = np.random.default_rng(seed)
    pick = order[:max(1, min(len(order), n_agents + 4))]
    return [pick[int(rng.integers(0, len(pick)))] for _ in range(n_agents)]


def _paste(frame, sprite, foot_xy, target_h, depth_mm, scene_zbuf):
    """스프라이트를 발끝 기준으로 합성. 반환 bbox(x,y,w,h) 또는 None(비가시)."""
    H, W = frame.shape[:2]
    sh, sw = sprite.shape[:2]
    th = int(round(target_h))
    if th < 12 or th > 4 * H:        # 너무 작거나(원거리) 비정상적으로 큼(카메라 코앞)
        return None
    tw = max(4, int(round(sw * th / sh)))
    rs = cv2.resize(sprite, (tw, th), interpolation=cv2.INTER_AREA)
    fx, fy = int(round(foot_xy[0])), int(round(foot_xy[1]))
    x0 = fx - tw // 2
    y0 = fy - th
    # 화면 교차영역
    xa, ya = max(0, x0), max(0, y0)
    xb, yb = min(W, x0 + tw), min(H, y0 + th)
    if xa >= xb or ya >= yb:
        return None
    sx0, sy0 = xa - x0, ya - y0
    patch = rs[sy0:sy0 + (yb - ya), sx0:sx0 + (xb - xa)]
    alpha = (patch[:, :, 3:4].astype(float) / 255.0)
    # 벽 가림: 사람 깊이가 씬 깊이보다 멀면(벽이 앞) 해당 픽셀 제거
    if scene_zbuf is not None:
        occ = (scene_zbuf[ya:yb, xa:xb] < depth_mm)   # 벽이 사람보다 앞
        alpha[occ] = 0.0
    if alpha.sum() < 8:        # 거의 다 가림 → 비가시
        return None
    roi = frame[ya:yb, xa:xb].astype(float)
    frame[ya:yb, xa:xb] = (patch[:, :, :3] * alpha + roi * (1 - alpha)).astype(np.uint8)
    # 가시 픽셀의 타이트 bbox
    ys, xs = np.where(alpha[:, :, 0] > 0.4)
    if len(xs) == 0:
        return None
    return (xa + xs.min(), ya + ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1)


def composite_frame(bg, cam, pos_m, present, sprites, assign, scene_zbuf=None):
    """bg 복사본에 합성. pos_m(N,2) m, present(N) bool. 반환 (frame, dets:list)."""
    frame = bg.copy()
    N = len(pos_m)
    xmin, ymin = cam.ext[0], cam.ext[1]
    items = []
    for i in range(N):
        if not present[i] or np.isnan(pos_m[i, 0]):
            continue
        Xmm = xmin + pos_m[i, 0] * 1000.0
        Ymm = ymin + pos_m[i, 1] * 1000.0
        foot = np.array([[Xmm, Ymm, 0.0]])
        head = np.array([[Xmm, Ymm, PERSON_H_MM]])
        pf, zf = cam.project(foot)
        ph, _ = cam.project(head)
        if not np.isfinite(pf).all() or not np.isfinite(ph).all():
            continue
        if zf[0] < 800:                       # 카메라 코앞(<0.8m) → 스킵
            continue
        target_h = abs(pf[0, 1] - ph[0, 1])
        if not np.isfinite(target_h) or target_h > 3 * cam.H:
            continue
        items.append((float(zf[0]), i, pf[0], target_h))
    # painter's: 먼 것부터
    items.sort(key=lambda t: -t[0])
    dets = []
    for depth, i, foot_px, th in items:
        bbox = _paste(frame, sprites[assign[i]], foot_px, th, depth, scene_zbuf)
        visible = bbox is not None
        dets.append({
            "id": i, "visible": visible,
            "foot_px": [round(float(foot_px[0]), 1), round(float(foot_px[1]), 1)],
            "bbox_xywh": [int(v) for v in bbox] if bbox else None,
            "depth_mm": round(depth, 1),
        })
    return frame, dets
