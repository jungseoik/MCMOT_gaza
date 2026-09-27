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

  ⓪ **시간은 실측 그대로 들고 간다** — 대표가 실제로 걸은 (시각, 위치) 짝을
     그대로 쓰고, 문을 지나는 시각만 배정받은 통과 시각에 맞춰 **통째로 민다**.
     시간을 균등하게 다시 매기면 속도 곡선이 뭉개져 (ⓐ) 어디선가 갑자기 빨라지고
     (ⓑ) 문 앞에서 멈칫하는 병목이 사라지고 (ⓒ) 끝만 맞고 중간이 어긋난다.

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
        "groups": [{"n": 10, "via": None, "gap": 0.35, "spread": 0.9}],
    },
    # 04 — 계단을 내려온 뒤 **계단 출구에서 차례로 나와** 두 출구로 반반
    "sess-1789693805049": {
        "label": "04 SEI", "mode": "emerge",
        "groups": [{"n": 5, "via": None, "exit": 0, "gap": 2.2, "spread": 1.1},
                   {"n": 5, "via": None, "exit": 1, "gap": 2.2, "spread": 1.1}],
    },
}

def passage_times(timeline, t0):
    """출구별 **실제 통과 시각**(경보 기준 초). 카운팅이 1 늘어난 순간들이다.

    이게 싱크의 정본이다 — 우측 그리드에서 사람이 문을 지나는 그 순간에 좌측
    점도 문을 지나야 한다. 실측 대표의 길이만으로 시간을 매기면 어긋난다
    (실측: 01 IDR 은 18~33초에 다 나갔는데 점은 30초에도 한참 멀리 있었다).
    """
    out, prev = {}, {}
    for p in timeline:
        d = p.model_dump() if hasattr(p, "model_dump") else p
        for k, v in (d.get("exit_counts") or {}).items():
            n = v - prev.get(k, 0)
            if n > 0:
                out.setdefault(k, []).extend([d["ts"] - t0] * n)
                prev[k] = v
    for k in out:
        out[k].sort()
    return out


V_MAX = 2.2            # 사람이 낼 수 있는 속도 상한 (m/s) — 훈련은 뛰는 게 아니다
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
        n = math.hypot(dx, dy)
        best = None
        if n > 1e-6:                                # 좌우로 번갈아 넓혀 가며
            ux, uy = -dy / n, dx / n
            for k in range(1, 25):
                for sgn in (1, -1):
                    px, py = x + ux * step * k * sgn, y + uy * step * k * sgn
                    if _free(df, px, py):
                        best = [px, py]
                        break
                if best:
                    break
        if best is None:
            # 서 있는 점은 진행방향이 없어 '옆'이 정의되지 않는다. 사방으로 찾는다
            # (실측: 시작 슬롯이 장애물 안에 놓여 한 사람이 통째로 벽에 박혔다).
            for k in range(1, 40):
                for ang in range(0, 360, 30):
                    t = math.radians(ang)
                    px, py = x + math.cos(t) * step * k, y + math.sin(t) * step * k
                    if _free(df, px, py):
                        best = [px, py]
                        break
                if best:
                    break
        out.append(best or [x, y])
        moved += 1
    return out, moved


def _exit_target(exits_px, k, frm=None):
    """출구선 **한가운데** — 문 가운데로 나가야 한다.

    수선의 발을 쓰면 문틀 끝, 즉 벽 쪽을 겨눠 점이 벽으로 걸어 들어간다
    (실측: 02 EPFI 에서 dot 가 출구 벽을 향해 갔다)."""
    (x1, y1), (x2, y2) = exits_px[k]
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


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


def _trim_start_i(route, hub, m_per_px):
    """시작지점 반경을 벗어나는 첫 점의 번호 — 그 앞은 제자리 맴돌기다."""
    r = HUB_R_M / m_per_px
    return next((k for k, q in enumerate(route)
                 if math.hypot(q[0] - hub[0], q[1] - hub[1]) > r), 0)


