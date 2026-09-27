#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""이상적 궤적 만들기 + 눈으로 확인할 대조 그림.

    python tools/demo_tracks_build.py --session <sess-id> --floor <층> [--people 10]

만든 결과는 `results/demo/_annotate/ideal_<세션>.json`, 대조 그림은 `--png`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2                                          # noqa: E402
import numpy as np                                  # noqa: E402

from system.config.schema import SiteConfig         # noqa: E402
from system.metrics import recorder                 # noqa: E402
from system.metrics.replay import run_replay        # noqa: E402
from tools import demo_ghosts as dg                 # noqa: E402
from tools import demo_obstacles as dobs            # noqa: E402
from tools import demo_choreo as dc                 # noqa: E402
from tools import demo_tracks as dt                 # noqa: E402
from tools.session_viz import find_session          # noqa: E402

SITE = ROOT / "data/sites/default"
ANCH = ROOT / "results/demo/_annotate"


def anchors(floor: str) -> dict:
    """사람이 :8920 에서 찍어 준 기준점 — 시작지점·계단 출구.

    투영만으로는 못 정하는 자리가 있다(계단 출구가 대표적 — 계단을 내려오는
    사람의 발끝 투영은 계단실 벽 안쪽에 찍힌다). 찍어 준 게 있으면 그걸 쓴다."""
    p = ANCH / f"anchors_{floor}.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("points") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def distfield(site: SiteConfig, floor: str, m_per_px: float):
    """경로 산출과 **같은 벽**을 쓴다 — 저기선 못 지나가는데 여기선 지나가면 안 된다."""
    from system.evac.person_routes import _edits_from_floor_json
    from system.evac.route_solver import build_distfield
    png = SITE / ("map.png" if floor == "default" else f"map_{floor}.png")
    exits = [[tuple(e.line[0]), tuple(e.line[1])] for e in site.exits if e.line]
    carve, blocks = _edits_from_floor_json(SITE, floor, m_per_px)
    if not carve:
        carve = [r.points for r in site.routes if len(r.points) >= 2]
    return build_distfield(str(png), exits, m_per_px, carve_paths=carve or None,
                           carve_w_m=2.0, clearance_m=0.15,
                           extra_walls=blocks or None,
                           hard_walls=[o["points"] for o in dobs.load(floor)] or None), exits


