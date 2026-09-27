# -*- coding: utf-8 -*-
"""**각본 연출** — 발표 영상의 점 움직임을 각본대로 짠다 (표출 전용).

앞 단계(`demo_tracks`)는 파편을 이어 붙여 "끊기지 않는 10명"을 만든다. 그것만으로는
**각본이 읽히지 않는다** — 우회 5명이어야 할 장면에 2명만 돌고, 줄지어 가야 할
4명이 3명으로 보이고, 뭉쳐 가야 할 무리에서 둘이 뒤처졌다 합류하고, 우회조가
출구 앞까지 갔다 되돌아 나온다. 실측 트랙이 카메라 사각과 오검출로 그렇게 찍힌
것이지 훈련이 그랬던 게 아니다.

여기서는 그 base 에서 **세 가지만** 뽑아 쓰고 길은 도면 위에서 새로 긋는다.

  ① 어디서 출발하나 — 실측 사슬 첫 점들이 가장 빽빽한 곳(시작지점)
  ② 어디까지 돌았나 — 우회조가 실제로 닿은 가장 먼 지점(경유점)
  ③ 얼마나 걸렸나   — 그 무리가 실제로 걸린 시간(속도)

길 자체는 **거리장 위의 최적 경로**로 긋는다(시작지점 → [경유점] → 출구). 그래서
  · 벽·의자·적치물을 절대 통과하지 않는다 — 격자가 애초에 막혀 있다
  · 되돌아 나오는 구간이 없다 — 최적 경로는 왔던 길을 되짚지 않는다
실측 궤적을 그대로 쓰면 저 둘이 안 지켜진다(실측: 장애물 통과 179점, 역주행 4명).

**지표는 이것을 쓰지 않는다.** 4대 지표·카운팅은 원 관측으로 이미 산출돼 있다.
"""
from __future__ import annotations

import math

import numpy as np

from tools import demo_tracks as dt

# 각본 — 세션별로 몇 명이 어느 길로, 어떤 간격으로 가는가.
#   mode   cluster = 경보 순간 시작지점에 다 모여 있다 / emerge = 그 자리에서 차례로 나온다
#   via    None(곧장 출구) · "right"(우측으로 돌았다 출구)
#   exit   나가는 출구 번호(None 이면 가장 가까운 출구)
#   gap    앞사람과의 출발 간격(초) — 좁으면 뭉쳐 보이고 넓으면 줄지어 보인다
#   spread 좌우로 퍼지는 폭(m)
SPECS = {
    # 01 — 5명은 곧장 출구, 5명은 우측으로 조금 돌았다 다시 출구
    "sess-1789693149252": {
        "label": "01 IDR", "mode": "cluster",
        "groups": [{"n": 5, "via": None, "gap": 1.1, "spread": 1.2},
                   {"n": 5, "via": "right", "gap": 1.2, "spread": 1.0}],
    },
    # 02 — 우측으로 빙 돌아 **줄서서** 가는 4명 + 곧장 가는 6명
    "sess-1789693292022": {
        "label": "02 EPFI", "mode": "cluster",
        "groups": [{"n": 6, "via": None, "gap": 1.2, "spread": 1.2},
                   {"n": 4, "via": "right", "gap": 2.0, "spread": 0.3}],
    },
    # 03 — 처음부터 끝까지 **한 무리로 뭉쳐서**. 뒤처졌다 합류하는 사람은 없다
    "sess-1789693478593": {
        "label": "03 CBS", "mode": "cluster",
        "groups": [{"n": 10, "via": None, "gap": 0.35, "spread": 1.5}],
    },
    # 04 — 계단을 내려온 뒤 **계단 출구에서 차례로 나와** 두 출구로 반반
    "sess-1789693805049": {
        "label": "04 SEI", "mode": "emerge",
        "groups": [{"n": 5, "via": None, "exit": 0, "gap": 2.2, "spread": 1.1},
                   {"n": 5, "via": None, "exit": 1, "gap": 2.2, "spread": 1.1}],
    },
}

HUB_R_M = 3.0          # 시작지점을 벗어났다고 볼 거리
WIGGLE = 0.40          # 좌우 흔들림 — 같은 선 위를 행진하면 기계 같다
STEP_M = 0.35          # 경로 리샘플 간격
PAST_M = 1.5           # 출구선을 넘어 이만큼 더 가고 사라진다


# ── 도면 위에 길 긋기 ─────────────────────────────────────────────────────
def _free_cell(df, px):
    from system.evac.route_solver import _nearest_free
    c, r = df.to_cell(*px)
    n = _nearest_free(df, c, r, 60)
    return (n[0], n[1]) if n else None


