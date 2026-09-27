# -*- coding: utf-8 -*-
"""**각본 연출** — 발표 영상의 점 움직임을 각본대로 짠다 (표출 전용).

앞 단계(`demo_tracks`)는 파편을 이어 붙여 "끊기지 않는 10명"을 만든다. 그것만으로는
**각본이 읽히지 않는다** — 우회 5명이어야 할 장면에 2명만 돌고, 줄지어 가야 할
4명이 3명으로 보이고, 뭉쳐 가야 할 무리에서 둘이 뒤처졌다 합류하고, 우회조가
출구 앞까지 갔다 되돌아 나온다. 실측 트랙이 카메라 사각과 오검출로 그렇게 찍힌
것이지 훈련이 그랬던 게 아니다.

**길은 실측 동선 그대로 쓴다.** 도면 위에 새로 그으면 사람이 실제로 다닌 자리와
달라져 우측 그리드 영상과 따로 논다 — 그건 시각화가 아니라 그림이다.

여기서 하는 일은 셋뿐이다.

  ① 무리 나누기 — 실측 궤적을 곧장 간 쪽 / 크게 돌아간 쪽으로 가른다
  ② 인원 맞추기 — 각 무리의 **실측 대표 동선**에 각본 인원을 태운다. 실제로 그
     길로 간 사람이 2명뿐이어도 각본이 5명이면 5명을 그 동선에 올린다
     (출발 간격과 좌우 폭만 준다 — 줄서기는 좁게, 밀집은 넓게)
  ③ 장애물 비켜 가기 — 장애물 안에 찍힌 점만 **옆으로 밀어** 뺀다. 길을 새로
     긋지 않는다. 동선 모양과 시간축은 실측 그대로 남는다 → 그리드와 안 어긋난다

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


# ── 장애물 비켜 가기 ──────────────────────────────────────────────────────
def _push_out(df, route, m_per_px):
    """장애물 안에 들어간 점만 **진행방향과 수직으로** 밀어 뺀다.

    길을 새로 긋지 않는 이유: 실측 동선이 base 다. A* 로 다시 그으면 사람이
    실제로 다닌 자리와 달라져 우측 그리드 영상과 따로 논다. 옆으로 비키는 건
    사람이 의자를 피해 가는 것과 같은 동작이라 모양이 거의 안 변한다."""
    step = 0.25 / m_per_px
    out, moved = [], 0
    for i, (x, y) in enumerate(route):
        if _free(df, x, y):
            out.append([x, y])
            continue
        a = route[max(0, i - 2)]
        b = route[min(len(route) - 1, i + 2)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy) or 1.0
        ux, uy = -dy / n, dx / n
        best = None
        for k in range(1, 25):                      # 좌우로 번갈아 넓혀 가며 찾는다
            for sgn in (1, -1):
                px, py = x + ux * step * k * sgn, y + uy * step * k * sgn
                if _free(df, px, py):
                    best = [px, py]
                    break
            if best:
                break
        out.append(best or [x, y])
        moved += 1
    return out, moved


def _resample_px(pts, step_px):
    """일정 간격으로 다시 찍고 모서리를 둥글린다 — 관측 잡음만 걷어낸다."""
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


def _rep(pool, hub, exit_pt, kind):
    """그 무리의 **실측 대표 동선** 하나. 지어내지 않고 실제 궤적에서 고른다.

    우회조는 가장 크게 돌아간 것(그게 각본이 보여주려는 동작이다), 직선조는
    길이가 중앙값인 것(가장 평범한 걸음)."""
    scored = []
    for p in pool:
        dev = _deviation(p["xy"], hub, exit_pt)
        far = max((abs(d) for t, d in dev if 0.1 <= t <= 0.9), default=0.0)
        arc = sum(math.hypot(b[0] - a[0], b[1] - a[1])
                  for a, b in zip(p["xy"], p["xy"][1:]))
        scored.append((far, arc, p))
    if kind == "right":
        return max(scored, key=lambda z: z[0])[2]
    scored.sort(key=lambda z: z[1])
    return scored[len(scored) // 2][2]


def _dedetour(df, xy, exits_px, m_per_px, keep_m=9.0):
    """**헛걸음**을 걷어낸다 — 출구 문 앞까지 갔다가 되돌아 나와 다시 들어오는 구간.

    실측 우회 궤적이 그렇게 찍힌다(02 EPFI 대표: 출구거리 14→1→14→1). 각본은
    "줄서서 빙 돌아 **들어간다**"지 "문 앞까지 갔다 되돌아 나온다"가 아니다.

    지우는 방식: 나가는 길과 되돌아 나온 길이 **둘 다 출구에서 keep_m 쯤 떨어져
    있던 지점**을 찾아 그 둘을 잇고, 사이의 헛걸음을 뺀다. 잇는 구간만 벽을 피해
    새로 긋고(실측 12m) 나머지 — 우회 고리 전체 — 는 실측 동선 그대로다.
    """
    d = [min(dt._seg_dist(q, a, b) for a, b in exits_px) * m_per_px for q in xy]
    a = next((i for i, v in enumerate(d) if v < 4.0), None)
    if a is None:
        return xy, 0
    apex = a + int(np.argmax(d[a:]))
    if d[apex] < 10.0:                      # 되돌아 나간 적이 없다 — 그대로 둔다
        return xy, 0
    i1 = max((i for i in range(a) if d[i] >= keep_m), default=0)
    i2 = next((i for i in range(a, apex + 1) if d[i] >= keep_m), apex)
    # 잇는 길이 **출구 로비를 가로지르면** 결국 문 앞을 찍고 지나간다. 이을 때만
    # 출구 둘레 keep_m 를 막아 두고 A* 를 돌린다 — 그래야 빙 돌아 잇는다.
    bridge = _bridge_around_exit(df, xy[i1], xy[i2], exits_px, keep_m, m_per_px)
    return xy[:i1] + [list(q) for q in bridge] + xy[i2:], i2 - i1


def _bridge_around_exit(df, a_px, b_px, exits_px, keep_m, m_per_px):
    """a → b 를 잇되 **출구 둘레는 피한다**. 못 피하면 그냥 최단으로 잇는다."""
    import cv2
    cell_m = df.cell_px * df.m_per_px
    # 막는 반경은 이음점보다 **조금 작게** — 같으면 이음점 자체가 막혀 A* 가
    # 시작도 못 하고 로비를 가로지르는 폴백으로 떨어진다(실측: 문 앞 0.1m 통과).
    r = max(1, int(round(keep_m * 0.7 / cell_m)))
    cols, rows = df.wall.shape
    m = np.zeros((rows, cols), np.uint8)
    for (x1, y1), (x2, y2) in exits_px:
        cv2.line(m, (int(x1 / df.cell_px), int(y1 / df.cell_px)),
                 (int(x2 / df.cell_px), int(y2 / df.cell_px)), 1, thickness=r * 2)
    blocked = df.wall | m.astype(bool).T

    def free_on(px):
        c0, r0 = df.to_cell(*px)
        for rad in range(0, 60):
            for dc_ in range(-rad, rad + 1):
                for dr in (-rad, rad) if rad else (0,):
                    c, rr = c0 + dc_, r0 + dr
                    if 0 <= c < cols and 0 <= rr < rows and not blocked[c, rr]:
                        return (c, rr)
                for dr in range(-rad, rad + 1):
                    c, rr = c0 + (rad if dc_ == rad else -rad), r0 + dr
                    if 0 <= c < cols and 0 <= rr < rows and not blocked[c, rr]:
                        return (c, rr)
        return None

    ca, cb = free_on(a_px), free_on(b_px)
    if ca and cb:
        cells = dt._astar(blocked, ca, cb, limit=600000)
        if cells:
            step = max(1, len(cells) // 40)
            return [list(df.to_px(c, rr)) for c, rr in cells[1:-1:step]]
    return [list(q) for q in dt._walk_path(df, a_px, b_px)[1:-1]]


def _trim_start(xy, hub, m_per_px):
    """시작지점 근처에서 맴돈 앞머리를 자른다 — 출발이 흐리면 각본이 안 읽힌다."""
    r = HUB_R_M / m_per_px
    i = next((k for k, q in enumerate(xy)
              if math.hypot(q[0] - hub[0], q[1] - hub[1]) > r), 0)
    return xy[i:], i


def build(ideal: dict, df, m_per_px: float, exits_px, spec: dict) -> dict:
    dt_s = ideal["dt"]
    hub = tuple(ideal["hub"])
    main_k = _main_exit(ideal["people"], exits_px)
    main_pt = _exit_target(exits_px, main_k, hub)
    direct_p, right_p = _classify(ideal["people"], hub, main_pt)
    step_px = STEP_M / m_per_px

    people, idx, moved_total = [], 0, 0
    for g in spec["groups"]:
        pool = right_p if g.get("via") == "right" else direct_p
        if g.get("exit") is not None:                 # 나가는 문으로 무리를 가른다
            k = g["exit"]
            sel = [p for p in ideal["people"]
                   if int(np.argmin([dt._seg_dist(p["xy"][-1], e[0], e[1])
                                     for e in exits_px])) == k]
            pool = sel or pool
        k_now = g.get("exit") if g.get("exit") is not None else main_k
        rep = _rep(pool, hub, main_pt, g.get("via"))

        # 실측 동선 그대로 — 앞머리(시작지점 맴돌기)와 헛걸음만 걷어낸다
        route, i0 = _trim_start(rep["xy"], hub, m_per_px)
        if len(route) < 5:
            route, i0 = rep["xy"], 0
        t_move = rep["t0"] + i0 * dt_s
        dur = max(3.0, len(route) * dt_s)             # 그 사람이 실제로 걸린 시간
        # 판정 기준은 **그 무리가 나가는 문** 하나 — 지나가는 다른 문까지
        # 세면 "문 앞에 갔다 왔다"로 오판해 멀쩡한 우회를 잘라 버린다.
        route, cut = _dedetour(df, route, [exits_px[k_now]], m_per_px)
        if cut:
            dur = max(3.0, dur - cut * dt_s * 0.5)    # 지운 만큼 조금 당긴다
        route = _resample_px(route, step_px)
        route, mv = _push_out(df, route, m_per_px)
        moved_total += mv

        n_g = g["n"]
        for j in range(n_g):
            lane = ((j - (n_g - 1) / 2) / max(1.0, (n_g - 1) / 2)
                    * g["spread"] / m_per_px)
            path = _offset(route, lane, phase=j * 1.7, df=df)
            t_dep = t_move + j * g["gap"]
            ts = np.linspace(t_dep, t_dep + dur, len(path))
            pts = [[float(t), q[0], q[1]] for t, q in zip(ts, path)]
            if spec["mode"] == "cluster":
                slot = dt._hub_slot(idx, 10, hub, m_per_px)
                lead = dt._walk_path(df, slot, (path[0][0], path[0][1]))
                sec = dt._path_sec(lead, m_per_px, dt.HUB_WALK_MPS)
                t_start = max(0.0, t_dep - sec)
                pre = [[0.0, slot[0], slot[1]]]
                if t_start > 0.05:
                    pre.append([t_start, slot[0], slot[1]])
                span = max(t_dep - t_start, 0.2)
                tot = sum(math.hypot(q[0] - p[0], q[1] - p[1])
                          for p, q in zip(lead, lead[1:])) or 1.0
                acc = 0.0
                for pa, qa in zip(lead, lead[1:]):
                    acc += math.hypot(qa[0] - pa[0], qa[1] - pa[1])
                    pre.append([t_start + span * acc / tot, qa[0], qa[1]])
                pts = pre[:-1] + pts
            people.append({"id": f"P{idx + 1:02d}", "pts": pts, "exit_at": pts[-1][0]})
            idx += 1

    out = []
    for p in people:
        r = dt._resample(p)
        r["xy"], mv = _push_out(df, r["xy"], m_per_px)
        moved_total += mv
        r["xy"] = [[round(a, 1), round(b, 1)] for a, b in r["xy"]]
        out.append(r)
    return {"n": len(out), "t_end": ideal["t_end"], "dt": dt_s, "hub": list(hub),
            "people": out, "choreo": spec["label"], "pushed": moved_total,
            "waypoint": None}