def _path_sec_px(pts, m_per_px, mps):
    d = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
    return d * m_per_px / max(0.1, mps)


def _lerp_span(t0, t1, n):
    return [t0 + (t1 - t0) * (k + 1) / (n + 1) for k in range(n)] if n else []


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
        # 문 앞은 좁다. 차선을 늦게 접으면 바깥 차선이 벽에 밀렸다 튕겼다 하며
        # 경로가 길어지고, 고정 간격(0.1초)에서 그게 곧 순간이동이 된다
        # (실측: 03 CBS 바깥 차선 17 m/s).
        taper = min(1.0, arc[i] / (L * 0.12) if L else 1.0,
                    (L - arc[i]) / (L * 0.30) if L else 1.0)
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


def _dedetour_t(df, xy, T, ex, m_per_px, keep_m=9.0):
    """**헛걸음**을 걷어낸다 — 출구 문 앞까지 갔다가 되돌아 나와 다시 들어오는 구간.

    실측 우회 궤적이 그렇게 찍힌다(02 EPFI 대표: 출구거리 14→1→14→1). 각본은
    "줄서서 빙 돌아 **들어간다**"지 "문 앞까지 갔다 되돌아 나온다"가 아니다.

    지우는 방식: 나가는 길과 되돌아 나온 길이 **둘 다 출구에서 keep_m 쯤 떨어져
    있던 지점**을 찾아 그 둘을 잇고, 사이의 헛걸음을 뺀다. 잇는 구간만 벽을 피해
    새로 긋고(실측 12m) 나머지 — 우회 고리 전체 — 는 실측 동선 그대로다.
    """
    d = [dt._seg_dist(q, ex[0], ex[1]) * m_per_px for q in xy]
    a = next((i for i, v in enumerate(d) if v < 4.0), None)
    if a is None:
        return xy, T
    apex = a + int(np.argmax(d[a:]))
    if d[apex] < 10.0:                      # 되돌아 나간 적이 없다 — 그대로 둔다
        return xy, T
    i1 = max((i for i in range(a) if d[i] >= keep_m), default=0)
    i2 = next((i for i in range(a, apex + 1) if d[i] >= keep_m), apex)
    # 잇는 길이 **출구 로비를 가로지르면** 결국 문 앞을 찍고 지나간다. 이을 때만
    # 출구 둘레 keep_m 를 막아 두고 A* 를 돌린다 — 그래야 빙 돌아 잇는다.
    bridge = _bridge_around_exit(df, xy[i1], xy[i2], [ex], keep_m, m_per_px)
    # 헛걸음을 지운 만큼 **시간도 함께** 당긴다 — 안 그러면 잇는 구간에서
    # 순간이동한 것처럼 보인다.
    gone = T[i2] - T[i1]
    # 잇는 구간도 **걸어서** 가야 한다. 지운 시간에 억지로 맞추면 그 구간만
    # 순간이동하듯 빨라진다 — 모자라면 뒤쪽을 미는 게 낫다(끝은 어차피 통과
    # 시각에 다시 못 박는다).
    keep = _path_sec_px(bridge, m_per_px, 1.3)
    bT = _lerp_span(T[i1], T[i1] + keep, len(bridge))
    tail = [t - (gone - keep) for t in T[i2:]]
    return xy[:i1] + [list(q) for q in bridge] + xy[i2:], T[:i1] + bT + tail


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


