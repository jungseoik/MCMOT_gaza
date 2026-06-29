#!/usr/bin/env python3
"""
raster.py — numpy z-buffer 삼각형 래스터라이저(작고 의존성 없음).
추천2의 '진짜 가림(occlusion)'을 위해 정적 씬(바닥/벽)을 깊이버퍼로 굽는다.
바리센트릭 보간으로 픽셀별 카메라공간 깊이를 채운다.
"""
import numpy as np


def fill_tri(color_buf, zbuf, pts, zs, color):
    """삼각형 하나를 color_buf/zbuf에 그린다(가까운 깊이만 갱신).
    pts:(3,2) 픽셀, zs:(3,) 카메라공간 깊이(mm, 작을수록 가까움), color:(3,) BGR."""
    H, W = zbuf.shape
    p = np.asarray(pts, float)
    if not np.isfinite(p).all() or not np.isfinite(zs).all():
        return
    x0 = max(int(np.floor(p[:, 0].min())), 0)
    x1 = min(int(np.ceil(p[:, 0].max())), W - 1)
    y0 = max(int(np.floor(p[:, 1].min())), 0)
    y1 = min(int(np.ceil(p[:, 1].max())), H - 1)
    if x1 < x0 or y1 < y0:
        return
    (ax, ay), (bx, by), (cx, cy) = p
    denom = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(denom) < 1e-6:
        return
    ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
    xs = xs.astype(float); ys = ys.astype(float)
    l1 = ((by - cy) * (xs - cx) + (cx - bx) * (ys - cy)) / denom
    l2 = ((cy - ay) * (xs - cx) + (ax - cx) * (ys - cy)) / denom
    l3 = 1 - l1 - l2
    inside = (l1 >= -1e-4) & (l2 >= -1e-4) & (l3 >= -1e-4)
    if not inside.any():
        return
    z = l1 * zs[0] + l2 * zs[1] + l3 * zs[2]
    sub_z = zbuf[y0:y1 + 1, x0:x1 + 1]
    closer = inside & (z < sub_z)
    sub_z[closer] = z[closer]
    sub_c = color_buf[y0:y1 + 1, x0:x1 + 1]
    sub_c[closer] = color


def fill_quad(color_buf, zbuf, quad_px, quad_z, color):
    """4점 사각형 = 삼각형 2개."""
    fill_tri(color_buf, zbuf, quad_px[[0, 1, 2]], quad_z[[0, 1, 2]], color)
    fill_tri(color_buf, zbuf, quad_px[[0, 2, 3]], quad_z[[0, 2, 3]], color)
