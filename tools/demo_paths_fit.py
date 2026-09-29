#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""사람이 그린 동선을 **실제 출구 통과 시각에 정렬**한다.

편집기에서 [출구 시각에 맞추기]를 사람마다 누르지 않았어도, 여기서 한 번에
맞춘다. 하는 일은 둘뿐이다.

  ① 통과 시각 배정 — **찍은 끝 시각 순서를 그대로 지켜** 배정한다. P01→첫 번째로
     고정하면 먼저 도착하게 그려 둔 사람이 뒤로 밀려 서로 추월한다.
  ② 시각 늘이기 — 시작은 그대로 두고 **마지막 점이 그 통과 시각에** 오도록
     전체를 비례로 늘인다. 자리는 건드리지 않는다 — 그린 사람의 선이 정본이다.

    python tools/demo_paths_fit.py --session <sess-id> [--apply]
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from system.config.schema import SiteConfig          # noqa: E402
from system.metrics import recorder                  # noqa: E402
from system.metrics.replay import run_replay         # noqa: E402
from tools import demo_ghosts as dghost              # noqa: E402
from tools import demo_paths as dp                   # noqa: E402
from tools.session_viz import find_session           # noqa: E402

V_MAX = 1.6            # 훈련 보행 속도 상한 — 이보다 빠르면 달리는 그림이 된다


def pace_table(sid: str, floor: str | None, n_bin: int = 24):
    """실측에서 뽑는 **걸음 리듬** — "출구까지 d m 남았을 때 통과까지 몇 초".

    거리는 **지금까지 가장 가까웠던 거리**(누적 최소)로 잰다. 그냥 직선거리면
    우회조가 고리를 도느라 멀어졌다 가까워져 같은 거리에 두 시각이 걸린다.
    누적 최소는 항상 줄어들고, 문 앞에서는 직선거리와 같다.

    이 표 하나로 두 가지가 동시에 풀린다.
      · **문 앞 정체** — 실측이 마지막 몇 m 에서 느려진 그대로 느려진다
      · **한 명만 앞장서는 것** — 같은 무리가 같은 리듬을 공유하게 된다
    """
    import collections

    import numpy as np

    from system.metrics.replay import run_replay
    from tools import demo_tracks as dtk

    db, fl = find_session(sid, floor)
    meta = recorder.load_meta(db)
    t0 = float(meta["alarm_ts"])
    site = SiteConfig.model_validate(meta["site_view"])
    mpp = site.map.resolve_m_per_px()
    exits = [[tuple(e.line[0]), tuple(e.line[1])] for e in site.exits if e.line]
    gh = [g["key"] for g in dghost.load(sid)]
    _, _, frames, _ = run_replay(db, {"exclude_tracks": gh} if gh else {}, fps=5.0)

    tr = collections.defaultdict(list)
    for fr in frames:
        for o in fr["objects"]:
            tr[o["gid"]].append((fr["ts"] - t0, o["x"], o["y"]))
    pairs = []
    for pts in tr.values():
        de = [min(dtk._seg_dist((q[1], q[2]), a, b) for a, b in exits) * mpp
              for q in pts]
        if min(de) > 1.5:
            continue                       # 출구에 닿지 않은 트랙은 리듬을 모른다
        i = int(np.argmin(de))
        t_pass = pts[i][0]
        run = 1e9
        for j in range(i + 1):
            run = min(run, de[j])
            pairs.append((run, t_pass - pts[j][0]))
    if len(pairs) < 20:
        return None
    arr = np.array(pairs)
    hi = float(np.percentile(arr[:, 0], 97))
    edges = np.linspace(0, hi, n_bin + 1)
    xs, ys = [0.0], [0.0]
    for lo, up in zip(edges, edges[1:]):
        m = (arr[:, 0] >= lo) & (arr[:, 0] < up)
        if m.sum() >= 3:
            xs.append((lo + up) / 2)
            ys.append(float(np.median(arr[m, 1])))
    for k in range(1, len(ys)):             # 멀수록 오래 걸린다 — 단조로 편다
        ys[k] = max(ys[k], ys[k - 1] + 0.05)
    if len(xs) < 3:
        return None
    slope = (ys[-1] - ys[-2]) / max(0.1, xs[-1] - xs[-2])   # 밖은 등속 외삽
    return {"r": xs, "t": ys, "slope": max(0.3, slope), "n": len(arr)}


def apply_pace(ks, table, t_pass, mpp, v_out):
    """키프레임 시각을 다시 매긴다. 자리는 그대로.

    표가 닿는 **문 앞 구간만** 실측 리듬을 쓰고, 그 밖은 `v_out`(그 무리가 원래
    걷던 속도)으로 간다. 표는 카메라가 문 앞만 보는 탓에 몇 m 까지밖에 없어서,
    그 기울기로 30m 를 외삽하면 2.5 m/s 로 달리게 된다(실측).

    앞쪽이 경보 전으로 넘어가면 그 키프레임은 **버린다** — 통째로 밀면 통과
    시각이 어긋나고, 빨리 걷게 하면 달리는 그림이 된다. 이미 가고 있던 셈이다.
    """
    import numpy as np
    # 끝까지 남은 **경로 길이**
    rem = [0.0]
    for a, b in zip(reversed(ks[:-1]), reversed(ks[1:])):
        rem.append(rem[-1] + math.hypot(b["x"] - a["x"], b["y"] - a["y"]) * mpp)
    rem = rem[::-1]
    # 지금까지 가장 가까웠던 **직선거리**(누적 최소) — 문 앞 판정은 이쪽이다
    end = (ks[-1]["x"], ks[-1]["y"])
    run, near = 1e9, []
    for q in ks:
        run = min(run, math.hypot(q["x"] - end[0], q["y"] - end[1]) * mpp)
        near.append(run)
    R, T = table["r"], table["t"]
    out = []
    for r_arc, d in zip(rem, near):
        # 문 앞(표가 닿는 범위)에서는 **표만** 쓴다. 걸은 길이를 섞으면, 문 앞에서
        # 왔다갔다 그린 사람이 그만큼 일찍 진입한 것으로 계산돼 실측보다 훨씬
        # 먼저 문 앞에 모인다(실측: 36초에 0명인데 그린 건 10명이었다).
        # 표 밖에서는 남은 길이를 걷는 속도로 나눈다.
        if d <= R[-1]:
            g = float(np.interp(d, R, T))
        else:
            g = T[-1] + max(0.0, r_arc - R[-1]) / max(0.2, v_out)
        out.append(t_pass - g)
    for i in range(1, len(out)):
        out[i] = max(out[i], out[i - 1] + 0.05)
    keep = next((i for i, t in enumerate(out) if t >= 0), len(out) - 2)
    return out[keep:], keep


def match_rate(sid: str, floor: str | None, people: dict, r_m=3.0, w_s=2.0):
    """**찍은 (시각, 자리)가 실측 dot 와 맞나** — 이게 싱크의 실측 척도다.

    각 키프레임에 대해 "그 시각 ±{w_s}초에 {r_m}m 안에 실측 dot 가 있었나"를 센다.
    시각을 손볼 때마다 이 수치로 확인한다 — 눈으로는 좋아졌는지 나빠졌는지
    못 가린다(실측: 걸음 리듬을 씌웠더니 03 CBS 가 80% → 75% 로 나빠졌다).
    """
    import math as _m

    from system.metrics.replay import run_replay

    db, fl = find_session(sid, floor)
    meta = recorder.load_meta(db)
    t0 = float(meta["alarm_ts"])
    site = SiteConfig.model_validate(meta["site_view"])
    mpp = site.map.resolve_m_per_px()
    gh = [g["key"] for g in dghost.load(sid)]
    _, _, frames, _ = run_replay(db, {"exclude_tracks": gh} if gh else {}, fps=5.0)
    R = r_m / mpp
    hit = tot = 0
    ends = []
    for ks in people.values():
        if ks:
            ends.append(ks[-1]["t"])
        for q in ks:
            tot += 1
            hit += any(_m.hypot(o["x"] - q["x"], o["y"] - q["y"]) < R
                       for fr in frames if abs(fr["ts"] - t0 - q["t"]) <= w_s
                       for o in fr["objects"])
    if not tot:
        return None
    return 100 * hit / tot, hit, tot, min(ends), max(ends)