def _finish_at_exit(df, P, T, ex, m_per_px, near_m=0.4):
    """경로가 **반드시 문에서 끝나게** 한다.

    문에 닿으면 거기서 자르고, 못 닿으면 마지막으로 가장 가까웠던 점에서 문까지
    걸어 붙인다(벽을 피해서). 끝을 문에 고정해야 배정받은 통과 시각으로 밀 때
    맞출 지점이 생기고, 지나친 뒤가 남지 않아 **나간 점이 다시 나타나지 않는다**.
    """
    d = [dt._seg_dist(q, ex[0], ex[1]) * m_per_px for q in P]
    i = next((k for k, v in enumerate(d) if v <= near_m), None)
    if i is not None:
        return P[:i + 1], list(T[:i + 1])
    i = max(1, int(np.argmin(d)))
    P, T = P[:i + 1], list(T[:i + 1])
    tgt = ((ex[0][0] + ex[1][0]) / 2.0, (ex[0][1] + ex[1][1]) / 2.0)
    tail = dt._walk_path(df, (P[-1][0], P[-1][1]), tgt)[1:]
    if not tail:
        return P, T
    # 붙이는 구간의 속도는 **그 사람이 직전에 걷던 속도** — 갑자기 빨라지면 안 된다
    n = min(30, len(P) - 1)
    mps = 1.2
    if n > 2 and T[-1] > T[-1 - n]:
        dist = sum(math.hypot(b[0] - a[0], b[1] - a[1])
                   for a, b in zip(P[-1 - n:], P[-n:])) * m_per_px
        mps = max(0.4, min(2.0, dist / (T[-1] - T[-1 - n])))
    t = T[-1]
    for a, b in zip([P[-1]] + [list(q) for q in tail], tail):
        t += math.hypot(b[0] - a[0], b[1] - a[1]) * m_per_px / mps
        P.append(list(b))
        T.append(t)
    return P, T


def _limit_speed(pts, m_per_px, v_max=V_MAX):
    """**속도 상한**을 건다. 도착 시각은 그대로 두고 앞 시각을 당긴다.

    이어 붙이고 자르고 미는 손질이 쌓이면 어느 구간에서 점이 갑자기 튄다
    (실측: 최고 7.35 m/s — 전력 질주다). 뒤에서부터 훑으며 너무 빠른 구간마다
    '이만큼은 걸려야 한다'를 앞 시각에 반영한다. 도착(= 문을 지나는 순간)은
    못 박혀 있으므로 뒤로는 절대 밀리지 않는다."""
    out = [list(q) for q in pts]
    for i in range(len(out) - 2, -1, -1):
        d = math.hypot(out[i + 1][1] - out[i][1],
                       out[i + 1][2] - out[i][2]) * m_per_px
        need = d / v_max
        if out[i + 1][0] - out[i][0] < need:
            out[i][0] = out[i + 1][0] - need
    return out


def _lerp_t(T, i, j, n):
    """i~j 사이에 n 개를 끼워 넣을 때 줄 시각들."""
    return [T[i] + (T[j] - T[i]) * (k + 1) / (n + 1) for k in range(n)]


def _unclip_t(df, P, T):
    """벽에 막힌 구간에 돌아가는 길을 끼워 넣는다. **시각도 같이** 보간한다."""
    from system.evac.route_solver import _los
    oP, oT = [P[0]], [T[0]]
    for i in range(len(P) - 1):
        a, b = P[i], P[i + 1]
        if not _los(df.wall, df.to_cell(*a), df.to_cell(*b)):
            mid = dt._walk_path(df, a, b)[1:-1]
            if mid:
                oP.extend(list(q) for q in mid)
                oT.extend(_lerp_t(T, i, i + 1, len(mid)))
        oP.append(list(b))
        oT.append(T[i + 1])
    return oP, oT


def _despike(xy, w=5):
    """한 점만 튀는 것을 **중앙값**으로 눌러 준다.

    이동평균은 튄 점을 이웃으로 퍼뜨릴 뿐이다. 실측 잡음은 한두 점이 확 튀는
    모양이라 중앙값이 맞다. 0.5초 창이라 걸음걸이는 그대로 남는다."""
    import numpy as _np
    a = _np.asarray(xy, float)
    if len(a) < w + 2:
        return [list(q) for q in a]
    h = w // 2
    pad = _np.pad(a, ((h, h), (0, 0)), mode="edge")
    out = _np.stack([_np.median(pad[i:i + w], axis=0) for i in range(len(a))])
    return [[float(x), float(y)] for x, y in out]


