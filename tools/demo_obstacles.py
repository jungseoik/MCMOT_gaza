# -*- coding: utf-8 -*-
"""발표 시각화용 **장애물 표시** — 읽기·그리기.

tools/obstacle_editor.py(:8920)가 저장한 `results/demo/_annotate/obstacles_<층>.json`
을 읽는다. 쓰는 곳은 발표용 시각화(tools/demo_viz.py) 한 군데뿐이다.

무엇에 영향을 주는지 분명히 해 둔다:
  · 영상 좌측 도면에 **그려진다**
  · 재실자별 최적 경로 산출에서 **벽으로 취급된다**(의자를 통과하는 경로는 거짓말이다)
  · 4대 지표 값에는 **들어가지 않는다** — 지표는 사람이 그린 공통 경로 기준으로
    이미 산출돼 있고, 여기 찍은 도형은 그 계산 어디에도 주입되지 않는다
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ANN = ROOT / "results/demo/_annotate"

# CAD 편집기(evac/web.py OBSTACLE_TAGS)와 같은 3종·같은 색. cv2 라 **BGR**.
TAGS = {
    "chair": {"name": "의자·집기", "bgr": (9, 83, 180)},
    "box":   {"name": "박스·적치물", "bgr": (237, 58, 124)},
    "nogo":  {"name": "통행금지", "bgr": (38, 38, 220)},
}


def path(floor_id: str) -> Path:
    return ANN / f"obstacles_{floor_id}.json"


def load(floor_id: str) -> list[dict]:
    """[{tag, points:[[x,y]..]}] — 맵 원본 px. 없으면 빈 목록."""
    p = path(floor_id)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for o in data.get("obstacles") or []:
        pts = [[float(q[0]), float(q[1])] for q in (o.get("points") or [])]
        if len(pts) >= 3:
            out.append({"tag": o.get("tag") or "nogo", "points": pts})
    return out


def signature(obstacles: list[dict]) -> str:
    """장애물이 바뀌면 산출해 둔 경로도 못 믿는다 — 사이드카에 같이 박아 둘 지문."""
    blob = json.dumps(obstacles, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def _hatch(bgr, size=10):
    """대각 해치 타일 — 색만으로는 도면 위에서 구별이 안 된다."""
    t = np.zeros((size, size, 3), np.uint8)
    cv2.line(t, (-1, size), (size, -1), bgr, 2, cv2.LINE_AA)
    return t


def draw(img, obstacles: list[dict], MP):
    """맵 이미지에 장애물을 겹쳐 그린다. MP 는 (맵px)->(화면px) 변환."""
    for o in obstacles:
        bgr = TAGS.get(o["tag"], TAGS["nogo"])["bgr"]
        poly = np.array([MP(*p) for p in o["points"]], np.int32)
        x, y, w, h = cv2.boundingRect(poly)
        if w <= 0 or h <= 0:
            continue
        x0, y0 = max(0, x), max(0, y)
        x1 = min(img.shape[1], x + w)
        y1 = min(img.shape[0], y + h)
        if x1 <= x0 or y1 <= y0:
            continue
        tile = _hatch(bgr)
        fill = np.tile(tile, (y1 // tile.shape[0] + 2, x1 // tile.shape[1] + 2, 1))
        mask = np.zeros(img.shape[:2], np.uint8)
        cv2.fillPoly(mask, [poly], 255)
        roi, m = img[y0:y1, x0:x1], mask[y0:y1, x0:x1]
        pat = fill[y0:y1, x0:x1]
        # 해치 선이 있는 픽셀만 칠한다 — 도면이 비치게
        sel = (m > 0) & (pat.max(axis=2) > 40)
        roi[sel] = pat[sel]
        # 바탕을 살짝 눌러 영역이 보이게
        light = (m > 0) & ~sel
        roi[light] = (roi[light] * 0.78 + np.array(bgr) * 0.22).astype(np.uint8)
        cv2.polylines(img, [poly], True, bgr, 2, cv2.LINE_AA)
    return img


def legend(obstacles: list[dict]) -> list[tuple[str, tuple]]:
    """영상 범례용 — 실제로 찍힌 종류만 (이름, BGR)."""
    used = []
    for key, v in TAGS.items():
        if any(o["tag"] == key for o in obstacles):
            used.append((v["name"], v["bgr"]))
    return used
