#!/usr/bin/env python3
"""
scene.py — DXF 도면을 합성 씬으로 변환.
  - load_segments: 선형 엔티티 → 2D 선분(mm) + 도면 범위.
  - wall_quads: 긴 선분을 벽 높이까지 압출한 3D 사각형(가림·3D렌더용).
  - render_background: 카메라 시점의 바닥+타일격자+벽 배경 이미지와
    정적 씬 깊이버퍼(zbuf)를 굽는다. zbuf는 추천2에서 사람 가림 판정에 재사용.
"""
import numpy as np
import cv2
import ezdxf

from raster import fill_quad


def load_segments(dxf_path):
    """DXF 선형 엔티티 → (segs(N,4) mm, ext=(xmin,ymin,xmax,ymax) mm)."""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    segs = []

    def add_poly(pts, closed=False):
        pts = [(p[0], p[1]) for p in pts]
        if closed and len(pts) >= 2:
            pts = pts + [pts[0]]
        for a, b in zip(pts[:-1], pts[1:]):
            segs.append((a[0], a[1], b[0], b[1]))

    for e in msp:
        t = e.dxftype()
        if t == "LWPOLYLINE":
            add_poly(list(e.get_points()), e.closed)
        elif t == "LINE":
            segs.append((e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y))
        elif t in ("ARC", "CIRCLE", "ELLIPSE", "SPLINE"):
            try:
                pts = list(e.flattening(distance=20))
                add_poly([(p.x, p.y) for p in pts], closed=(t == "CIRCLE"))
            except Exception:
                pass
    arr = np.array(segs, dtype=float)
    h = doc.header
    (xmin, ymin, _), (xmax, ymax, _) = h.get("$EXTMIN"), h.get("$EXTMAX")
    return arr, (xmin, ymin, xmax, ymax)


def wall_quads(segs, thresh_mm=2000.0, wall_h_mm=1200.0):
    """긴 선분 → 수직 벽 사각형 리스트. 각 원소 (4,3) mm: [A0,B0,B1,A1]."""
    p0 = segs[:, 0:2]; p1 = segs[:, 2:4]
    seglen = np.linalg.norm(p1 - p0, axis=1)
    idx = np.where(seglen >= thresh_mm)[0]
    quads = []
    for i in idx:
        a, b = p0[i], p1[i]
        quads.append(np.array([
            [a[0], a[1], 0.0],
            [b[0], b[1], 0.0],
            [b[0], b[1], wall_h_mm],
            [a[0], a[1], wall_h_mm],
        ]))
    return quads


def render_background(cam, segs, ext, wall_h_mm=1200.0, thresh_mm=2000.0,
                      grid_m=2.0):
    """카메라 시점 배경(BGR) + 정적 씬 깊이버퍼(float32, mm; sky=inf)."""
    W, H = cam.W, cam.H
    color = np.full((H, W, 3), (60, 55, 50), np.uint8)        # 천장/배경 어두운 톤
    zbuf = np.full((H, W), np.inf, np.float32)
    xmin, ymin, xmax, ymax = ext

    # 1) 바닥 평면(z=0) 사각형 → 카펫 색 + 깊이
    floor = np.array([[xmin, ymin, 0], [xmax, ymin, 0],
                      [xmax, ymax, 0], [xmin, ymax, 0]], float)
    fpx, fz = cam.project(floor)
    # 깊이는 카메라공간 zc(=project의 두번째 반환) 사용
    fill_quad(color, zbuf, fpx, fz, (120, 118, 112))          # 카펫 그레이

    # 2) 바닥 타일 격자선(z=0) — 원근 시각단서 + 호모그래피 대응점
    step = grid_m * 1000.0
    glines = []
    x = xmin
    while x <= xmax + 1:
        glines.append(((x, ymin, 0), (x, ymax, 0))); x += step
    y = ymin
    while y <= ymax + 1:
        glines.append(((xmin, y, 0), (xmax, y, 0))); y += step
    for a, b in glines:
        pa, _ = cam.project(np.array([a])); pb, _ = cam.project(np.array([b]))
        if np.isfinite(pa).all() and np.isfinite(pb).all():
            cv2.line(color, tuple(pa[0].astype(int)), tuple(pb[0].astype(int)),
                     (150, 148, 140), 1, cv2.LINE_AA)

    # 3) 벽(압출 사각형) — 깊이버퍼로 가림, 가까운 벽이 먼 벽을 덮음
    quads = wall_quads(segs, thresh_mm, wall_h_mm)
    # 먼 것부터(평균깊이 큰 것부터) 그려도 zbuf가 처리하지만 색 일관 위해 정렬
    order = []
    for q in quads:
        pq, zq = cam.project(q)
        if np.isfinite(pq).all() and (zq > 1).all():
            order.append((zq.mean(), pq, zq))
    order.sort(key=lambda t: -t[0])
    for _, pq, zq in order:
        fill_quad(color, zbuf, pq, zq, (150, 145, 138))
        # 윗변 강조선
        cv2.line(color, tuple(pq[3].astype(int)), tuple(pq[2].astype(int)),
                 (90, 88, 84), 1, cv2.LINE_AA)
    return color, zbuf