def formation(people: dict, order, t_pass: dict, mpp: float):
    """무리가 **같은 대형으로** 움직이게 시각을 다시 매긴다.

    각자 자기 박자로 그리면 무리가 흩어진다 — 실측은 사람 사이 퍼짐이 4.2m 인데
    그린 건 9m 였고(03 CBS), 우회조 선두는 2등보다 5.8m 앞섰다(02 EPFI).

    방식: 무리의 **진행 곡선을 공유**한다. 각자를 자기 통과 시각 기준으로 겹쳐
    놓고(τ = t − 통과시각) "통과 τ초 전에는 전체 경로의 몇 %를 갔나"의 중앙값을
    구한 뒤, 모두 그 곡선을 따르게 한다. 벌어지는 간격은 **실제 통과 시각 차이**
    그대로다 — 1초 먼저 나간 사람이 1초만큼 앞선다.

    자리는 건드리지 않는다. 진행도(경로를 따라 얼마나 갔나)는 그대로이고 그
    지점을 **언제** 지나는지만 바뀐다.
    """
    import numpy as np

    def prof(ks):
        t = np.array([k["t"] for k in ks], float)
        d = np.concatenate([[0.0], np.cumsum(
            [math.hypot(b["x"] - a["x"], b["y"] - a["y"])
             for a, b in zip(ks, ks[1:])])])
        return t, d / (d[-1] or 1.0)

    groups: dict[tuple, list] = {}
    for k_ex, pid in order:
        groups.setdefault((k_ex,), []).append(pid)
    # 통과 시각이 10초 넘게 벌어지면 다른 무리다(02 EPFI 의 직선조/우회조)
    split = {}
    for key, pids in groups.items():
        pids = sorted(pids, key=lambda q: t_pass[q])
        cur, out = [pids[0]], []
        for a, b in zip(pids, pids[1:]):
            if t_pass[b] - t_pass[a] > 10:
                out.append(cur)
                cur = []
            cur.append(b)
        out.append(cur)
        for i, g in enumerate(out):
            split[key + (i,)] = list(g)

    notes = []
    for key, pids in split.items():
        if len(pids) < 2:
            continue
        spans = [t_pass[q] - people[q][0]["t"] for q in pids]
        D = float(np.median(spans))
        grid = np.arange(-D, 0.0001, 0.1)
        rows = []
        for q in pids:
            t, u = prof(people[q])
            rows.append(np.interp(t_pass[q] + grid, t, u, left=0.0, right=1.0))
        U = np.median(np.stack(rows), axis=0)
        for i in range(1, len(U)):                 # 진행도는 줄지 않는다
            U[i] = max(U[i], U[i - 1] + 1e-6)
        for q in pids:
            t, u = prof(people[q])
            tau = np.interp(u, U, grid)            # 그 진행도를 언제 지나나
            for k, nt in zip(people[q], t_pass[q] + tau):
                k["t"] = round(float(nt), 2)
            for a, b in zip(people[q], people[q][1:]):
                b["t"] = max(b["t"], a["t"] + 0.05)
        notes.append(f"{'·'.join(pids)} ({len(pids)}명, 통과 "
                     f"{min(t_pass[q] for q in pids):.0f}~{max(t_pass[q] for q in pids):.0f}s)")
    return notes


def speed_profile(sid: str, floor: str | None, bin_s: float = 1.0):
    """실측의 **1초 단위 평균 속도 곡선**. 언제 느리고 언제 빨랐나.

    한 구간만 멈춰 세우는 걸로는 모자랐다. 실측(03 CBS)은 이렇게 간다.
        0~8s   0.15~0.48 m/s   거의 안 움직인다(모여서 머뭇거림)
        9~13s  0.64~0.97       한 번에 움직인다
        14~17s 0.22~0.39       다시 선다
        27~35s 0.25~0.50       또 느려진다
    빠름·느림이 번갈아 온다. 그래서 **곡선 전체**를 따르게 한다 — 절대값이
    아니라 **모양**만 쓴다(총 시간은 각자의 통과 시각에 맞춘다).
    """
    import collections

    import numpy as np

    from system.metrics.replay import run_replay

    db, fl = find_session(sid, floor)
    meta = recorder.load_meta(db)
    t0 = float(meta["alarm_ts"])
    site = SiteConfig.model_validate(meta["site_view"])
    mpp = site.map.resolve_m_per_px()
    gh = [g["key"] for g in dghost.load(sid)]
    _, _, frames, _ = run_replay(db, {"exclude_tracks": gh} if gh else {}, fps=5.0)
    tr = collections.defaultdict(list)
    for fr in frames:
        for o in fr["objects"]:
            tr[o["gid"]].append((fr["ts"] - t0, o["x"], o["y"]))
    if not tr:
        return None
    t_end = max(q[-1][0] for q in tr.values())
    ts = np.arange(0, t_end + bin_s, bin_s)
    vs = np.zeros(len(ts))
    for k, lo in enumerate(ts):
        acc, c = [], 0
        for pts in tr.values():
            for a, b in zip(pts, pts[1:]):
                if not (lo <= a[0] < lo + bin_s):
                    continue
                dtt = b[0] - a[0]
                if dtt <= 0 or dtt > 0.5:
                    continue
                acc.append(math.hypot(b[1] - a[1], b[2] - a[2]) * mpp / dtt)
                c += 1
        vs[k] = float(np.mean(acc)) if c >= 5 else np.nan
    ok = ~np.isnan(vs)
    if ok.sum() < 5:
        return None
    vs = np.interp(ts, ts[ok], vs[ok])
    vs = np.maximum(vs, 0.05)                 # 완전 0 이면 영영 못 간다
    return ts, vs


def sharpen(prof, gain: float):
    """완급의 **대비를 키운다** — 느린 구간은 더 느리게, 빠른 구간은 더 빠르게.

    경로 길이와 통과 시각이 정해져 있으면 **평균 속도는 못 낮춘다**(03 CBS 는
    33.4m ÷ 46s = 0.73 m/s 가 하한이다). 그래도 "멈췄다 간다"로 보이게 하려면
    같은 평균 안에서 대비를 키우는 수밖에 없다 — 실측이 바로 그 모양이다
    (멈춤 47% · 걸을 땐 0.81 m/s).
    """
    import numpy as np
    ts, vs = prof
    v = np.asarray(vs, float)
    return ts, np.maximum(v.min() * 0.5, (v / v.mean()) ** gain * v.mean())


def find_pauses(prof, thr: float = 0.40, min_s: float = 1.5):
    """실측 속도 곡선에서 **느린 구간을 전부** 찾는다.

    한 구간만 세우는 걸로는 모자랐다. 03 CBS 는 0~2s · 6~9s · 14~18s · 32~36s
    네 번 느려진다. 다 세워야 "뭉쳤다 움직인다"가 보인다.

    곡선의 대비를 키우는(gain) 방법도 써 봤지만 평균이 정해져 있어서 느린 만큼
    다른 데가 빨라진다 — gain 3 에서 4.2 m/s 로 달렸다. 명시적으로 세우고
    나머지를 고르게 걷는 편이 낫다.
    """
    import numpy as np
    ts, vs = prof
    out, run = [], None
    for t, v in zip(ts, vs):
        if v < thr:
            run = (run[0], t + (ts[1] - ts[0])) if run else (t, t + (ts[1] - ts[0]))
        elif run:
            if run[1] - run[0] >= min_s:
                out.append(run)
            run = None
    if run and run[1] - run[0] >= min_s:
        out.append(run)
    return out


def find_pause(sid: str, floor: str | None, win: float = 6.0, thr: float = 0.45):
    """실측에서 **다들 멈춰 있던 때**를 찾는다.

    03 CBS 각본은 "문 앞 밀집 **2회**"다. 실측을 보면 47%가 멈춰 있고 걷는
    속도는 0.81 m/s — 그린 동선(0.73)보다 오히려 빠르다. 즉 차이는 속도가
    아니라 **멈춤**이다. 그 멈춤을 안 넣으면 쉬지 않고 걷는 그림이 되고,
    그래서 빨라 보인다.
    """
    import collections

    import numpy as np

    from system.metrics.replay import run_replay

    db, fl = find_session(sid, floor)
    meta = recorder.load_meta(db)
    t0 = float(meta["alarm_ts"])
    site = SiteConfig.model_validate(meta["site_view"])
    mpp = site.map.resolve_m_per_px()
    gh = [g["key"] for g in dghost.load(sid)]
    _, _, frames, _ = run_replay(db, {"exclude_tracks": gh} if gh else {}, fps=5.0)
    tr = collections.defaultdict(list)
    for fr in frames:
        for o in fr["objects"]:
            tr[o["gid"]].append((fr["ts"] - t0, o["x"], o["y"]))
    t_end = max(q[-1][0] for q in tr.values()) if tr else 0
    best = None
    for lo in np.arange(0, max(0.1, t_end - win), win / 2):
        n = c = 0
        for pts in tr.values():
            for a, b in zip(pts, pts[1:]):
                if not (lo <= a[0] < lo + win):
                    continue
                dtt = b[0] - a[0]
                if dtt <= 0 or dtt > 0.5:
                    continue
                c += 1
                if math.hypot(b[1] - a[1], b[2] - a[2]) * mpp / dtt >= 0.15:
                    n += 1
        if c > 40:
            r = n / c
            if r < thr and (best is None or r < best[2]):
                best = (float(lo), float(lo + win), r)
    return best


