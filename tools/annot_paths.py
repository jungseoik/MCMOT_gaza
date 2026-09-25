#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""장애물 표시용 **밑그림** 만들기 — 도면 + 실제 동선.

:8920 편집기에서 "사람이 실제로 다닌 자리"를 보면서 장애물을 찍으라고 깔아 주는
배경이다. 층 하나에 여러 각본이 걸리면(3층=01·02·03) **합쳐서** 한 장으로 만든다
— 장애물은 각본마다 옮겨 다니지 않으니 한 벌만 찍으면 된다.

    python tools/annot_paths.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2                                   # noqa: E402
import numpy as np                           # noqa: E402

from system.config.schema import CameraConfig, SiteConfig   # noqa: E402
from system.metrics import recorder                          # noqa: E402
from system.spatial import CameraProjector                   # noqa: E402
from tools.session_viz import find_session                   # noqa: E402

ANN = ROOT / "results/demo/_annotate"
SITE = ROOT / "data/sites/default"


def tracks_of(session_id: str, floor_hint=None):
    """녹화본 → 트랙별 도면 좌표 폴리라인."""
    db, floor = find_session(session_id, floor_hint)
    meta = recorder.load_meta(db)
    site = SiteConfig.model_validate(meta["site_view"])
    cams = [CameraConfig.model_validate(c) for c in (meta.get("cameras") or [])]
    mw = site.map.w if site.map else None
    mh = site.map.h if site.map else None
    projs = {c.cam_id: CameraProjector(c, mw, mh) for c in cams if c.mapping is not None}

    paths: dict[str, list] = {}
    for cam_id, ts, trs in recorder.iter_calls(db):
        pr = projs.get(cam_id)
        if pr is None:
            continue
        for tr in trs:
            p = pr.project(tr.foot_uv)
            if p is not None and getattr(p, "in_bounds", True):
                paths.setdefault(f"{cam_id}:{tr.local_track_id}", []).append([p.x, p.y])
    return floor, {k: v for k, v in paths.items() if len(v) >= 3}


def render(floor: str, tracks: dict, out: Path):
    mapf = SITE / ("map.png" if floor == "default" else f"map_{floor}.png")
    img = cv2.imread(str(mapf))
    h, w = img.shape[:2]

    # ① 통행 밀도 — 어디를 얼마나 많이 지났나. 흐리게 깔아 "여긴 길이다"를 보여준다.
    heat = np.zeros((h, w), np.float32)
    for pts in tracks.values():
        a = np.array(pts, np.int32)
        cv2.polylines(heat, [a], False, 1.0, 5)
    heat = cv2.GaussianBlur(heat, (0, 0), 7)
    if heat.max() > 0:
        heat /= heat.max()
    col = cv2.applyColorMap((np.clip(heat, 0, 1) ** 0.55 * 255).astype(np.uint8),
                            cv2.COLORMAP_TURBO)
    # 도면이 비쳐야 한다 — 장애물을 찍을 자리는 동선이 아니라 도면에 있다
    a = (np.clip(heat, 0, 1) ** 0.6 * 0.42)[..., None]
    img = (img * (1 - a) + col * a).astype(np.uint8)

    # ② 개별 동선 — 밀도만으로는 "한 사람이 어떻게 돌았나"가 안 보인다
    rng = np.random.default_rng(7)
    for pts in tracks.values():
        hsv = np.uint8([[[int(rng.integers(0, 180)), 200, 235]]])
        c = tuple(int(v) for v in cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0][0])
        cv2.polylines(img, [np.array(pts, np.int32)], False, c, 1, cv2.LINE_AA)

    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), img)
    return w, h


# 층 하나 = 밑그림 한 장. 3층은 각본 3건을 겹쳐 그린다.
GROUPS = {
    "floor4": ["01_IDR", "02_EPFI", "03_CBS"],
    "floor5": ["04_SEI"],
}


def main() -> int:
    for floor, tags in GROUPS.items():
        merged, labels = {}, []
        for tag in tags:
            meta = json.loads((ANN / f"{tag}_paths.json").read_text(encoding="utf-8"))
            f, tr = tracks_of(meta["session_id"], meta.get("floor"))
            if f != floor:
                print(f"  ! {tag} 은 {f} 층 — {floor} 그룹에서 건너뜀")
                continue
            merged.update({f"{tag}:{k}": v for k, v in tr.items()})
            labels.append(meta.get("label") or tag)
        out = ANN / f"{floor}_paths.png"
        w, h = render(floor, merged, out)
        (ANN / f"{floor}_paths.json").write_text(json.dumps(
            {"floor": floor, "map_px": [w, h], "sources": labels,
             "tracks": len(merged)}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[{floor}] 각본 {len(labels)}건 · 트랙 {len(merged)}개 → {out.name} ({w}×{h})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