def preview(site, floor, data, out_png, raw_frames, t0):
    """왼쪽 = 원 관측(파편), 오른쪽 = 이어 붙인 N명. 한눈에 비교하라고."""
    mapf = SITE / ("map.png" if floor == "default" else f"map_{floor}.png")
    base = cv2.imread(str(mapf))
    h, w = base.shape[:2]
    panes = []

    left = base.copy()
    rng = np.random.default_rng(3)
    import collections
    raw = collections.defaultdict(list)
    for f in raw_frames:
        for o in f["objects"]:
            raw[o["gid"]].append((o["x"], o["y"]))
    for pts in raw.values():
        if len(pts) < 2:
            continue
        hsv = np.uint8([[[int(rng.integers(0, 180)), 210, 230]]])
        c = tuple(int(v) for v in cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0][0])
        cv2.polylines(left, [np.array(pts, np.int32)], False, c, 3, cv2.LINE_AA)
    cv2.putText(left, f"RAW {len(raw)} tracklets", (30, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 255), 4)
    panes.append(left)

    right = base.copy()
    for o in dobs.load(floor):
        poly = np.array(o["points"], np.int32)
        ov = right.copy(); cv2.fillPoly(ov, [poly], (60, 60, 230))
        cv2.addWeighted(ov, 0.30, right, 0.70, 0, right)
        cv2.polylines(right, [poly], True, (40, 40, 210), 3)
    for i, p in enumerate(data["people"]):
        hsv = np.uint8([[[int(i * 180 / max(1, len(data["people"]))), 220, 230]]])
        c = tuple(int(v) for v in cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0][0])
        a = np.array(p["xy"], np.int32)
        cv2.polylines(right, [a], False, c, 4, cv2.LINE_AA)
        cv2.circle(right, tuple(a[0]), 13, c, -1)
        cv2.circle(right, tuple(a[0]), 13, (255, 255, 255), 2)
        cv2.putText(right, p["id"], (int(a[0][0]) + 16, int(a[0][1]) + 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 3)
        cv2.putText(right, p["id"], (int(a[0][0]) + 16, int(a[0][1]) + 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, c, 2)
    if data.get("waypoint"):
        wx, wy = int(data["waypoint"][0]), int(data["waypoint"][1])
        cv2.circle(right, (wx, wy), 30, (140, 0, 140), 3)
        cv2.putText(right, "VIA", (wx - 30, wy - 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (140, 0, 140), 3)
    if data.get("hub"):
        hx, hy = int(data["hub"][0]), int(data["hub"][1])
        cv2.circle(right, (hx, hy), 46, (0, 0, 0), 3)
        cv2.putText(right, "START", (hx - 52, hy - 56),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 3)
    cv2.putText(right, f"IDEAL {len(data['people'])} people", (30, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 150, 0), 4)
    panes.append(right)

    out = np.full((h, w * 2 + 12, 3), 20, np.uint8)
    out[:, :w] = panes[0]
    out[:, w + 12:] = panes[1]
    sc = 1800 / out.shape[1]
    cv2.imwrite(str(out_png), cv2.resize(out, (1800, int(out.shape[0] * sc))))
    return out_png


def main() -> int:
    ap = argparse.ArgumentParser(description="이상적 궤적 합성 (표출 전용)")
    ap.add_argument("--session", required=True)
    ap.add_argument("--floor")
    ap.add_argument("--people", type=int, default=dt.N_PEOPLE)
    ap.add_argument("--png", help="대조 그림 경로")
    ap.add_argument("--raw-only", action="store_true",
                    help="각본 연출을 건너뛰고 이어 붙인 궤적만 저장")
    a = ap.parse_args()

    db, floor = find_session(a.session, a.floor)
    meta = recorder.load_meta(db)
    site = SiteConfig.model_validate(meta["site_view"])
    m_per_px = site.map.resolve_m_per_px()
    ghosts = [g["key"] for g in dg.load(a.session)]
    res, tl, frames, _ = run_replay(db, {"exclude_tracks": ghosts} if ghosts else {}, fps=5.0)
    t0 = float(meta["alarm_ts"])

    df, exits = distfield(site, floor, m_per_px)
    data = dt.build(frames, site, df, t0=t0, n_people=a.people,
                    m_per_px=m_per_px, exits_px=exits)
    # 각본 연출 — 이어 붙인 궤적을 base 로 쓰고 각본 인원을 그 위에 배치한다
    spec = dc.SPECS.get(a.session)
    if spec and not a.raw_only:
        base_n = len(data["people"])
        pas = dc.passage_times(tl, t0)
        anc = anchors(floor)
        print("[싱크] 출구 통과 실제 시각 "
              + " · ".join(f"{k} {[round(v,1) for v in vs]}" for k, vs in sorted(pas.items())))
        if anc:
            print(f"[앵커] 사람이 찍어 준 지점 {sorted(anc)}")
        data = dc.build(data, df, m_per_px, exits, spec, passages=pas, anchors=anc)
        print(f"[각본] {spec['label']} — base {base_n}명 → 연출 {data['n']}명")
        for g in spec["groups"]:
            via = "우측 우회" if g.get("via") == "right" else "곧장 출구"
            ex = "" if g.get("exit") is None else f" · 출구 {g['exit']}"
            print(f"        {g['n']}명 {via}{ex} · 출발간격 {g['gap']}s "
                  f"· 좌우폭 {g['spread']}m")
    data["session_id"] = a.session
    data["floor"] = floor
    p = dt.save(a.session, data)

    spans = [(q["t0"], q["t0"] + len(q["xy"]) * data["dt"]) for q in data["people"]]
    print(f"{a.session} ({floor}) · 파편 → {data['n']}명 · 영상 {data['t_end']:.0f}s")
    for q, (s, e) in zip(data["people"], spans):
        print(f"  {q['id']}  {s:5.1f}s ~ {e:5.1f}s  ({e - s:4.1f}s, 점 {len(q['xy'])})"
              + ("  출구" if q["exit_at"] is not None else "  ※ 출구 도달 못함"))
    print("저장 →", p)
    if a.png:
        print("대조 그림 →", preview(site, floor, data, a.png, frames, t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