def hold_at_door(people: dict, order, t_pass: dict, mpp: float, exits,
                 enter_m: float = 2.5, walk_s: float = 3.0,
                 gather_s: float = 3.0, step_s: float = 0.2, pause=None,
                 prof=None):
    """문 앞에서 **잠깐 멈춰 밀집**하게 한다 — 병목이 눈에 보이게.

    실측이 그렇다: 문 앞 5m 안에 42~44초에 몰렸다가 44~49초에 **차례로** 빠져
    나간다(03 CBS). 병목 폴리곤 안 체류도 3.0~3.9초다. 그런데 통과 시각까지
    고르게 진행시키면 다들 흘러가듯 지나가 **병목이 안 보인다**.

    그래서 셋으로 나눠 시각을 준다.
      ① 접근  — 출발 ~ 문 앞 {enter_m}m 까지. 모두 비슷한 때 도착한다
      ② 대기  — 거기서 **자기 통과 시각 {walk_s}초 전까지 멈춘다.** 먼저 도착한
                 사람일수록 오래 서 있다 = 밀집
      ③ 통과  — 마지막 {walk_s}초에 문을 지난다
    """
    import numpy as np
    seq = sorted(order, key=lambda z: t_pass[z[1]])
    t_min = min(t_pass[pid] for _, pid in seq)
    notes = []
    for j, (_k, pid) in enumerate(seq):
        ks = people[pid]
        d = [min(dt_seg(q, e, mpp) for e in exits) for q in ks]
        i_in = next((i for i, v in enumerate(d) if v <= enter_m), None)
        if i_in is None or i_in < 2:
            continue
        t_a = ks[0]["t"]
        t_go = t_pass[pid] - walk_s                    # 문을 향해 나서는 순간
        t_in = min(t_go, t_min - gather_s + j * step_s)  # 문 앞 도착
        t_in = max(t_in, t_a + 1.0)
        # ① 접근 — 문 앞까지. **실측 속도 곡선의 모양**을 따른다(느릴 때 느리게).
        arc = np.concatenate([[0.0], np.cumsum(
            [math.hypot(b["x"] - a["x"], b["y"] - a["y"])
             for a, b in zip(ks[:i_in + 1], ks[1:i_in + 1])])])
        L = arc[-1] or 1.0
        if prof is not None:
            pt, pv = prof
            # 키프레임이 성기면(0.7~1.5초) 1초 단위 완급을 담을 수 없다 —
            # 사이를 보간하며 등속이 된다. 접근 구간을 **0.5m 간격**으로 다시 뽑는다.
            step = 0.5 / mpp
            n_fine = max(4, int(L / step))
            g = np.linspace(0, L, n_fine + 1)
            xs = np.interp(g, arc, [q["x"] for q in ks[:i_in + 1]])
            ys = np.interp(g, arc, [q["y"] for q in ks[:i_in + 1]])
            tail = ks[i_in + 1:]
            ks = ([{"t": 0.0, "x": round(float(x), 1), "y": round(float(y), 1)}
                   for x, y in zip(xs, ys)] + tail)
            i_in = n_fine
            arc, L = g, (g[-1] or 1.0)
            fine = np.arange(max(0.0, t_a), t_in + 0.05, 0.05)
            w = np.interp(fine, pt, pv)
            cum = np.concatenate([[0.0], np.cumsum(w[:-1]) * 0.05])
            cum = cum / (cum[-1] or 1.0)
            for i in range(i_in + 1):
                ks[i]["t"] = round(float(np.interp(arc[i] / L, cum, fine)), 2)
            for a, b in zip(ks, ks[1:i_in + 1]):
                b["t"] = max(b["t"], a["t"] + 0.05)
            for a, b in zip(ks, ks[1:]):
                b["t"] = max(b["t"], a["t"] + 0.05)
            arc2 = np.concatenate([[0.0], np.cumsum(
                [math.hypot(b["x"] - a["x"], b["y"] - a["y"])
                 for a, b in zip(ks[i_in:], ks[i_in + 1:])])])
            L2 = arc2[-1] or 1.0
            for k, i in enumerate(range(i_in, len(ks))):
                ks[i]["t"] = round(t_go + walk_s * arc2[k] / L2, 2)
            for a, b in zip(ks, ks[1:]):
                b["t"] = max(b["t"], a["t"] + 0.05)
            people[pid] = ks
            notes.append(f"{pid} 도착 {t_in:.1f}s · 대기 {max(0.0, t_go - t_in):.1f}s")
            continue
        zones = [z for z in (pause or []) if z[1] > t_a and z[0] < t_in]
        P = sum(min(b1, t_in) - max(a1, t_a) for a1, b1 in zones)
        walkable = max(1.0, (t_in - t_a) - P)
        for i in range(i_in + 1):
            w = t_a + walkable * arc[i] / L
            for a1, b1 in zones:                   # 지나온 멈춤만큼 뒤로 민다
                if w >= a1:
                    w += min(b1, t_in) - max(a1, t_a)
            ks[i]["t"] = round(w, 2)
        if P > 0.5:
            # **같은 자리에 두 점**을 박아야 진짜 멈춘다. 시각만 벌려 놓으면
            # 사이를 보간해 '느리게 걷는' 그림이 된다(실측: 멈춤 2%).
            for pa1, pb1 in reversed(zones):
                if pb1 <= t_a or pa1 >= t_in:
                    continue
                fr = max(0.0, min(1.0, (pa1 - t_a) / walkable))
                d_at = fr * L
                k = int(np.searchsorted(arc, d_at))
                k = max(1, min(k, i_in))
                a0, b0 = ks[k - 1], ks[k]
                span = (arc[k] - arc[k - 1]) or 1.0
                g = (d_at - arc[k - 1]) / span
                px = a0["x"] + (b0["x"] - a0["x"]) * g
                py = a0["y"] + (b0["y"] - a0["y"]) * g
                ks[k:k] = [{"t": round(pa1, 2), "x": round(px, 1), "y": round(py, 1)},
                           {"t": round(pb1, 2), "x": round(px, 1), "y": round(py, 1)}]
                i_in += 2
        # ②③ 대기 뒤 통과
        arc2 = np.concatenate([[0.0], np.cumsum(
            [math.hypot(b["x"] - a["x"], b["y"] - a["y"])
             for a, b in zip(ks[i_in:], ks[i_in + 1:])])])
        L2 = arc2[-1] or 1.0
        for k, i in enumerate(range(i_in, len(ks))):
            ks[i]["t"] = round(t_go + walk_s * arc2[k] / L2, 2)
        for a, b in zip(ks, ks[1:]):
            b["t"] = max(b["t"], a["t"] + 0.05)
        people[pid] = ks
        notes.append(f"{pid} 도착 {t_in:.1f}s · 대기 {max(0.0, t_go - t_in):.1f}s")
    return notes


def dt_seg(q, e, mpp):
    from tools import demo_tracks as dtk
    return dtk._seg_dist((q["x"], q["y"]), e[0], e[1]) * mpp


def load_jams(sid: str):
    """편집기(:8930)에서 찍어 준 **병목** — 어디서 · 몇 초부터 몇 초까지 · 몇 명."""
    f = dp.ANN / f"jams_{sid}.json"
    if not f.is_file():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("jams") or []
    except (OSError, json.JSONDecodeError):
        return []


def fit_exit_times(people: dict, order, exit_at: dict, ets, mpp: float,
                   v_target: float = 0.0):
    """통과 시각을 **실측(또는 지정값)에 맞춰** 키프레임 시각을 늘린다.

    시작(계단에서 나오는 시각)은 찍어 준 그대로 두고 끝만 민다 — 그 사이가
    늘어나면서 속도가 떨어진다. 손으로 찍으면 대개 빠르게 그려진다(12 SEI 는
    중앙 1.2~1.95 m/s, 뛰는 속도였다).
    """
    rows = []
    # **찍은 순서**(계단에서 나온 순서)대로 배정한다. 끝 시각으로 줄 세우면
    # 중간에 천천히 그린 사람이 뒤로 밀린다(12 SEI 의 P05 가 5번째 → 8번째).
    seq = sorted(order, key=lambda z: people[z[1]][0]["t"])
    for n, (_k, pid) in enumerate(seq):
        if n >= len(ets):
            break
        ks = people[pid]
        t_a, t_b = ks[0]["t"], ks[-1]["t"]
        tgt = float(ets[n])
        if v_target:
            # 보통 걸음으로 가도록 **나오는 시각을 앞당긴다.** 손으로 찍은
            # 시각 그대로면 21m 를 15초에 가게 되는데(1.4 m/s), 실측 걸음은
            # 0.74 m/s 다. 계단에서 나오는 건 각본이니 여기서 정해도 된다.
            d0 = sum(math.hypot(b["x"] - a["x"], b["y"] - a["y"]) * mpp
                     for a, b in zip(ks, ks[1:]))
            t_a = max(0.0, tgt - d0 / v_target)
        if tgt <= t_a + 0.5 or t_b <= ks[0]["t"]:
            continue
        f = (tgt - t_a) / (t_b - ks[0]["t"])
        t_from = ks[0]["t"]
        for k in ks:
            k["t"] = round(t_a + (k["t"] - t_from) * f, 2)
        exit_at[pid] = tgt
        d = sum(math.hypot(b["x"] - a["x"], b["y"] - a["y"]) * mpp
                for a, b in zip(ks, ks[1:]))
        rows.append(f"{pid} {t_from:.1f}~{t_b:.1f} → {t_a:.1f}~{tgt:.1f}s "
                    f"· {d:.1f}m · {d / (tgt - t_a):.2f} m/s")
        continue
        f = (tgt - t_a) / (t_b - t_a)
        for k in ks:
            k["t"] = round(t_a + (k["t"] - t_a) * f, 2)
        exit_at[pid] = tgt
        d = sum(math.hypot(b["x"] - a["x"], b["y"] - a["y"]) * mpp
                for a, b in zip(ks, ks[1:]))
        rows.append(f"{pid} {t_a:.1f}~{t_b:.1f} → {t_a:.1f}~{tgt:.1f}s "
                    f"· {d:.1f}m · {d / (tgt - t_a):.2f} m/s")
    return rows