def _hug(df, xy):
    """이웃한 두 점 사이가 벽으로 막히면 **뒤 점을 앞 점 쪽으로 당겨** 붙인다.

    점을 끼워 넣지 않는 이유: 이 단계의 xy 는 0.1초 격자라 개수가 곧 시간이다.
    끼워 넣으면 시간이 밀린다. 막히는 건 보통 한 자릿수라 당겨도 그림이 안 변한다."""
    from system.evac.route_solver import _los
    out = [xy[0]]
    for q in xy[1:]:
        a = out[-1]
        if not _los(df.wall, df.to_cell(*a), df.to_cell(*q)):
            # ① 제자리 근처를 살짝 **옆으로** 밀어 본다 — 모서리를 자른 탓이라
            #    대개 몇 십 cm 면 풀린다. 개수를 유지해야 시간이 안 밀린다.
            hit = None
            for rad in (0.2, 0.4, 0.6):
                r_px = rad / df.m_per_px
                for ang in range(0, 360, 20):
                    t = math.radians(ang)
                    c = [q[0] + math.cos(t) * r_px, q[1] + math.sin(t) * r_px]
                    if (_free(df, *c)
                            and _los(df.wall, df.to_cell(*a), df.to_cell(*c))):
                        hit = c
                        break
                if hit:
                    break
            # ② 그래도 안 되면 앞 점 쪽으로 당긴다
            if hit is None:
                for f in (0.75, 0.5, 0.3, 0.15):
                    c = [a[0] + (q[0] - a[0]) * f, a[1] + (q[1] - a[1]) * f]
                    if _los(df.wall, df.to_cell(*a), df.to_cell(*c)):
                        hit = c
                        break
            # ③ 끝내 못 찾으면 **원래 자리**를 쓴다. 앞 점에 붙여 버리면 거기서
            #    멈추고 그 뒤로도 계속 막혀 한 사람이 통째로 얼어붙는다
            #    (실측: 02 EPFI 의 우회 3명이 출구에 못 닿고 끝났다).
            q = hit if hit is not None else q
        out.append(q)
    return out


def _from_point_t(route, T, pt, m_per_px, r_m=4.0):
    """경로의 앞머리를 버리고 **찍어 준 지점**에서 출발시킨다.

    계단 출구가 그렇다 — 계단을 내려오는 사람의 발끝 투영은 계단실 벽 안쪽에
    찍혀서, 그대로 그리면 점이 **벽을 통과해** 나온다(실측: 04 SEI).

    반경을 **마지막으로** 벗어난 지점부터 쓴다. 처음 벗어난 지점을 쓰면 계단실
    안팎을 들락거린 잡음이 남아 점이 문 앞에서 맴돌다 나간다."""
    r = r_m / m_per_px
    inside = [k for k, q in enumerate(route)
              if math.hypot(q[0] - pt[0], q[1] - pt[1]) <= r]
    i = (inside[-1] + 1) if inside else 0
    if i >= len(route) - 3:                    # 전부 안쪽이면 자르지 않는다
        i = 0
    return [list(pt)] + route[i:], [T[i]] + T[i:]


def _trim_start(xy, hub, m_per_px):
    """시작지점 근처에서 맴돈 앞머리를 자른다 — 출발이 흐리면 각본이 안 읽힌다."""
    r = HUB_R_M / m_per_px
    i = next((k for k, q in enumerate(xy)
              if math.hypot(q[0] - hub[0], q[1] - hub[1]) > r), 0)
    return xy[i:], i


