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


def path(session_id: str) -> Path:
    return ANN / f"paths_{session_id}.json"


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


def load(session_id: str, t_end: float | None = None) -> dict | None:
    """편집기 파일 → demo_viz 가 쓰는 궤적 묶음(demo_tracks 와 같은 모양)."""
    p = path(session_id)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    exits = raw.get("exit_at") or {}
    people = []
    for pid, ks in sorted((raw.get("people") or {}).items()):
        ks = sorted(ks, key=lambda k: k["t"])
        if len(ks) < 2:
            continue
        t_a = ks[0]["t"]
        t_b = ks[-1]["t"]
        ex = exits.get(pid)
        if ex is not None:
            t_b = min(t_b, float(ex))      # 문을 지나면 거기서 사라진다
        if t_b <= t_a:
            continue
        grid = np.arange(t_a, t_b + DT * 0.5, DT)
        xy = _catmull(ks, grid)
        people.append({"id": pid, "t0": float(t_a), "exit_at": ex,
                       "xy": [[round(float(a), 1), round(float(b), 1)] for a, b in xy]})
    if not people:
        return None
    end = t_end if t_end is not None else max(
        q["t0"] + len(q["xy"]) * DT for q in people)
    return {"n": len(people), "t_end": float(end), "dt": DT,
            "people": people, "authored": True,
            "hub": people[0]["xy"][0] if people else [0, 0]}


def summary(data: dict) -> str:
    n = len(data["people"])
    keys = sum(len(q["xy"]) for q in data["people"])
    return f"사람이 그린 동선 {n}명 · 표본 {keys}개"