def clamp_speed(people: dict, mpp: float, v_max: float = 1.5,
                keep_last: bool = False):
    """마지막 안전장치 — 앞 점에서 너무 멀리 뛴 점을 **그만큼만 옮긴다.**

    시간은 건드리지 않는다. 시간을 늘려 속도를 낮추면 병목에서 서성이던 리듬이
    같이 뭉개진다(그렇게 했더니 서성임이 0.15 → 0.98 m/s 가 됐다). 자리를 조금
    당기는 쪽이 싸다 — 걸리는 건 보통 몇 점뿐이다.
    """
    # 키프레임이 성기면 곡선(Catmull-Rom)이 밖으로 부풀며 속도가 커진다 —
    # 여기서 누른 값이 그려질 때 되살아난다(선두 P02 가 41s 에 2.2 m/s).
    # 먼저 **먼 구간에 중간점을 꽂아** 곡선이 덜 부풀게 한다.
    for pid, ks in people.items():
        out = [ks[0]]
        for a, b in zip(ks, ks[1:]):
            d = math.hypot(b["x"] - a["x"], b["y"] - a["y"]) * mpp
            k = int(d / 1.2)
            for m in range(1, k):
                u = m / k
                out.append({"t": round(a["t"] + (b["t"] - a["t"]) * u, 2),
                            "x": round(a["x"] + (b["x"] - a["x"]) * u, 1),
                            "y": round(a["y"] + (b["y"] - a["y"]) * u, 1)})
            out.append(b)
        people[pid] = out

    n = 0
    for ks in people.values():
        # 마지막 점은 **출구선 너머로 일부러 내보낸 자리**다. 여기서 당기면 점이
        # 선 안쪽으로 되돌아와 통과가 안 세어진다(실측 10명 중 2명만 잡혔다).
        tail = ks[-1] if keep_last and len(ks) >= 2 else None
        for a, b in zip(ks, ks[1:]):
            if b is tail:
                continue
            dt_ = max(0.05, b["t"] - a["t"])
            d = math.hypot(b["x"] - a["x"], b["y"] - a["y"]) * mpp
            lim = v_max * dt_
            if d > lim:
                f = lim / d
                b["x"] = round(a["x"] + (b["x"] - a["x"]) * f, 1)
                b["y"] = round(a["y"] + (b["y"] - a["y"]) * f, 1)
                n += 1
    return n


def _clean_seg(seg, mpp: float, v_max: float = 1.2):
    """실측 조각의 **튀는 점**을 다듬는다.

    투영 잡음으로 한두 점이 확 튄다(실측 최대 7.5 m/s). 그대로 가져오면 그
    사람이 병목 안에서 순간이동한다. 앞 점에서 v_max 넘게 벗어난 점은 그만큼만
    옮긴다 — 서성이는 모양은 그대로 남는다.
    """
    out = [seg[0]]
    for t, x, y in seg[1:]:
        pt, px, py = out[-1]
        dt_ = max(0.05, t - pt)
        d = math.hypot(x - px, y - py) * mpp
        lim = v_max * dt_
        if d > lim:
            f = lim / d
            x, y = px + (x - px) * f, py + (y - py) * f
        out.append((t, x, y))
    return out


def jam_segments(sid: str, floor: str | None, jams, mpp: float):
    """병목마다 **그 반경 안에서 실측이 실제로 움직인 조각**을 모은다.

    원 중심을 도는 규칙적인 움직임을 만들어 봤지만 그건 사람이 아니다. 그
    반경 안에서 사람들이 어떻게 서성였는지는 **실측에 이미 들어 있다** —
    03 CBS 는 병목마다 14~16개 조각이 있고 0.11~0.93 m/s 로 움직인다.
    그걸 가져다 쓰면 만들어 낼 것이 없다.
    """
    import collections

    from system.metrics.replay import run_replay

    db, fl = find_session(sid, floor)
    meta = recorder.load_meta(db)
    t0 = float(meta["alarm_ts"])
    gh = [g["key"] for g in dghost.load(sid)]
    _, _, frames, _ = run_replay(db, {"exclude_tracks": gh} if gh else {}, fps=5.0)
    tr = collections.defaultdict(list)
    for fr in frames:
        for o in fr["objects"]:
            tr[o["gid"]].append((fr["ts"] - t0, o["x"], o["y"]))
    out = []
    for j in jams:
        segs = []
        for pts in tr.values():
            seg = [(t, x, y) for t, x, y in pts
                   if j["t0"] - 1 <= t <= j["t1"] + 1
                   and math.hypot(x - j["x"], y - j["y"]) * mpp <= j["r"] * 1.3]
            if len(seg) >= 3:
                segs.append(_clean_seg(seg, mpp))
        segs.sort(key=len, reverse=True)
        out.append(segs)
    return out


def _wander(seg, j0, j1, dt_s=0.25):
    """실측 조각을 병목 구간에 맞춰 편다 — 모자라면 **왔다갔다 되돌린다**.

    조각이 병목 구간보다 짧을 때 그냥 멈춰 세우면 굳어 버린다. 앞뒤로 되짚으면
    (핑퐁) 이음매에서 튀지 않고 계속 서성이는 모양이 된다.
    """
    import numpy as np
    ts = np.array([q[0] for q in seg], float)
    xs = np.array([q[1] for q in seg], float)
    ys = np.array([q[2] for q in seg], float)
    span = ts[-1] - ts[0]
    if span < 0.5:
        return [(j0, xs[0], ys[0]), (j1, xs[0], ys[0])]
    out = []
    n = max(2, int((j1 - j0) / dt_s))
    for k in range(n + 1):
        t = j0 + (j1 - j0) * k / n
        u = (t - j0) % (2 * span)                 # 갔다가 되돌아온다
        u = u if u <= span else 2 * span - u
        out.append((t, float(np.interp(ts[0] + u, ts, xs)),
                    float(np.interp(ts[0] + u, ts, ys))))
    return out


V_APP = 0.90        # 문 앞 병목까지 걸어가는 속도 (실측 전체 중앙 0.99~1.1)
TAIL_S = 0.3        # 문 앞에서 멈칫하는 시간. 실측 문 앞 원 진입 중앙이 47.4s
                    # 로 통과 시각(44~49s)과 거의 같다 — 즉 **문에 닿자마자**
                    # 나간다. 여기를 3초로 두면 선두가 39초에 문 앞에 서 있게
                    # 되는데, 실제로는 44초에 도착한다.