def _leg(df, a_px, b_px):
    """a → b 최적 경로(맵 px). 격자가 막혀 있으니 벽·장애물을 통과할 수 없다."""
    from system.evac.route_solver import _los
    ca, cb = _free_cell(df, a_px), _free_cell(df, b_px)
    if ca is None or cb is None:
        return [list(a_px), list(b_px)]
    cells = dt._astar(df.wall, ca, cb, limit=250000)
    if not cells:
        return [list(a_px), list(b_px)]
    # 줄 당기기 — 격자 계단을 사람이 걷는 선으로 편다
    out, i = [cells[0]], 0
    while i < len(cells) - 1:
        j = len(cells) - 1
        while j > i + 1 and not _los(df.wall, cells[i], cells[j]):
            j -= 1
        out.append(cells[j])
        i = j
    return [list(df.to_px(c, r)) for c, r in out]


def _resample_px(pts, step_px):
    """일정 간격으로 다시 찍고 모서리를 둥글린다 — 꺾인 각을 사람처럼 돌게."""
    if len(pts) < 2:
        return pts
    seg = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    L = sum(seg)
    n = max(2, int(L / step_px))
    arc = np.concatenate([[0.0], np.cumsum(seg)])
    g = np.linspace(0, L, n)
    xs = np.interp(g, arc, [p[0] for p in pts])
    ys = np.interp(g, arc, [p[1] for p in pts])
    w = 9
    if n > w:
        k = np.ones(w) / w
        xs = np.convolve(np.pad(xs, w // 2, mode="edge"), k, "valid")
        ys = np.convolve(np.pad(ys, w // 2, mode="edge"), k, "valid")
    return [[float(x), float(y)] for x, y in zip(xs, ys)]


def _exit_target(exits_px, k, frm):
    """출구선 위에서 frm 에 가장 가까운 점 + 선을 조금 넘어선 점."""
    (x1, y1), (x2, y2) = exits_px[k]
    _, foot = dt._seg_foot(frm, (x1, y1), (x2, y2))
    return foot


# ── base 에서 뽑아 쓰는 세 가지 ───────────────────────────────────────────
def _main_exit(people, exits_px):
    """base 사람들이 **실제로 나간 출구**. 직선거리로 고르면 엉뚱한 문으로 간다
    (실측: 전원이 계단을 썼는데 오른쪽 문이 더 가까워 그리로 몰려갔다)."""
    votes = [0] * len(exits_px)
    for p in people:
        end = p["xy"][-1]
        votes[int(np.argmin([dt._seg_dist(end, e[0], e[1]) for e in exits_px]))] += 1
    return int(np.argmax(votes))


def _deviation(xy, a, b):
    """선분 a→b 기준 **수직 이탈** 목록 (부호 있음, px). 어느 쪽으로 얼마나 돌았나."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    out = []
    for x, y in xy:
        vx, vy = x - a[0], y - a[1]
        t = (vx * ux + vy * uy) / n
        out.append((t, vx * (-uy) + vy * ux))
    return out


def _classify(people, hub, exit_pt):
    """시작지점→출구 직선에서 **얼마나 벗어났나**로 가른다. 곧장 간 무리 / 돈 무리."""
    rs = []
    for i, p in enumerate(people):
        dev = _deviation(p["xy"], hub, exit_pt)
        far = max((abs(d) for t, d in dev if 0.1 <= t <= 0.9), default=0.0)
        rs.append((far, i, p))
    rs.sort(key=lambda z: z[0])
    cut = rs[min(len(rs) - 1, int(len(rs) * 0.6))][0]
    direct = [p for r, _, p in rs if r <= cut]
    right = [p for r, _, p in rs if r > cut] or [rs[-1][2]]
    return direct, right


def _moving_span(p, hub, dt_s, m_per_px):
    """그 사람이 **실제로 움직인** 구간 (시작 시각, 걸린 시간)."""
    xy, r_px = p["xy"], HUB_R_M / m_per_px
    i0 = next((i for i, q in enumerate(xy)
               if math.hypot(q[0] - hub[0], q[1] - hub[1]) > r_px), 0)
    return p["t0"] + i0 * dt_s, max(3.0, (len(xy) - i0) * dt_s)


def _waypoint(right_people, hub, exit_pt):
    """우회조가 실제로 **가장 크게 벗어난 지점** — 경유점.

    시작지점→출구 직선에서 수직으로 가장 멀리 나간 점이다. 화면 좌표의 좌·우가
    아니라 **가는 방향 기준**이라, 도면을 어느 쪽으로 돌려 놔도 같은 뜻이 된다."""
    best, bd = None, 0.0
    for p in right_people:
        for (t, d), q in zip(_deviation(p["xy"], hub, exit_pt), p["xy"]):
            if 0.15 <= t <= 0.85 and abs(d) > bd:
                bd, best = abs(d), q
    return best


# ── 배치 ──────────────────────────────────────────────────────────────────
def _free(df, x, y):
    W, H = df.wall.shape
    c, r = df.to_cell(x, y)
    return not (0 <= c < W and 0 <= r < H) or not df.wall[c, r]


def _offset(route, lane_px, phase, df=None):
    """진행방향에 **수직으로** 밀어 준다 — 차선 + 완만한 흔들림.

    차선을 그대로 밀면 바깥 차선이 통로를 벗어나 의자 위로 올라간다(실측 116점).
    벽에 걸리면 그 점만 **중심선 쪽으로 당겨** 붙인다 — 길 밖으로 나가느니
    가운데로 붙는 게 맞다."""
    arc = np.concatenate([[0.0], np.cumsum(
        [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(route, route[1:])])])
    L = arc[-1] or 1.0
    out = []
    for i, (x, y) in enumerate(route):
        a = route[max(0, i - 2)]
        b = route[min(len(route) - 1, i + 2)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy) or 1.0
        k = lane_px * (1.0 + WIGGLE * math.sin(phase + arc[i] / L * 9.0))
        # 양 끝은 차선을 접는다 — 출발점·출구에서 옆으로 벌어져 있으면 어색하다
        taper = min(1.0, arc[i] / (L * 0.12) if L else 1.0,
                    (L - arc[i]) / (L * 0.12) if L else 1.0)
        ux, uy = -dy / n, dx / n
        px, py = x + ux * k * taper, y + uy * k * taper
        if df is not None and not _free(df, px, py):
            for f in (0.7, 0.45, 0.25, 0.1, 0.0):
                px, py = x + ux * k * taper * f, y + uy * k * taper * f
                if _free(df, px, py):
                    break
        out.append([px, py])
    return out


def _settle(df, xy):
    """마지막 손질 — 다듬기(이동평균)가 모서리에서 도로 벽으로 밀어 넣은 점을
    **직전 점 쪽으로 당겨** 뺀다. 남는 건 보통 한 자릿수라 그림은 안 변한다."""
    out = []
    for i, (x, y) in enumerate(xy):
        if not _free(df, x, y) and out:
            ax, ay = out[-1]
            for f in (0.8, 0.6, 0.4, 0.2, 0.0):
                nx, ny = ax + (x - ax) * f, ay + (y - ay) * f
                if _free(df, nx, ny):
                    x, y = nx, ny
                    break
        out.append([x, y])
    return out


def build(ideal: dict, df, m_per_px: float, exits_px, spec: dict) -> dict:
    dt_s = ideal["dt"]
    hub = tuple(ideal["hub"])
    main_k = _main_exit(ideal["people"], exits_px)
    main_pt = _exit_target(exits_px, main_k, hub)
    direct_p, right_p = _classify(ideal["people"], hub, main_pt)
    wp = _waypoint(right_p, hub, main_pt)
    step_px = STEP_M / m_per_px

    people, idx = [], 0
    for g in spec["groups"]:
        pool = right_p if g.get("via") == "right" else direct_p
        t_move = float(np.median([_moving_span(p, hub, dt_s, m_per_px)[0] for p in pool]))
        dur = float(np.median([_moving_span(p, hub, dt_s, m_per_px)[1] for p in pool]))

        # 길 긋기 — 시작지점 → [경유점] → 출구. 격자가 막혀 있어 장애물을 못 뚫는다
        legs, cur = [], hub
        if g.get("via") == "right" and wp is not None:
            legs.append(_leg(df, cur, wp)); cur = tuple(wp)
        k = g.get("exit")
        if k is None:
            k = main_k                       # base 사람들이 실제로 쓴 문
        tgt = _exit_target(exits_px, k, cur)
        legs.append(_leg(df, cur, tgt))
        route = legs[0]
        for lg in legs[1:]:
            route += lg[1:]
        # 출구선을 조금 넘어가 사라진다 — 문턱에서 증발하면 이상하다
        a, b = route[-2], route[-1]
        n = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        route.append([b[0] + (b[0] - a[0]) / n * PAST_M / m_per_px,
                      b[1] + (b[1] - a[1]) / n * PAST_M / m_per_px])
        route = _resample_px(route, step_px)

        n_g = g["n"]
        for j in range(n_g):
            lane = ((j - (n_g - 1) / 2) / max(1.0, (n_g - 1) / 2)
                    * g["spread"] / m_per_px)
            path = _offset(route, lane, phase=j * 1.7, df=df)
            t_dep = t_move + j * g["gap"]
            # 실제로 걸린 시간에 맞춰 걷는다 — 그리드 영상과 크게 어긋나지 않게
            ts = np.linspace(t_dep, t_dep + dur, len(path))
            pts = [[float(t), q[0], q[1]] for t, q in zip(ts, path)]
            if spec["mode"] == "cluster":
                slot = dt._hub_slot(idx, 10, hub, m_per_px)
                pts = [[0.0, slot[0], slot[1]],
                       [max(0.05, t_dep - 0.8), slot[0], slot[1]]] + pts
            people.append({"id": f"P{idx + 1:02d}", "pts": pts, "exit_at": pts[-1][0]})
            idx += 1

    out = []
    for p in people:
        r = dt._resample(p)
        r["xy"] = _settle(df, r["xy"])
        r["xy"] = [[round(a, 1), round(b, 1)] for a, b in r["xy"]]
        out.append(r)
    return {"n": len(out), "t_end": ideal["t_end"], "dt": dt_s, "hub": list(hub),
            "people": out, "choreo": spec["label"],
            "waypoint": [round(wp[0], 1), round(wp[1], 1)] if wp is not None else None}
