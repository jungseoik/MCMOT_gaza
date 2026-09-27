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

    # 역주행 — **자기가 나가는 문** 앞까지 갔다가 되돌아 나오는 사람.
    # 모든 출구를 재면 우회조가 스쳐 지나간 반대편 문을 세어 오탐이 난다.
    ex = [[tuple(e.line[0]), tuple(e.line[1])] for e in site.exits if e.line]
    back = []
    for p in d["people"]:
        xy = p["xy"]
        if len(xy) < 20:
            continue
        mine = ex[int(np.argmin([dt._seg_dist(xy[-1], a, b) for a, b in ex]))]
        de = [dt._seg_dist(q, mine[0], mine[1]) * mpp for q in xy]
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


def sync_report(session, floor=None):
    """점이 문을 지나는 시각 vs **실제 통과 시각** — 싱크의 정본."""
    import numpy as np

    from system.metrics.replay import run_replay
    from tools import demo_choreo as dc
    from tools import demo_ghosts as dg

    db, fl = find_session(session, floor)
    meta = recorder.load_meta(db)
    site = SiteConfig.model_validate(meta["site_view"])
    mpp = site.map.resolve_m_per_px()
    ex = [[tuple(e.line[0]), tuple(e.line[1])] for e in site.exits if e.line]
    gh = [g["key"] for g in dg.load(session)]
    _, tl, _, _ = run_replay(db, {"exclude_tracks": gh} if gh else {}, fps=5.0)
    real = dc.passage_times(tl, float(meta["alarm_ts"]))
    d = dt.load(session)

    mine = []
    for p in d["people"]:
        xy = p["xy"]
        k = int(np.argmin([dt._seg_dist(xy[-1], a, b) for a, b in ex]))
        de = [dt._seg_dist(q, ex[k][0], ex[k][1]) * mpp for q in xy]
        i = next((n for n, v in enumerate(de) if v < 1.0), len(de) - 1)
        mine.append((p["t0"] + i * d["dt"], k))
    for k in sorted({q[1] for q in mine}):
        got = sorted(t for t, kk in mine if kk == k)
        want = sorted(real.get(f"exit-{k}", []))[:len(got)]
        if not want:
            continue
        err = [abs(a - b) for a, b in zip(got, want)]
        print(f"   exit-{k} 통과시각  실제 {[round(v) for v in want]}")
        print(f"             점     {[round(v) for v in got]}"
              f"   차이 평균 {sum(err)/len(err):.1f}s 최대 {max(err):.1f}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--floor")
    a = ap.parse_args()
    check(a.session, a.floor)
    sync_report(a.session, a.floor)