def apply_jams(people: dict, order, t_pass: dict, mpp: float, jams, v: float = 0.9,
               t_start: float = 0.0, step_s: float = 0.7, segs_per_jam=None,
               door_times=None):
    """**찍어 준 병목대로** 다시 만든다 — 사이는 걷고, 안에서는 **서성인다.**

    병목은 가만히 서 있는 게 아니다. 반경 안에서 자리를 바꾸며 왔다갔다 한다 —
    그게 뭉쳐 있는 모습이다. 그래서 병목 구간에는 **그 원 안을 도는 점**을
    {step_s}초 간격으로 새로 박는다(사람마다 다른 자리에서 다른 방향으로).

    걷는 속도는 이쪽에서 정하지 않는다. 병목과 병목 사이의 **거리 ÷ 시간**이
    곧 속도다 — 찍어 준 시각이 그걸 정한다.
    """
    import numpy as np
    rows = []
    seq = sorted(order, key=lambda z: t_pass[z[1]])
    # 찍어 준 창은 **무리 전체**가 그 자리에 있던 시간대다. 한 사람 한 사람은
    # 자기 차례에 들어왔다 나간다 — 실측 병목2 는 진입이 26.0~38.8s 에 걸쳐
    # 차례로 일어났다. 창을 전원에게 똑같이 쓰면 10명이 한꺼번에 튀어나간다.
    # 그래서 **통과 시각 차이만큼 창을 통째로 민다.**
    t_mid = float(np.median([t_pass[z[1]] for z in seq]))
    for idx, (_k, pid) in enumerate(seq):
        ks = people[pid]
        arc = np.concatenate([[0.0], np.cumsum(
            [math.hypot(b["x"] - a["x"], b["y"] - a["y"]) * mpp
             for a, b in zip(ks, ks[1:])])])
        L = arc[-1] or 1.0
        stops = []
        for j in jams:
            if idx >= int(j.get("n", 10)):
                continue                      # 그 병목에 안 끼는 사람
            d = [math.hypot(q["x"] - j["x"], q["y"] - j["y"]) * mpp for q in ks]
            i = int(np.argmin(d))
            if d[i] > j["r"] * 3:
                continue                      # 그 사람 길에서 먼 병목
            stops.append((arc[i], i, j))
        stops.sort()

        out, prev_i, t_cur = [], 0, float(t_start)
        t_end = float(t_pass[pid])
        shift = float(t_pass[pid]) - t_mid
        for sn, (a_m, i, j) in enumerate(stops):
            j0, j1 = float(j["t0"]) + shift, float(j["t1"]) + shift
            if j0 < t_start:
                j0, j1 = t_start, max(t_start + 0.5, j1)
            if j0 < t_cur:
                j0 = t_cur
            # 찍어 준 병목이 그 사람의 통과 시각을 넘을 수 있다 — 문 앞 병목이
            # 그렇다(47~55s 인데 통과는 44~49s). 사람마다 **자기 차례에** 빠져
            # 나가야 하므로 거기서 끊는다.
            last = sn == len(stops) - 1
            lim = t_end - (0.4 if last else 0.3)
            if last and door_times and idx < len(door_times):
                # 문 앞에 **닿는 시각**을 직접 준 경우 — 원 가장자리에서 조각
                # 자리(원 중심 쪽)까지 1.3초 더 걸어 들어간다. 나머지 시간은
                # 전부 그 자리에서 머뭇거린다. 뒤로 갈수록 오래 머문다 =
                # 병목이 커지는 모습.
                j0, j1 = float(door_times[idx]) + 1.3, lim
                if j1 <= j0:
                    j0 = max(t_cur + 0.4, j1 - 0.5)
            if j1 > lim:
                # **창을 그 사람 통과 직전으로 옮긴다.** 그냥 자르면 j1 < j0 이
                # 되어 통째로 스킵되고, 문 앞에서 안 멈추고 바로 나가 버린다.
                # 길이는 실측(문 앞 체류 중앙 3.0s)을 넘지 않게 줄인다.
                j1 = lim
                j0 = max(t_cur + 0.4, j1 - min(float(j["t1"]) - float(j["t0"]),
                                               TAIL_S))
            if j1 <= j0:
                j1 = min(j0 + 0.5, lim)
            if j1 <= j0:
                continue
            # ① 여기까지 걸어온다 — 구간 거리 ÷ 구간 시간이 속도다.
            #    걸어올 시간이 없으면(첫 병목이 0초부터면) 그냥 **거기서 시작**한다.
            #    억지로 욱여넣으면 그 한 칸이 순간이동이 된다(실측 3.9 m/s).
            if last and j0 - t_cur > 0.4:
                # 문까지 **보통 걸음으로** 간다. 남은 시간을 다 쓰면 0.76 m/s 로
                # 느릿느릿 가게 되는데, 느릴수록 문 앞 원에 **더 일찍** 닿는다
                # (선두가 39초에 도착해 버렸다). 실측은 병목2 를 늦게 뜨고 보통
                # 걸음으로 가 42.4s 에 닿았다 — 남는 시간은 앞 병목에 머문다.
                need = (arc[i] - arc[prev_i]) / V_APP
                t_cur = max(t_cur, j0 - need)
            if j0 - t_cur >= 0.4:
                # 앞 구간이 이미 그 점을 넣었으면 건너뛴다 — 같은 자리를 두 번
                # 넣으면 0.05초 간격으로 밀리며 순간이동이 된다(실측 38 m/s).
                s0 = prev_i + 1 if out else prev_i
                seg = ks[s0:i + 1]
                sa = arc[s0:i + 1] - arc[prev_i]
                span = (arc[i] - arc[prev_i]) or 1.0
                for q, am in zip(seg, sa):
                    out.append({"t": round(t_cur + (j0 - t_cur) * am / span, 2),
                                "x": q["x"], "y": q["y"]})
            elif not out:
                out.append({"t": round(j0, 2),
                            "x": round(j["x"], 1), "y": round(j["y"], 1)})
            # ② 병목 안에서 **실측이 서성인 그대로** 움직인다.
            #    조각은 그 반경 안에서 실제로 잡힌 트랙이다 — 만들어 내지 않는다.
            segs = (segs_per_jam or {}).get(id(j)) or []
            if segs:
                seg = segs[idx % len(segs)]
                # 같은 조각을 여러 사람이 쓰면 겹친다 — 조금씩 떨어뜨린다
                kk = idx // max(1, len(segs))
                ang = (idx * 2.399) % (2 * math.pi)
                off = (0.0, 0.0) if kk == 0 else (
                    math.cos(ang) * 0.6 / mpp, math.sin(ang) * 0.6 / mpp)
                wp = [(t, x + off[0], y + off[1])
                      for t, x, y in _wander(seg, j0, max(j0 + 0.2, j1 - 0.4))]
                # **앞뒤를 조각 자리에 맞춘다.** 경로 지점과 조각 자리가 떨어져
                # 있는데 0.05초에 건너가려 하면 그 한 칸이 순간이동이 된다
                # (실측 38 m/s). 걸어오는 끝점을 조각 시작 자리로 바꾼다.
                if out:
                    out[-1]["x"], out[-1]["y"] = round(wp[0][1], 1), round(wp[0][2], 1)
                for t, x, y in wp:
                    out.append({"t": round(t, 2), "x": round(x, 1), "y": round(y, 1)})
                # 나가는 쪽도 조각의 **마지막 자리**에서 시작한다
                out.append({"t": round(j1, 2),
                            "x": round(wp[-1][1], 1), "y": round(wp[-1][2], 1)})
            else:
                out.append({"t": round(j1, 2), "x": ks[i]["x"], "y": ks[i]["y"]})
            t_cur, prev_i = j1, i
        # ③ 마지막 병목 ~ 통과
        s0 = prev_i + 1 if out else prev_i
        seg = ks[s0:]
        sa = arc[s0:] - arc[prev_i]
        span = (arc[-1] - arc[prev_i]) or 1.0
        for q, am in zip(seg, sa):
            out.append({"t": round(t_cur + (t_end - t_cur) * am / span, 2),
                        "x": q["x"], "y": q["y"]})
        for a, b in zip(out, out[1:]):
            b["t"] = max(b["t"], a["t"] + 0.05)
        people[pid] = out

        # 구간별 걷는 속도(병목 사이) — 눈으로 확인하라고 찍어 준다
        segs, k0 = [], 0
        marks = [0.0] + [st[0] for st in stops] + [L]
        tms = [t_start] + [float(j["t1"]) for _a, _i, j in stops] + [t_end]
        t0s = [t_start] + [float(j["t0"]) for _a, _i, j in stops]
        for n in range(len(marks) - 1):
            dd = marks[n + 1] - marks[n]
            dt_ = (t0s[n + 1] if n + 1 < len(t0s) else t_end) - tms[n]
            if dd > 0.5 and dt_ > 0.2:
                segs.append(dd / dt_)
        rows.append(f"{pid} 병목 {len(stops)}개 · 걷는속도 "
                    + "/".join(f"{x:.2f}" for x in segs) + " m/s")
    return rows


def _same_side(q, p, u) -> bool:
    """가던 방향(u)으로 가면 출구선 최근접점(p)을 아직 지나야 하는가."""
    return (p[0] - q["x"]) * u[0] + (p[1] - q["y"]) * u[1] > 0