def build(ideal: dict, df, m_per_px: float, exits_px, spec: dict,
          passages: dict | None = None, anchors: dict | None = None) -> dict:
    dt_s = ideal["dt"]
    anchors = anchors or {}
    hub = tuple(anchors.get("start") or ideal["hub"])    # 사람이 찍어 준 자리가 우선
    main_k = _main_exit(ideal["people"], exits_px)
    main_pt = _exit_target(exits_px, main_k, hub)
    direct_p, right_p = _classify(ideal["people"], hub, main_pt)
    step_px = STEP_M / m_per_px

    # 통과 시각을 출구 **번호**로 바꿔 담는다 — 각본은 번호로 문을 가리킨다
    pass_pool = {}
    for key, ts in (passages or {}).items():
        try:
            pass_pool[int(str(key).rsplit("-", 1)[-1])] = list(ts)
        except ValueError:
            continue
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

        # **실측 (시각, 위치) 짝을 그대로 들고 간다.** 여기서 시간을 다시 매기면
        # 실제 속도 곡선 — 문 앞에서 멈칫하는 병목까지 — 이 뭉개진다.
        P = [list(q) for q in rep["xy"]]
        T = [rep["t0"] + k * dt_s for k in range(len(P))]

        i0 = _trim_start_i(P, hub, m_per_px)          # 시작지점 맴돌기
        P, T = P[i0:], T[i0:]
        if len(P) < 5:
            P = [list(q) for q in rep["xy"]]
            T = [rep["t0"] + k * dt_s for k in range(len(P))]

        # 판정 기준은 **그 무리가 나가는 문** 하나 — 지나가는 다른 문까지 세면
        # "문 앞에 갔다 왔다"로 오판해 멀쩡한 우회를 잘라 버린다.
        P, T = _dedetour_t(df, P, T, exits_px[k_now], m_per_px)
        if spec["mode"] == "emerge" and anchors.get("stair_exit"):
            P, T = _from_point_t(P, T, anchors["stair_exit"], m_per_px)
        P, mv = _push_out(df, P, m_per_px)
        moved_total += mv

        n_g = g["n"]
        avail = list(pass_pool.get(k_now) or [])
        pass_q = avail[:n_g]
        pass_pool[k_now] = avail[n_g:]
        for j in range(n_g):
            lane = ((j - (n_g - 1) / 2) / max(1.0, (n_g - 1) / 2)
                    * g["spread"] / m_per_px)
            path = _offset(P, lane, phase=j * 1.7, df=df)
            path, tt = _finish_at_exit(df, path, T, exits_px[k_now], m_per_px)
            path, tt = _unclip_t(df, path, tt)                 # 선이 벽을 안 뚫게

            # **문을 지나는 시각만** 배정받은 통과 시각에 맞춰 통째로 민다.
            # 속도 곡선은 실측 그대로라 중간도 저절로 맞는다.
            t_arr = pass_q[j] if j < len(pass_q) else tt[-1] + j * g["gap"]
            shift = t_arr - tt[-1]
            tt = [t + shift for t in tt]
            if tt[0] < 0:                                      # 경보 전엔 못 움직인다
                tt = [t - tt[0] for t in tt]
            pts = [[float(t), q[0], q[1]] for t, q in zip(tt, path)]

            if spec["mode"] == "cluster":
                slot = dt._hub_slot(idx, 10, hub, m_per_px)
                if not _free(df, *slot):        # 사람이 의자 위에 서 있을 수는 없다
                    slot = tuple(_push_out(df, [list(slot)], m_per_px)[0][0])
                lead = dt._walk_path(df, slot, (path[0][0], path[0][1]))
                sec = dt._path_sec(lead, m_per_px, dt.HUB_WALK_MPS)
                if sec > tt[0] - 0.5:
                    # 걸어갈 시간이 모자라면 억지로 달리게 된다(실측 7 m/s).
                    # 그럴 바엔 애초에 거기 서 있던 것으로 둔다.
                    lead, sec = [list(path[0])], 0.0
                    slot = (path[0][0], path[0][1])
                t_start = max(0.0, tt[0] - sec)
                pre = [[0.0, slot[0], slot[1]]]
                if t_start > 0.05:
                    pre.append([t_start, slot[0], slot[1]])
                span = max(tt[0] - t_start, 0.2)
                tot = sum(math.hypot(q[0] - p[0], q[1] - p[1])
                          for p, q in zip(lead, lead[1:])) or 1.0
                acc = 0.0
                for pa, qa in zip(lead, lead[1:]):
                    acc += math.hypot(qa[0] - pa[0], qa[1] - pa[1])
                    pre.append([t_start + span * acc / tot, qa[0], qa[1]])
                pts = pre[:-1] + pts
            # 이어 붙인 구간(카메라 사각을 돌아서 메운 자리)이 실제 공백 시간보다
            # 길어 그 구간만 튄다(실측 7.35 m/s). 도착은 못 박고 앞 시각을 당겨
            # **걸을 수 있는 속도**로 되돌린다.
            people.append({"id": f"P{idx + 1:02d}", "pts": pts, "exit_at": pts[-1][0],
                           "_k": k_now, "_t_arr": t_arr})
            idx += 1

    out = []
    for p in people:
        r = dt._resample(p, smooth_s=0.3)   # 이미 매끄럽다. 세게 다듬으면 모서리를
        r["xy"] = _despike(r["xy"])                      # 잘라 벽을 뚫는다
        r["xy"], mv = _push_out(df, r["xy"], m_per_px)
        moved_total += mv
        r["xy"] = [[round(a, 1), round(b, 1)] for a, b in r["xy"]]
        r["xy"] = _hug(df, r["xy"])                      # 반올림 뒤에 마지막 확인
        # **자르는 건 맨 마지막.** 옆으로 미는 손질이 점을 출구선에 스치게 만들 수
        # 있어서, 그 전에 자르면 나간 점이 뒤에 다시 나타난다(실측: 01 IDR 3명).
        ex = exits_px[p["_k"]]
        d = [dt._seg_dist(q, ex[0], ex[1]) * m_per_px for q in r["xy"]]
        # 문 앞 1.2m 에서 끊는다. 0.4m 까지 끌고 가면 혼잡 구간의 실측 잡음
        # (문 앞에서 서성이는 자리)을 그대로 타 점이 지그재그로 튄다
        # (실측: 03 CBS 에서 순간 58 m/s). 화면에서 1.2m 는 문을 지난 것으로 읽힌다.
        cut = next((k for k, v in enumerate(d) if v <= 1.2), len(d) - 1)
        r["xy"] = r["xy"][:max(2, cut + 1)]
        # 마지막 점이 곧 문을 지나는 순간이다 — 배정받은 통과 시각에 못 박는다
        t0 = p["_t_arr"] - (len(r["xy"]) - 1) * dt_s
        if t0 < 0:
            # 경로가 통과 시각보다 길다 — 경보 전에 출발할 수는 없으니 **길이를
            # 줄인다**(같은 모양을 조금 빠르게). t0 를 0 으로 자르면 끝 시각이
            # 밀려 문을 지나는 순간이 어긋난다(실측: 02 EPFI 우회 3명이 4초 늦음).
            n = max(3, int(p["_t_arr"] / dt_s) + 1)
            src = np.asarray(r["xy"], float)
            g = np.linspace(0, len(src) - 1, n)
            r["xy"] = [[round(float(np.interp(t, np.arange(len(src)), src[:, 0])), 1),
                        round(float(np.interp(t, np.arange(len(src)), src[:, 1])), 1)]
                       for t in g]
            t0 = 0.0
            r["xy"] = _hug(df, r["xy"])     # 줄이면서 모서리를 자를 수 있다
        r["t0"] = t0
        r["exit_at"] = p["_t_arr"]
        out.append(r)
    return {"n": len(out), "t_end": ideal["t_end"], "dt": dt_s, "hub": list(hub),
            "people": out, "choreo": spec["label"], "pushed": moved_total,
            "waypoint": None}
