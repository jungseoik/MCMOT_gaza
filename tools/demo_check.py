#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""연출된 궤적 점검 — 장애물 통과·동시 인원·합류 여부를 **수치로** 본다.

눈으로 보면 놓친다. 장애물을 뚫고 지나가는 점이 하나라도 있으면 그림 전체를
못 믿으니, 렌더 전에 여기서 0 인지 확인한다.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2                                          # noqa: E402
import numpy as np                                  # noqa: E402

from system.config.schema import SiteConfig         # noqa: E402
from system.metrics import recorder                 # noqa: E402
from tools import demo_obstacles as dobs            # noqa: E402
from tools import demo_tracks as dt                 # noqa: E402
from tools.session_viz import find_session          # noqa: E402


def check(session, floor=None):
    db, fl = find_session(session, floor)
    site = SiteConfig.model_validate(recorder.load_meta(db)["site_view"])
    mpp = site.map.resolve_m_per_px()
    d = dt.load(session)
    obs = [np.array(o["points"], np.int32) for o in dobs.load(fl)]

    inside = 0
    total = 0
    who = set()
    for p in d["people"]:
        for x, y in p["xy"]:
            total += 1
            for poly in obs:
                if cv2.pointPolygonTest(poly, (float(x), float(y)), False) >= 0:
                    inside += 1
                    who.add(p["id"])
                    break

    T = d["t_end"]
    conc = [len(dt.at(d, T * f)) for f in (0, .1, .25, .4, .55, .7, .85, 1.0)]
    grew = [i for i in range(1, len(conc)) if conc[i] > conc[i - 1]]

    # 역주행 — 출구 쪽에서 되돌아 나오는 사람
    back = []
    for p in d["people"]:
        xy = p["xy"]
        if len(xy) < 20:
            continue
        ex = [[tuple(e.line[0]), tuple(e.line[1])] for e in site.exits if e.line]
        de = [min(dt._seg_dist(q, a, b) for a, b in ex) * mpp for q in xy]
        # 출구 5m 안에 들어왔다가 다시 12m 넘게 멀어지면 역주행
        seen_near = False
        for v in de:
            if v < 5.0:
                seen_near = True
            elif seen_near and v > 12.0:
                back.append(p["id"])
                break

    print(f"== {session} ({fl}) · 연출 {d.get('choreo', '—')}")
    print(f"   장애물 통과 점 {inside}/{total}"
          + (f"  ⚠ {sorted(who)}" if inside else "  ✅"))
    print(f"   동시 인원 {conc}" + ("  ⚠ 중간에 증가" if grew else "  ✅ 단조 감소"))
    print(f"   출구 역주행 {sorted(set(back)) or '없음'}"
          + ("" if not back else "  ⚠"))
    return inside, bool(grew), bool(back)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--floor")
    a = ap.parse_args()
    check(a.session, a.floor)