def extend_to_exit(people: dict, t_pass: dict, mpp: float, exits,
                    over_m: float = 0.6, v: float = 1.1):
    """경로 끝을 **출구선 너머**까지 늘린다.

    손으로 찍은 마지막 점은 문 **앞**이다(실측 0.7~1.5m 앞, 문 앞 병목 원 안).
    거기서 점이 사라지면 아직 문에 닿지도 않았는데 카운트가 오른 것처럼 보인다.
    그래서 출구선 위 최근접점을 지나 {over_m}m 더 간 자리를 통과 시각에 놓고,
    직전 점(문 앞)은 거기서 걸어갈 만큼 앞당긴다.
    """
    n = 0
    for pid, ks in people.items():
        q = ks[-1]
        best = None
        for (ax, ay), (bx, by) in exits:
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy or 1.0
            u = max(0.0, min(1.0, ((q["x"] - ax) * dx + (q["y"] - ay) * dy) / L2))
            px, py = ax + u * dx, ay + u * dy
            d = math.hypot(q["x"] - px, q["y"] - py)
            if best is None or d < best[0]:
                best = (d, px, py)
        d_px, px, py = best
        if d_px < 1e-6:
            continue
        # **가던 방향으로** 더 간다. 출구선 쪽으로 밀면, 이미 선을 넘어 서 있는
        # 사람을 도로 안으로 되돌린다 — 12 SEI 가 그랬다(찍어 준 끝점이 이미
        # 선 너머라, 연장이 전원을 안쪽으로 끌어와 통과가 2명만 잡혔다).
        prev = ks[-2] if len(ks) >= 2 else None
        vx, vy = ((q["x"] - prev["x"], q["y"] - prev["y"]) if prev else
                  (px - q["x"], py - q["y"]))
        vn = math.hypot(vx, vy)
        if vn < 1e-6:
            vx, vy, vn = px - q["x"], py - q["y"], d_px
        ux, uy = vx / vn, vy / vn
        # 아직 선 앞이면 선까지 거리를 더해 확실히 넘긴다
        ahead = d_px * mpp if _same_side(q, (px, py), (ux, uy)) else 0.0
        tx = q["x"] + ux * (ahead + over_m) / mpp
        ty = q["y"] + uy * (ahead + over_m) / mpp
        t_end = float(t_pass.get(pid, q["t"]))
        dist = math.hypot(tx - q["x"], ty - q["y"]) * mpp
        # 앞 점보다 이르게 당기면 **시간이 역행**한다(실측 43.97 → 42.15,
        # 그 자리가 7.4 m/s 로 튀었다). 바로 앞 점 뒤로만 당긴다.
        floor_t = ks[-2]["t"] + 0.1 if len(ks) >= 2 else 0.0
        q["t"] = round(max(floor_t, min(q["t"], t_end - dist / v)), 2)
        ks.append({"t": round(t_end, 2), "x": round(tx, 1), "y": round(ty, 1)})
        # 통과 시각은 실측이라 못 옮긴다 — 대신 **앞쪽을 역순으로 당겨** 순서를
        # 맞춘다. 문 앞 멈칫이 0.1~0.3초 줄 뿐이다.
        for k in range(len(ks) - 2, -1, -1):
            if ks[k]["t"] >= ks[k + 1]["t"]:
                ks[k]["t"] = round(ks[k + 1]["t"] - 0.05, 2)
        n += 1
    return n


def constant_walk(people: dict, order, t_pass: dict, mpp: float, exits,
                  v: float = 0.9, t_start: float = 6.0, door_m: float = 2.5,
                  pass_s: float = 2.0):
    """**일정한 속도로 걷다 문 앞에서 선다.** 빨라지는 구간이 없다.

    멈춤을 많이 넣을수록 걷는 구간이 빨라진다 — 경로 길이와 통과 시각이 묶여
    있기 때문이다(03 CBS: 31.2m ÷ 46s). 멈춤 24초를 넣었더니 걸을 때 1.02 m/s
    가 됐다(실측 0.81). 그래서 거꾸로 간다.

      ① 출발 전 — 시작 자리에서 뭉쳐 기다린다(0 ~ t_start)
      ② 이동   — **줄곧 같은 속도** v 로 문 앞 {door_m}m 까지. 빨라질 일이 없다
      ③ 문 앞  — 자기 통과 시각까지 선다. 먼저 온 사람일수록 오래 선다 = 밀집
      ④ 통과   — 마지막 {pass_s}초에 문을 지난다

    시간이 모자라면 그 사람만 속도를 올린다(아래 표에 표시된다).
    """
    import numpy as np
    rows = []
    for _k, pid in sorted(order, key=lambda z: t_pass[z[1]]):
        ks = people[pid]
        d = [min(dt_seg(q, e, mpp) for e in exits) for q in ks]
        i_in = next((i for i, x in enumerate(d) if x <= door_m), len(ks) - 2)
        i_in = max(2, i_in)
        arc = np.concatenate([[0.0], np.cumsum(
            [math.hypot(b["x"] - a["x"], b["y"] - a["y"]) * mpp
             for a, b in zip(ks, ks[1:])])])
        L_in = arc[i_in]
        # 문 앞~출구 구간도 **같은 속도로** 걷게 한다. 2초에 몰아 넣으면 거기서만
        # 2.4 m/s 로 튄다(문 앞에서 왔다갔다 그린 사람은 그 구간이 길다).
        span_m = arc[-1] - arc[i_in]
        ps = max(pass_s, span_m / v)
        t_go = t_pass[pid] - ps
        vv = v
        if t_start + L_in / vv > t_go:            # 시간이 모자라면 그만큼만 빠르게
            vv = L_in / max(1.0, t_go - t_start)
        t_arr = t_start + L_in / vv
        for i in range(i_in + 1):
            ks[i]["t"] = round(t_start + arc[i] / vv, 2)
        for i in range(i_in + 1):                 # 출발 전엔 제자리
            if ks[i]["t"] < t_start:
                ks[i]["t"] = round(t_start, 2)
        ks.insert(0, {"t": 0.0, "x": ks[0]["x"], "y": ks[0]["y"]})
        i_in += 1
        span = (arc[-1] - arc[i_in - 1]) or 1.0
        for i in range(i_in, len(ks)):
            f = (arc[i - 1] - arc[i_in - 1]) / span
            ks[i]["t"] = round(t_go + ps * f, 2)
        for a, b in zip(ks, ks[1:]):
            b["t"] = max(b["t"], a["t"] + 0.05)
        people[pid] = ks
        rows.append(f"{pid} 출발 {t_start:.0f}s · 문앞 {t_arr:.1f}s · "
                    f"대기 {max(0.0, t_go - t_arr):.1f}s · {vv:.2f} m/s")
    return rows


def even_speed(people: dict, mpp: float, cap: float = 1.25, rounds: int = 40):
    """**빨라지는 구간만** 눌러 준다. 느려지는 건 그대로 둔다.

    사람은 일정하게 걷다 느려질 수는 있어도 갑자기 빨라지지 않는다. 그런데 손으로
    찍으면 클릭 간격이 고르지 않아 어떤 구간만 튄다(실측: 중앙 0.5 m/s 인데
    최대 2.2). 자리는 그대로 두고 **시간만** 옮긴다 — 빠른 구간에 시간을 더 주고
    그만큼 느린 구간에서 뺀다. 양 끝과 총 시간은 건드리지 않는다.
    """
    import numpy as np
    fixed = 0
    for pid, ks in people.items():
        if len(ks) < 4:
            continue
        d = np.array([math.hypot(b["x"] - a["x"], b["y"] - a["y"]) * mpp
                      for a, b in zip(ks, ks[1:])])
        dt = np.array([b["t"] - a["t"] for a, b in zip(ks, ks[1:])], float)
        dt = np.maximum(dt, 0.05)
        total = dt.sum()
        v_max = float(np.median(d / dt)) * cap
        if v_max <= 0:
            continue
        for _ in range(rounds):
            need = d / v_max
            over = need > dt + 1e-6
            if not over.any():
                break
            short = float((need[over] - dt[over]).sum())
            dt[over] = need[over]
            slack = dt.copy()
            slack[over] = 0.0
            room = float(np.maximum(slack - 0.1, 0).sum())
            if room <= 1e-6:
                break
            take = np.minimum(np.maximum(slack - 0.1, 0) * (short / room),
                              np.maximum(slack - 0.1, 0))
            dt = dt - take
        dt = dt * (total / dt.sum())            # 총 시간은 그대로
        t = ks[0]["t"]
        for k, step in zip(ks[1:], dt):
            t += float(step)
            k["t"] = round(t, 2)
        fixed += 1
    return fixed


def speed_check(sid: str, floor: str | None, people: dict, jump: float = 1.4,
                hard: float = 1.5):
    """**빨라지는 구간**을 찾는다.

    사람은 일정하게 걷다 느려질 수는 있어도 갑자기 빨라지지 않는다. 손으로 찍은
    시각이 성기면 어떤 구간만 튀는데, 눈으로는 잘 안 보인다.
    """
    db, fl = find_session(sid, floor)
    site = SiteConfig.model_validate(recorder.load_meta(db)["site_view"])
    mpp = site.map.resolve_m_per_px()
    import numpy as np
    bad = []
    for pid, ks in sorted(people.items()):
        vs = []
        for a, b in zip(ks, ks[1:]):
            dtt = b["t"] - a["t"]
            if dtt <= 0.05:
                continue
            vs.append(math.hypot(b["x"] - a["x"], b["y"] - a["y"]) * mpp / dtt)
        if len(vs) < 3:
            continue
        med = float(np.median(vs))
        # 절대 기준으로 본다. 중앙값 배수로 보면 병목에서 서성이는 사람
        # (중앙 0.15 m/s)에게 0.2 m/s 도 '빠르다'가 된다.
        hi = [v for v in vs if v > hard]
        if hi:
            bad.append(f"{pid}({len(hi)}구간 최대 {max(hi):.1f})")
        print(f"  {pid:5} 중앙 {med:.2f} m/s · 최대 {max(vs):.2f}"
              + (f"  ⚠ {len(hi)}구간이 {hard} m/s 초과" if hi else "  ✅"))
    if bad:
        print("  빨라지는 구간: " + " · ".join(bad))
    else:
        print("  ✅ 갑자기 빨라지는 구간 없음")


