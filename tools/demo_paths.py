# -*- coding: utf-8 -*-
"""**사람이 그린 동선** 읽기 — 편집기(:8930)가 남긴 키프레임 → 재생 가능한 궤적.

키프레임은 `(시각, 위치)` 쌍이다. 위치만 받으면 속도를 알 수 없고, 속도를 이쪽에서
지어내는 순간 그리드 영상과 다시 어긋난다. 그래서 편집기는 **재생하면서** 찍게
하고, 여기서는 찍힌 시각을 그대로 존중해 사이만 메운다.

사이 메우기는 **Catmull-Rom**이다 — 직선으로 이으면 꺾이는 자리마다 각이 져서
사람이 아니라 로봇처럼 보인다. 키프레임을 지나가는 곡선이라 찍은 자리는 그대로
지나간다(찍은 점을 배신하지 않는다).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ANN = ROOT / "results/demo/_annotate"

DT = 0.1
EVEN = 0.65            # 걸음 고르기 — 0=찍은 시각 그대로, 1=완전 등속
HOLD = True            # 경보 순간부터 첫 자리에 서 있게 한다


def path(session_id: str) -> Path:
    return ANN / f"paths_{session_id}.json"


def _even_times(ks, alpha=EVEN):
    """**걸음을 고른다** — 찍은 시각을 등속 쪽으로 섞는다.

    손으로 찍으면 클릭 간격이 고르지 않아 어떤 구간은 멀리, 어떤 구간은 가깝게
    찍힌다. 그대로 두면 그 구간만 4 m/s 로 달린다(실측). 시작·끝 시각은 **그대로
    두고** 사이만 호 길이에 비례한 시각 쪽으로 당긴다 — 싱크의 양 끝은 지키면서
    중간의 덜컥거림만 없앤다.
    """
    import math
    if len(ks) < 3:
        return [k["t"] for k in ks]
    t = [k["t"] for k in ks]
    arc = [0.0]
    for a, b in zip(ks, ks[1:]):
        arc.append(arc[-1] + math.hypot(b["x"] - a["x"], b["y"] - a["y"]))
    L = arc[-1] or 1.0
    t_a, t_b = t[0], t[-1]
    out = [t_a]
    for i in range(1, len(ks) - 1):
        u = t_a + (t_b - t_a) * arc[i] / L
        out.append((1 - alpha) * t[i] + alpha * u)
    out.append(t_b)
    for i in range(1, len(out)):                  # 순서가 뒤집히지 않게
        out[i] = max(out[i], out[i - 1] + 0.05)
    return out


def _catmull(ks, grid):
    """키프레임을 **지나가는** 곡선 위에서 grid 시각의 좌표를 뽑는다."""
    t = np.array([k["t"] for k in ks], float)
    P = np.array([[k["x"], k["y"]] for k in ks], float)
    if len(ks) < 3:
        return np.stack([np.interp(grid, t, P[:, 0]),
                         np.interp(grid, t, P[:, 1])], axis=1)
    # 양 끝을 한 번씩 복제해 처음·마지막 구간도 곡선이 되게 한다
    tt = np.concatenate([[t[0] - (t[1] - t[0])], t, [t[-1] + (t[-1] - t[-2])]])
    PP = np.vstack([P[0] + (P[0] - P[1]), P, P[-1] + (P[-1] - P[-2])])
    out = np.empty((len(grid), 2))
    seg = np.clip(np.searchsorted(t, grid, side="right") - 1, 0, len(t) - 2)
    for n, (g, i) in enumerate(zip(grid, seg)):
        j = i + 1                                   # 복제본 기준 인덱스
        span = (tt[j + 1] - tt[j]) or 1.0
        u = (g - tt[j]) / span
        p0, p1, p2, p3 = PP[j - 1], PP[j], PP[j + 1], PP[j + 2]
        u2, u3 = u * u, u * u * u
        out[n] = 0.5 * ((2 * p1) + (-p0 + p2) * u
                        + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u2
                        + (-p0 + 3 * p1 - 3 * p2 + p3) * u3)
    return out


def _smooth(xy, w=5):
    """0.5초 이동평균 — 클릭 위치의 미세한 떨림만 없앤다.

    Catmull-Rom 은 찍은 점을 **지나가므로** 손이 조금 흔들린 자리에서 곡선이
    잘게 흔들린다. 양 끝은 그대로 둔다(출발 자리와 문은 못 박아야 한다).
    """
    a = np.asarray(xy, float)
    if len(a) < w + 2:
        return a
    k = np.ones(w) / w
    h = w // 2
    sm = np.stack([np.convolve(np.pad(a[:, c], h, mode="edge"), k, "valid")
                   for c in (0, 1)], axis=1)
    sm[:h] = a[:h]
    sm[-h:] = a[-h:]
    return sm


def _avoid(polys, xy, m_per_px, max_m=1.5):
    """**장애물 위를 지나는 구간만** 옆으로 비껴 준다.

    왜 장애물만인가: 도면 벽선(문틀·계단 난간)까지 피하게 하면 문 앞 좁은 통로가
    통째로 막혀 점이 튄다(실측: 1.1 m/s 동선이 11.6 m/s). 그런데 그 자리는
    **실측 트랙이 실제로 지나간 자리**다 — 격자가 보수적일 뿐 사람이 못 가는
    곳이 아니다. 반면 의자·적치물 위를 지나는 건 한눈에 틀려 보인다.

    비킴량은 이웃과 섞어 **완만하게** 만든 뒤 한꺼번에 적용한다. 점마다 따로
    밀면 단차가 생기고, 단차가 곧 튐이다.
    """
    import cv2
    if not polys:
        return [[float(x), float(y)] for x, y in xy], 0
    a = np.asarray(xy, float)
    n = len(a)

    def inside(x, y):
        return any(cv2.pointPolygonTest(q, (float(x), float(y)), False) >= 0
                   for q in polys)

    nb = np.roll(a, -3, axis=0); nb[-3:] = a[-1]
    pv = np.roll(a, 3, axis=0); pv[:3] = a[0]
    d = nb - pv
    ln = np.hypot(d[:, 0], d[:, 1]); ln[ln < 1e-6] = 1.0
    nx, ny = -d[:, 1] / ln, d[:, 0] / ln

    step = 0.15 / m_per_px
    lim = int(max_m / 0.15)
    off = np.zeros(n)
    hit = 0
    for i in range(n):
        if not inside(*a[i]):
            continue
        hit += 1
        for kk in range(1, lim + 1):
            for sgn in (1, -1):
                if not inside(a[i][0] + nx[i] * step * kk * sgn,
                              a[i][1] + ny[i] * step * kk * sgn):
                    off[i] = step * kk * sgn
                    break
            if off[i]:
                break
    if not hit:
        return [[float(x), float(y)] for x, y in a], 0
    w = 11
    sm = np.convolve(np.pad(off, w // 2, mode="edge"), np.ones(w) / w, "valid")
    sgn = np.sign(off)
    sm = np.where(off != 0, np.where(sgn >= 0, np.maximum(sm, off),
                                     np.minimum(sm, off)), sm)
    a = a + np.stack([nx * sm, ny * sm], axis=1)
    return [[float(x), float(y)] for x, y in a], hit


def load(session_id: str, t_end: float | None = None, obstacles=None,
         m_per_px: float | None = None) -> dict | None:
    """편집기 파일 → demo_viz 가 쓰는 궤적 묶음(demo_tracks 와 같은 모양).

    obstacles 를 주면 그 위를 지나는 자리만 비켜 준다(그린 선은 그대로, 표출만).
    """
    import cv2
    polys = [np.array(o["points"], np.int32) for o in (obstacles or [])]
    p = path(session_id)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    exits = raw.get("exit_at") or {}
    # 시각을 이미 정교하게 매긴 파일은 **걸음 고르기를 끈다.** 등속 쪽으로 섞으면
    # 애써 넣은 완급(문 앞 대기·중간 정체)이 도로 뭉개진다.
    even = 0.0 if raw.get("paced") else EVEN
    people = []
    moved_total = [0]
    for pid, ks in sorted((raw.get("people") or {}).items()):
        ks = sorted(ks, key=lambda k: k["t"])
        if len(ks) < 2:
            continue
        ks = [dict(k) for k in ks]
        for k, nt in zip(ks, _even_times(ks, even)):
            k["t"] = nt
        t_a = ks[0]["t"]
        t_b = ks[-1]["t"]
        ex = exits.get(pid)
        if ex is not None:
            t_b = min(t_b, float(ex))      # 문을 지나면 거기서 사라진다
        if t_b <= t_a:
            continue
        grid = np.arange(t_a, t_b + DT * 0.5, DT)
        xy = _catmull(ks, grid)
        xy = _smooth(xy)                       # 손으로 찍은 떨림을 가볍게 편다
        if polys and m_per_px:
            xy, mv = _avoid(polys, xy, m_per_px)
            moved_total[0] += mv
            xy = _smooth(xy, 3)                # 비킨 자국을 한 번 더 편다
        out = [[round(float(a), 1), round(float(b), 1)] for a, b in xy]
        if HOLD and t_a > 0:
            # 각본상 10명은 경보 순간부터 그 자리에 있었다. 찍기 시작한 시각부터
            # 등장하면 도면이 텅 비어 있다가 하나씩 나타난다(실측: 0초에 2명).
            out = [list(out[0])] * int(round(t_a / DT)) + out
            t_a = 0.0
        people.append({"id": pid, "t0": float(t_a), "exit_at": ex, "xy": out})
    if not people:
        return None
    end = t_end if t_end is not None else max(
        q["t0"] + len(q["xy"]) * DT for q in people)
    return {"n": len(people), "t_end": float(end), "dt": DT, "moved": moved_total[0],
            "people": people, "authored": True,
            "hub": people[0]["xy"][0] if people else [0, 0]}


def summary(data: dict) -> str:
    n = len(data["people"])
    keys = sum(len(q["xy"]) for q in data["people"])
    mv = data.get("moved") or 0
    return (f"사람이 그린 동선 {n}명 · 표본 {keys}개"
            + (f" · 장애물 비켜감 {mv}점" if mv else " · 장애물 통과 없음"))