def passage_times(sid: str, floor: str | None):
    """**출구별** 통과 시각. 출구가 둘이면 한 줄로 섞으면 안 된다 — 왼쪽 문으로
    나간 사람에게 오른쪽 문 통과 시각을 주게 된다(04 SEI 가 그렇다)."""
    db, fl = find_session(sid, floor)
    meta = recorder.load_meta(db)
    t0 = float(meta["alarm_ts"])
    gh = [g["key"] for g in dghost.load(sid)]
    _, tl, _, _ = run_replay(db, {"exclude_tracks": gh} if gh else {}, fps=5.0)
    out, prev = {}, {}
    for p in tl:
        d = p.model_dump() if hasattr(p, "model_dump") else p
        for k, v in (d.get("exit_counts") or {}).items():
            n = v - prev.get(k, 0)
            if n > 0:
                try:
                    i = int(str(k).rsplit("-", 1)[-1])
                except ValueError:
                    continue
                out.setdefault(i, []).extend([round(d["ts"] - t0, 1)] * n)
                prev[k] = v
    for i in out:
        out[i].sort()
    return out, fl


def main() -> int:
    ap = argparse.ArgumentParser(description="그린 동선 → 실제 통과 시각에 정렬")
    ap.add_argument("--session", required=True)
    ap.add_argument("--floor")
    ap.add_argument("--apply", action="store_true", help="파일에 반영(원본은 .bak)")
    ap.add_argument("--no-pace", action="store_true",
                    help="실측 걸음 리듬을 쓰지 않고 단순 비례로만 늘인다")
    ap.add_argument("--check", action="store_true",
                    help="**실측 dot 와 얼마나 맞나** 만 재 본다(고치지 않는다). "
                         "찍은 시각이 이미 맞는지, 통과 시각에 늘이는 게 나은지 "
                         "이 수치로 고른다.")
    ap.add_argument("--formation", action="store_true",
                    help="무리가 **같은 대형으로** 움직이게 한다. 진행 곡선을 무리끼리 "
                         "공유하고, 벌어지는 간격은 실제 통과 시각 차이 그대로.")
    ap.add_argument("--exit-over", type=float, default=0.6,
                    help="출구선을 **얼마나 더** 지나 끝낼지(m). 평활화·장애물 "
                         "비껴가기가 끝점을 되밀어 선을 못 넘는 사람이 생기면 키운다")
    ap.add_argument("--use-exits", default="",
                    help="출구선 연장에 **쓸 출구만** 지정(콤마). 안 쓰는 출구가 더 "
                         "가까우면 점이 그쪽으로 뻗어 통과가 안 잡힌다")
    ap.add_argument("--fit-speed", type=float, default=0.0,
                    help="--exit-times 와 같이 — 이 속도로 걷도록 **나오는 시각을 "
                         "앞당긴다**(12 SEI 실측 걸음 0.74 m/s)")
    ap.add_argument("--door-times", default="",
                    help="문 앞에 **닿는** 시각들(콤마, 통과 순서대로). 실측이 아니라 "
                         "연출로 정할 때 쓴다 — 44초에 선두가 코앞, 50초에 아직 몰려 있게")
    ap.add_argument("--exit-times", default="",
                    help="문을 **통과하는** 시각들(콤마, 순서대로). 이걸 주면 실측 "
                         "통과 시각 대신 이 값으로 맞춘다")
    ap.add_argument("--jams", action="store_true",
                    help="편집기(:8930)에서 찍어 준 **병목대로** 시각을 매긴다. "
                         "병목 사이는 일정한 속도로 걷고 병목 안에서는 선다.")
    ap.add_argument("--walk", type=float, default=0.0,
                    help="**일정 속도로 걷다 문 앞에서 선다**(m/s). 빨라지는 구간이 "
                         "없다. 0.9 쯤이 실측(0.81)에 가깝다.")
    ap.add_argument("--walk-start", type=float, default=6.0,
                    help="--walk 에서 움직이기 시작하는 시각(초). 그 전엔 시작 "
                         "자리에서 뭉쳐 기다린다.")
    ap.add_argument("--even-speed", action="store_true",
                    help="**빨라지는 구간만** 눌러 준다(느려지는 건 그대로). 자리는 "
                         "두고 시간만 옮긴다 — 총 시간과 양 끝은 그대로.")
    ap.add_argument("--gain", type=float, default=1.0,
                    help="완급 대비. 1=실측 그대로, 2 이상이면 느린 구간이 더 "
                         "느려지고 빠른 구간이 더 빨라진다(평균은 그대로).")
    ap.add_argument("--profile", action="store_true",
                    help="**실측 속도 곡선의 모양**을 따른다 — 실측이 느릴 때 느리고 "
                         "빠를 때 빠르다. 한 구간만 멈추는 --pause 보다 낫다.")
    ap.add_argument("--pause", action="store_true",
                    help="실측에서 **다들 멈춰 있던 때**를 찾아 그만큼 멈춰 세운다. "
                         "속도가 아니라 멈춤이 빠져서 빨라 보이는 경우에 쓴다.")
    ap.add_argument("--hold", action="store_true",
                    help="문 앞에서 **잠깐 멈춰 밀집**하게 한다 — 먼저 도착한 사람이 "
                         "자기 통과 시각까지 서 있다가 차례로 빠져나간다.")
    ap.add_argument("--keep-time", action="store_true",
                    help="**찍은 시각을 그대로 둔다.** 퇴장만 각자의 마지막 시각으로 "
                         "박는다. 편집기에서 실측에 맞춰 찍었다면 그 시각이 이미 "
                         "정답이다 — 통과 시각에 억지로 늘이면 오히려 어긋난다.")
    a = ap.parse_args()

    p = dp.path(a.session)
    if not p.is_file():
        print(f"그린 동선이 없습니다 — {p}")
        return 1
    raw = json.loads(p.read_text(encoding="utf-8"))
    people = raw.get("people") or {}
    if a.check:
        for lab, f in (("현재", p), ("원본(.bak)", p.with_suffix(".json.bak"))):
            if not f.is_file():
                continue
            d = json.loads(f.read_text(encoding="utf-8")).get("people") or {}
            r = match_rate(a.session, a.floor, d)
            if r:
                print(f"  {lab:12} 실측 일치 {r[0]:3.0f}% ({r[1]}/{r[2]}) · "
                      f"끝 {r[3]:.0f}~{r[4]:.0f}s")
        speed_check(a.session, a.floor, people)
        return 0
    want, floor = passage_times(a.session, a.floor)
    for i, v in sorted(want.items()):
        print(f"실제 통과 시각 exit-{i}: {v}")

    db, _ = find_session(a.session, floor)
    site = SiteConfig.model_validate(recorder.load_meta(db)["site_view"])
    mpp = site.map.resolve_m_per_px()
    exits = [[tuple(e.line[0]), tuple(e.line[1])] for e in site.exits if e.line]

    # 누가 **어느 문으로** 나갔나 — 마지막 점에서 가장 가까운 출구
    import numpy as np
    from tools import demo_tracks as dtk
    by_exit: dict[int, list[str]] = {}
    for pid, ks in people.items():
        q = (ks[-1]["x"], ks[-1]["y"])
        k = int(np.argmin([dtk._seg_dist(q, e[0], e[1]) for e in exits]))
        by_exit.setdefault(k, []).append(pid)
    for k in by_exit:
        by_exit[k].sort(key=lambda pid: people[pid][-1]["t"])
        if len(want.get(k, [])) < len(by_exit[k]):
            print(f"exit-{k}: 통과 시각 {len(want.get(k, []))}건 < 사람 {len(by_exit[k])}명")
            return 1
    print("\n문별 배정: " + " · ".join(
        f"exit-{k} {len(v)}명" for k, v in sorted(by_exit.items())))
    order = [(k, pid) for k in sorted(by_exit) for pid in by_exit[k]]

    exit_at = dict(raw.get("exit_at") or {})
    print(f"\n{'':5} {'문':>2} {'찍은 끝':>6} {'→통과':>6} {'늘임':>6} {'속도':>13}")
    if a.keep_time:
        # 찍은 시각이 곧 싱크다. 실측 dot 와의 일치율로 재 보면 손대는 쪽이
        # 오히려 나쁠 때가 있다(실측: 03 CBS 80% → 75%).
        exit_at = {}
        print(f"\n{'':5} {'끝s':>6} {'점':>4}  (시각 그대로, 퇴장만 박음)")
        for pid in sorted(people, key=lambda k: people[k][-1]["t"]):
            ks = people[pid]
            exit_at[pid] = float(ks[-1]["t"])
            print(f"{pid:5} {ks[-1]['t']:6.1f} {len(ks):4}")
        raw["exit_at"] = exit_at
        if a.apply:
            shutil.copy2(p, p.with_suffix(".json.bak2"))
            p.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"\n반영 → {p.relative_to(ROOT)}")
        else:
            print("\n--apply 를 붙이면 파일에 반영합니다")
        return 0

    table = None if a.no_pace else pace_table(a.session, floor)
    if table:
        print(f"\n실측 걸음 리듬: 표본 {table['n']} · 남은거리 {table['r'][-1]:.0f}m 까지 "
              + " ".join(f"{r:.0f}m→{t:.0f}s" for r, t in
                         zip(table["r"][1::6], table["t"][1::6])))
    # 무리마다 **원래 걷던 속도**(중앙값). 문 앞 밖은 이 속도로 간다 — 무리가
    # 같은 속도를 쓰니 한 명만 앞장서는 일도 없어진다.
    import numpy as _np
    v_grp = {}
    for k, pids in by_exit.items():
        vs = []
        for pid in pids:
            kk = people[pid]
            L = sum(math.hypot(b["x"] - c["x"], b["y"] - c["y"])
                    for c, b in zip(kk, kk[1:])) * mpp
            vs.append(L / max(0.1, kk[-1]["t"] - kk[0]["t"]))
        v_grp[k] = float(_np.median(vs))
    print("무리 속도: " + " · ".join(f"exit-{k} {v:.2f} m/s" for k, v in sorted(v_grp.items())))
    seen: dict[int, int] = {}
    for k_ex, pid in order:
        i = seen.get(k_ex, 0)
        seen[k_ex] = i + 1
        tgt = want[k_ex][i]
        ks = people[pid]
        t_a, t_b = ks[0]["t"], ks[-1]["t"]
        ks_t0 = t_a
        if tgt <= t_a:
            print(f"{pid:5} 통과 {tgt}s 가 출발 {t_a}s 보다 빠릅니다 — 출발을 0 으로 당깁니다")
            ks[0]["t"] = t_a = max(0.0, tgt - 3.0)
        L = sum(math.hypot(b["x"] - c["x"], b["y"] - c["y"])
                for c, b in zip(ks, ks[1:])) * mpp
        v0 = L / max(0.1, t_b - t_a)
        # 늘일 여유가 없어 너무 빨라지면 **출발을 당긴다.** 각본상 10명은 경보
        # 순간부터 그 자리에 있었으니 일찍 나서는 건 이상하지 않다 — 반대로
        # 2.8 m/s 로 달리는 건 이상하다.
        need = L / V_MAX
        if tgt - t_a < need:
            t_a = max(0.0, tgt - need)
            ks[0]["t"] = min(ks[0]["t"], t_a)
        r = (tgt - t_a) / max(0.1, t_b - t_a)
        base = ks[0]["t"]
        if table:
            nts, cut = apply_pace(ks, table, tgt, mpp, v_grp[k_ex])
            ks = ks[cut:]
            people[pid] = ks
            for q, nt in zip(ks, nts):
                q["t"] = round(nt, 2)
            base = ks[0]["t"]
            t_b = ks[-1]["t"]
        else:
            span = max(0.1, t_b - base)
            r = (tgt - base) / span
            for q in ks:
                q["t"] = round(base + (q["t"] - base) * r, 2)
        exit_at[pid] = float(tgt)
        print(f"{pid:5} e{k_ex} {t_b:6.1f} {tgt:6.1f} {r:6.2f}x "
              f"{v0:5.2f} → {L / max(0.1, tgt - base):4.2f} m/s"
              + (f"  (출발 {base:.1f}s 로 당김)" if base < ks_t0 else ""))

    if a.formation:
        notes = formation(people, order,
                          {pid: exit_at[pid] for _, pid in order}, mpp)
        print("\n대형 맞춤: " + " · ".join(notes))
        for pid in exit_at:
            exit_at[pid] = float(people[pid][-1]["t"])
    if a.exit_times and not a.jams:
        ets = [float(x) for x in a.exit_times.split(",") if x.strip()]
        rows = fit_exit_times(people, order, exit_at, ets, mpp,
                              v_target=a.fit_speed)
        print("\n통과 시각 맞춤:\n  " + "\n  ".join(rows))
        # 문 앞에서 끝나면 **점이 문을 넘지 않는다** — 그러면 출구 카운트가 0 이
        # 되고 SEI 가 아예 산출되지 않는다(12 SEI 가 그랬다. 끝점이 출구선에서
        # 1.75~3.04m 앞이었다).
        use = [x.strip() for x in a.use_exits.split(",") if x.strip()]
        ex_use = ([[tuple(e.line[0]), tuple(e.line[1])] for e in site.exits
                   if e.line and e.id in use] if use else exits)
        n = extend_to_exit(people, {pid: exit_at[pid] for _, pid in order},
                           mpp, ex_use, over_m=a.exit_over)
        print(f"  출구선 너머로 연장: {n}명"
              + (f" (출구 {use} 만)" if use else ""))
        for pid in exit_at:
            exit_at[pid] = float(people[pid][-1]["t"])
        n = clamp_speed(people, mpp, keep_last=True)
        print(f"  속도 상한(1.5 m/s) 적용: {n}점 당김 (출구 너머 점은 그대로)")

    if a.jams:
        jm = load_jams(a.session)
        if not jm:
            print("찍어 준 병목이 없습니다 — :8930 에서 [+ 여기서 시작] 으로 찍으세요")
            return 1
        print("\n찍어 준 병목: " + " · ".join(
            f"{j['t0']:.0f}~{j['t1']:.0f}s {j['n']}명 반경 {j['r']}m" for j in jm))
        dts = [float(x) for x in a.door_times.split(",") if x.strip()] or None
        ets = [float(x) for x in a.exit_times.split(",") if x.strip()] or None
        if ets:
            for n, (_k, pid) in enumerate(sorted(order, key=lambda z: exit_at[z[1]])):
                if n < len(ets):
                    exit_at[pid] = ets[n]
            print("  통과 시각 지정: " + " ".join(f"{x:.1f}" for x in ets))
        if dts:
            print("  문앞 도착 지정: " + " ".join(f"{x:.1f}" for x in dts))
        segl = jam_segments(a.session, a.floor, jm, mpp)
        print("  실측 조각: " + " · ".join(
            f"{j['t0']:.0f}~{j['t1']:.0f}s {len(sg)}개" for j, sg in zip(jm, segl)))
        rows = apply_jams(people, order,
                          {pid: exit_at[pid] for _, pid in order}, mpp, jm,
                          v=a.walk or 0.9, t_start=0.0,
                          segs_per_jam={id(j): sg for j, sg in zip(jm, segl)},
                          door_times=dts)
        print("  " + "\n  ".join(rows))
        for pid in exit_at:
            exit_at[pid] = float(people[pid][-1]["t"])
        if not a.even_speed:
            print("  (병목 사이 구간마다 속도가 달라진다 — `--even-speed` 를 같이 "
                  "쓰면 튀는 구간을 눌러 준다)")

    if a.walk > 0 and not a.jams:
        rows = constant_walk(people, order,
                             {pid: exit_at[pid] for _, pid in order}, mpp, exits,
                             v=a.walk, t_start=a.walk_start)
        print("\n일정 속도 걷기:\n  " + "\n  ".join(rows))
        for pid in exit_at:
            exit_at[pid] = float(people[pid][-1]["t"])

    if a.hold:

        pf = speed_profile(a.session, a.floor) if a.profile else None
        if pf is not None and a.gain != 1.0:
            pf = sharpen(pf, a.gain)
        if pf is not None:
            v = pf[1]
            print("\n실측 속도 곡선(1초): "
                  + " ".join(f"{int(t)}s{v[i]:.2f}" for i, t in enumerate(pf[0][:20:2])))
        zs = find_pauses(pf) if (pf is not None and a.pause) else None
        if zs:
            print("실측에서 다들 느렸던 때: "
                  + " · ".join(f"{a1:.0f}~{b1:.0f}s" for a1, b1 in zs)
                  + f"  (합 {sum(b1 - a1 for a1, b1 in zs):.0f}초)")
        notes = hold_at_door(people, order,
                             {pid: exit_at[pid] for _, pid in order}, mpp, exits,
                             pause=zs, prof=None)
        print("\n문 앞 대기:\n  " + "\n  ".join(notes))
        for pid in exit_at:
            exit_at[pid] = float(people[pid][-1]["t"])

    if getattr(a, "even_speed", False):
        n = even_speed(people, SiteConfig.model_validate(
            recorder.load_meta(find_session(a.session, a.floor)[0])["site_view"]
        ).map.resolve_m_per_px())
        print(f"가속 눌러줌: {n}명 (자리는 그대로, 시간만 옮김)")

    if a.jams:
        n = extend_to_exit(people, {pid: exit_at[pid] for _, pid in order},
                           mpp, exits)
        print(f"출구선 너머로 연장: {n}명 (문 앞에서 사라지지 않게)")
        for pid in exit_at:
            exit_at[pid] = float(people[pid][-1]["t"])
        n = clamp_speed(people, mpp)
        print(f"속도 상한(1.5 m/s) 적용: {n}점 당김")

    raw["exit_at"] = exit_at
    raw["paced"] = bool(a.profile or a.pause or a.hold or a.formation)
    if a.apply:
        shutil.copy2(p, p.with_suffix(".json.bak"))
        p.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n반영 → {p.relative_to(ROOT)} (원본 {p.name}.bak)")
    else:
        print("\n--apply 를 붙이면 파일에 반영합니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
