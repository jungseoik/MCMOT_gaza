# -*- coding: utf-8 -*-
"""**이상적 궤적 합성** — 끊기지 않는 N명을 만든다 (발표 시각화 전용).

무엇을 하는 물건인가: 녹화된 트랙 파편(한 세션에 74~100개)을 이어 붙여
**사람 수만큼의 연속 궤적**으로 만든다. "글로벌 ID 가 완벽했다면 이렇게 보인다"를
보여주는 영상 편집 레이어다.

**지표는 이것을 쓰지 않는다.** 4대 지표·카운팅은 원 관측으로 이미 산출돼 있고
여기서 만든 궤적은 `demo_viz --tracks ideal` 의 좌측 도면 표출에만 들어간다.
수치를 만들어 내는 게 아니라 **보이는 선을 이어 주는** 것이다.

왜 필요한가: 카메라 경계마다 트랙 id 가 끊긴다. 그대로 그리면 점이 툭 사라졌다
다른 데서 튀어나와, 보는 사람은 "사람이 사라졌다"로 읽는다. 각본에는 10명이
처음부터 끝까지 있었다.

만드는 순서
  ① 파편 수집 — gid 별 (t, x, y). 0.4초 미만 조각은 버린다
  ② 잇기 — 끝난 자리에서 시간·거리가 말이 되는(≤2.2 m/s) 다음 조각을 붙인다
  ③ N개 고르기 — 오래 살아남았고 출구에서 끝나는 사슬을 우선
  ④ 메우기 — 사이가 빈 구간은 **벽을 피해** 잇는다(직선이 벽을 뚫으면 격자 A*)
  ⑤ 끝내기 — 마지막 점이 출구에서 멀면 최적 경로를 따라 걸어 나가 사라진다
  ⑥ **시작지점에 모으기** — 10명 전부 경보 순간에는 출발 자리에 뭉쳐 있다가,
     자기 차례에 거기서 걸어 나간다. 첫 관측이 건물 한복판인 사람도 그 자리에
     가만히 서 있지 않는다 — 시작지점에서 걸어와 자기 동선에 올라탄다
  ⑦ 다듬기 — 0.1초 격자로 리샘플 + 이동평균. 관측 5fps 의 덜컥거림을 없앤다
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ANN = ROOT / "results/demo/_annotate"

N_PEOPLE = 10          # 각본 인원 — 3층 각본은 10명, 1층 각본도 10명
MIN_FRAG_S = 0.4       # 이보다 짧은 조각은 잡음
GAP_MAX_S = 14.0       # 이 정도 공백까지는 같은 사람으로 본다(카메라 사각)
V_LINK = 2.2           # 잇기 허용 속도 m/s — 이보다 빠르면 다른 사람이다
OVERLAP_S = 1.5        # 두 카메라가 겹쳐 본 구간 허용
OVERLAP_M = 2.0
WALK_MPS = 1.3         # 마무리로 걸어 나갈 때 속도
FILL_MPS = 1.6         # 카메라 사각을 메울 때 걷는 속도
DT = 0.1               # 출력 시간 격자
SMOOTH_S = 0.9         # 이동평균 창
EXIT_NEAR_M = 2.0      # 출구선에 이만큼 붙으면 '나갔다'
EXIT_WALK_M = 10.0     # 경로 산출이 실패해도 이 거리 안이면 출구선까지 곧장 걸어 나간다
EXIT_PAST_M = 1.5      # 선을 넘어 이만큼 더 가고 사라진다 — 문턱에서 증발하면 이상하다
HUB_R_M = 7.0          # 시작지점 무리로 볼 반경
HUB_SPREAD_M = 1.6     # 시작지점에서 10명이 퍼져 서 있는 정도 (겹쳐 그리면 한 점으로 보인다)
HUB_WALK_MPS = 1.5     # 시작지점에서 자기 동선까지 걸어 나가는 속도


def _seg_foot(p, a, b):
    """점 p 에서 선분 ab 에 내린 발 — (거리 px, 발점)."""
    ax, ay = a; bx, by = b; px, py = p
    vx, vy = bx - ax, by - ay
    L = vx * vx + vy * vy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / L))
    fx, fy = ax + vx * t, ay + vy * t
    return math.hypot(px - fx, py - fy), (fx, fy)


def _seg_dist(p, a, b):
    """점 p 와 선분 ab 의 거리 (px)."""
    return _seg_foot(p, a, b)[0]


# ── ② 잇기 ────────────────────────────────────────────────────────────────
def _chain(frags, m_per_px):
    """파편 목록 → 사슬 목록. 시작이 이른 것부터 탐욕적으로 붙인다.

    붙일 후보가 여럿이면 **필요 속도가 가장 느린** 쪽을 고른다 — 사람은 보통
    지름길로 걷지 순간이동하지 않는다.
    """
    frags = sorted(frags, key=lambda f: f[0][0])
    chains: list[list] = []
    for fr in frags:
        t_s, x_s, y_s = fr[0]
        best, best_cost = None, 1e9
        for ch in chains:
            t_e, x_e, y_e = ch[-1]
            gap = t_s - t_e
            if gap > GAP_MAX_S:
                continue
            d_m = math.hypot(x_s - x_e, y_s - y_e) * m_per_px
            if gap < 0:                       # 시간이 겹친다 = 두 카메라가 같이 봤다
                if -gap > OVERLAP_S or d_m > OVERLAP_M:
                    continue
                cost = d_m
            else:
                if d_m > V_LINK * max(gap, 0.2):
                    continue
                cost = d_m / max(gap, 0.2) + gap * 0.05
            if cost < best_cost:
                best, best_cost = ch, cost
        if best is None:
            chains.append(list(fr))
        else:
            best.extend(q for q in fr if q[0] > best[-1][0])
    return chains


# ── ④ 벽을 피해 메우기 ────────────────────────────────────────────────────
def _astar(wall, a, b, limit=40000):
    """격자 A* — 직선이 벽을 뚫을 때만 쓴다."""
    import heapq
    W, H = wall.shape
    if not (0 <= a[0] < W and 0 <= a[1] < H and 0 <= b[0] < W and 0 <= b[1] < H):
        return None
    h = lambda c: math.hypot(c[0] - b[0], c[1] - b[1])    # noqa: E731
    open_ = [(h(a), 0.0, a)]
    came, g = {a: None}, {a: 0.0}
    seen = 0
    while open_:
        _, gc, cur = heapq.heappop(open_)
        if cur == b:
            out = []
            while cur is not None:
                out.append(cur); cur = came[cur]
            return out[::-1]
        seen += 1
        if seen > limit:
            return None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == dy == 0:
                    continue
                nc = (cur[0] + dx, cur[1] + dy)
                if not (0 <= nc[0] < W and 0 <= nc[1] < H) or wall[nc]:
                    continue
                ng = gc + math.hypot(dx, dy)
                if ng < g.get(nc, 1e18):
                    g[nc] = ng; came[nc] = cur
                    heapq.heappush(open_, (ng + h(nc), ng, nc))
    return None


def _fill(df, p0, p1):
    """p0 → p1 사이를 잇는 중간점들(px). 직선이 통하면 빈 목록."""
    from system.evac.route_solver import _los, _nearest_free
    c0 = df.to_cell(*p0); c1 = df.to_cell(*p1)
    if _los(df.wall, c0, c1):
        return []
    n0 = _nearest_free(df, *c0, 20); n1 = _nearest_free(df, *c1, 20)
    if n0 is None or n1 is None:
        return []
    cells = _astar(df.wall, (n0[0], n0[1]), (n1[0], n1[1]))
    if not cells:
        return []
    step = max(1, len(cells) // 24)
    return [list(df.to_px(c, r)) for c, r in cells[1:-1:step]]



# ── ⑥ 시작지점 ────────────────────────────────────────────────────────────
def _hub(chains, m_per_px):
    """10명이 출발한 자리. 사슬 **첫 점들이 가장 빽빽한 곳**의 무게중심.

    각본은 다 "한 곳에 모여 있다가 경보가 울리면 나간다"다. 그런데 트래커의
    첫 관측은 사람마다 제각각이라(어떤 사람은 복도 한복판에서 처음 잡힌다) 그걸
    그대로 출발 자리로 쓰면 건물 여기저기에 점이 박혀 있다 합류하는 그림이 된다.
    실제로는 그런 일이 없었다.
    """
    firsts = [(c[0][1], c[0][2], c[0][0]) for c in chains]
    r_px = HUB_R_M / m_per_px
    best, best_n = None, -1
    for x, y, _ in firsts:
        near = [(a, b) for a, b, _ in firsts if math.hypot(a - x, b - y) <= r_px]
        if len(near) > best_n:
            best_n, best = len(near), near
    return (sum(p[0] for p in best) / len(best),
            sum(p[1] for p in best) / len(best)), best_n


def _hub_slot(i, n, hub, m_per_px):
    """시작지점에서 i번째 사람이 설 자리 — 겹쳐 그리면 10명이 한 점으로 보인다."""
    r = HUB_SPREAD_M / m_per_px
    ang = 2 * math.pi * i / max(1, n)
    ring = 0.45 + 0.55 * ((i % 3) / 2.0)
    return (hub[0] + math.cos(ang) * r * ring, hub[1] + math.sin(ang) * r * ring)


def _walk_path(df, a, b):
    """a → b 사람이 걸어갈 만한 점들. 벽이 막으면 돌아간다."""
    mid = _fill(df, a, b) if df is not None else []
    return [list(a)] + mid + [list(b)]


def _path_sec(pts, m_per_px, mps):
    d = sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(pts, pts[1:]))
    return d * m_per_px / mps


def _lead_in(df, start, chain, m_per_px):
    """시작지점 → 사슬 합류점. (앞머리 점들, 합류 인덱스) 를 돌려준다.

    합류점을 뒤로 미루는 이유: 첫 관측이 이미 시작지점에서 먼 사람은 "경보와
    동시에 저기 있었다"는 뜻인데, 이상적 그림에서는 그럴 수 없다. 걸어서 닿을 수
    있는 첫 지점까지 사슬 앞머리를 잘라낸다.
    """
    step = max(1, len(chain) // 20)
    for k in range(0, len(chain), step):
        t_k, x_k, y_k = chain[k]
        pts = _walk_path(df, start, (x_k, y_k))
        sec = _path_sec(pts, m_per_px, HUB_WALK_MPS)
        if sec <= t_k:
            return pts, sec, k
    # 어느 지점에도 제때 못 닿으면 마지막 점에 붙인다(그래도 서 있진 않는다)
    t_k, x_k, y_k = chain[-1]
    pts = _walk_path(df, start, (x_k, y_k))
    return pts, min(_path_sec(pts, m_per_px, HUB_WALK_MPS), max(t_k, 1.0)), len(chain) - 1


# ── 본체 ──────────────────────────────────────────────────────────────────
def build(frames, site, df, *, t0: float, n_people: int = N_PEOPLE,
          m_per_px: float, exits_px) -> dict:
    """녹화 프레임 → 이상적 궤적 N개.

    frames: run_replay 가 돌려준 경량 프레임(ts + objects[gid,x,y])
    df:     route_solver.DistField (벽·거리장). 없으면 직선으로만 메운다
    """
    import collections

    raw = collections.defaultdict(list)
    for f in frames:
        for o in f["objects"]:
            raw[o["gid"]].append((f["ts"] - t0, float(o["x"]), float(o["y"])))
    frags = [sorted(v) for v in raw.values()
             if len(v) >= 2 and v[-1][0] - v[0][0] >= MIN_FRAG_S]
    # **출구에서 거꾸로 나오는 조각**은 빼고 잇는다. 계단 쪽 카메라가 내려간
    # 사람을 다시 잡거나 되돌아 들어온 사람을 문 근처에서 새로 잡은 것인데,
    # 그대로 이으면 "출구에서 누가 역으로 나와 합류하는" 그림이 된다(실측:
    # 02 EPFI 의 cam13:9·cam13:11 이 출구 2m 에서 13m 로 멀어졌다).
    def _d_exit(q):
        return min(_seg_dist((q[1], q[2]), e[0], e[1]) for e in exits_px) * m_per_px

    def _out(fr):
        return _d_exit(fr[0]) < 4.0 and _d_exit(fr[-1]) > 8.0

    frags = [f for f in frags if not _out(f)]
    chains = _chain(frags, m_per_px)

    # ③ N개 고르기 — 출구에서 끝나고, 오래 살아남고, 일찍 시작한 사슬이 먼저
    def near_exit(p):
        return min((_seg_dist(p, e[0], e[1]) for e in exits_px), default=1e9) * m_per_px

    scored = []
    for ch in chains:
        dur = ch[-1][0] - ch[0][0]
        ex = near_exit((ch[-1][1], ch[-1][2]))
        scored.append((-(dur + (25.0 if ex < EXIT_NEAR_M * 2 else 0.0)), ch[0][0], ch))
    scored.sort(key=lambda s: (s[0], s[1]))
    picked = [s[2] for s in scored[:n_people]]
    hub, hub_n = _hub(picked, m_per_px)

    t_end = max(f["ts"] for f in frames) - t0
    people = []
    for i, ch in enumerate(picked):
        pts = [[p[0], p[1], p[2]] for p in ch]

        # ④ 빈 구간 메우기 — 직선이 벽을 뚫으면 격자를 돌아서
        filled, shift = [pts[0]], 0.0
        for a, b in zip(pts, pts[1:]):
            gap_t, gap_d = b[0] - a[0], math.hypot(b[1] - a[1], b[2] - a[2]) * m_per_px
            if gap_t > 0.6 and gap_d > 1.0 and df is not None:
                mid = _fill(df, (a[1], a[2]), (b[1], b[2]))
                if mid:
                    # 돌아가는 길은 직선보다 훨씬 길다. 원래 공백 시간에 욱여넣으면
                    # 그 구간만 전력 질주가 된다(실측 7~28 m/s). 걸리는 만큼 시간을
                    # 주고 **뒤를 통째로 민다** — 끝은 어차피 통과 시각에 맞춘다.
                    way = [[a[1], a[2]]] + mid + [[b[1], b[2]]]
                    need = sum(math.hypot(q[0] - p[0], q[1] - p[1])
                               for p, q in zip(way, way[1:])) * m_per_px / FILL_MPS
                    extra = max(0.0, need - gap_t)
                    span = gap_t + extra
                    acc = 0.0
                    for k, mp in enumerate(mid, 1):
                        acc += math.hypot(way[k][0] - way[k - 1][0],
                                          way[k][1] - way[k - 1][1])
                        tot = sum(math.hypot(q[0] - p[0], q[1] - p[1])
                                  for p, q in zip(way, way[1:])) or 1.0
                        filled.append([a[0] + shift + span * acc / tot, mp[0], mp[1]])
                    shift += extra
            filled.append([b[0] + shift, b[1], b[2]])
        pts = filled

        # ⑤ 출구까지 마저 걸어 나간다 — 도중에 증발하지 않게
        last = pts[-1]
        exited_at = None
        if near_exit((last[1], last[2])) < EXIT_NEAR_M:
            exited_at = last[0]
        else:
            walk = None
            if df is not None:
                from system.evac.route_solver import solve_path
                r = solve_path(df, last[1], last[2])
                if r and len(r["points"]) > 1:
                    walk = r["points"]
            if walk is None:
                # 경로 산출이 실패하는 자리가 있다 — 계단실 안쪽은 여유폭을 부풀리면
                # 통째로 '벽'이 된다. 실측상 그런 사람들도 출구선에서 2~6m 거리다.
                # 발표 영상에서 문 앞 3m 에 멈춰 굳어 있는 것보다 걸어 나가는 게 맞다.
                d_px, foot = min(
                    (_seg_foot((last[1], last[2]), e[0], e[1]) for e in exits_px),
                    key=lambda z: z[0])
                if d_px * m_per_px <= EXIT_WALK_M:
                    ux = (foot[0] - last[1]) / max(d_px, 1e-6)
                    uy = (foot[1] - last[2]) / max(d_px, 1e-6)
                    past = EXIT_PAST_M / m_per_px
                    walk = [[last[1], last[2]],
                            [foot[0] + ux * past, foot[1] + uy * past]]
            if walk:
                t = last[0]
                for p, q in zip(walk, walk[1:]):
                    t += math.hypot(q[0] - p[0], q[1] - p[1]) * m_per_px / WALK_MPS
                    pts.append([t, q[0], q[1]])
                exited_at = t

        # ⑥ 경보 순간엔 **시작지점에 뭉쳐** 있다가 자기 차례에 걸어 나간다.
        # 첫 관측 자리에 세워 두면 건물 여기저기 점이 박혀 있다 합류하는,
        # 실제로는 없었던 그림이 된다.
        slot = _hub_slot(i, len(picked), hub, m_per_px)
        lead, sec, k = _lead_in(df, slot, pts, m_per_px)
        pts = pts[k:]
        t_join = pts[0][0]
        t_dep = max(0.0, t_join - sec)
        head = [[0.0, slot[0], slot[1]]]                 # 경보 순간 — 제자리
        if t_dep > 0.05:
            head.append([t_dep, slot[0], slot[1]])       # 자기 차례까지 대기
        span = max(t_join - t_dep, 0.2)
        acc, segs = 0.0, []
        total = sum(math.hypot(q[0] - p[0], q[1] - p[1])
                    for p, q in zip(lead, lead[1:])) or 1.0
        for pa, qa in zip(lead, lead[1:]):
            acc += math.hypot(qa[0] - pa[0], qa[1] - pa[1])
            segs.append([t_dep + span * acc / total, qa[0], qa[1]])
        pts = head + segs[:-1] + pts

        people.append({"id": f"P{i + 1:02d}", "pts": pts,
                       "exit_at": exited_at, "t_end": min(pts[-1][0], t_end + 6.0)})

    return {"n": len(people), "t_end": t_end, "dt": DT,
            "hub": [round(hub[0], 1), round(hub[1], 1)], "hub_n": hub_n,
            "people": [_resample(p) for p in people]}


def _resample(person, smooth_s: float = SMOOTH_S):
    """0.1초 격자로 리샘플 + 이동평균 — 관측 5fps 의 덜컥거림을 없앤다."""
    pts = person["pts"]
    t_a, t_b = pts[0][0], pts[-1][0]
    if t_b <= t_a:
        return {**person, "t0": t_a, "xy": [[pts[0][1], pts[0][2]]]}
    ts = np.array([p[0] for p in pts])
    xs = np.array([p[1] for p in pts]); ys = np.array([p[2] for p in pts])
    grid = np.arange(t_a, t_b + DT * 0.5, DT)
    gx = np.interp(grid, ts, xs); gy = np.interp(grid, ts, ys)
    w = max(3, int(smooth_s / DT) | 1)
    if len(gx) > w:
        k = np.ones(w) / w
        pad = w // 2
        gx = np.convolve(np.pad(gx, pad, mode="edge"), k, "valid")
        gy = np.convolve(np.pad(gy, pad, mode="edge"), k, "valid")
    return {"id": person["id"], "t0": float(t_a), "exit_at": person["exit_at"],
            "xy": [[round(float(a), 1), round(float(b), 1)] for a, b in zip(gx, gy)]}


def path(session_id: str) -> Path:
    return ANN / f"ideal_{session_id}.json"


def save(session_id: str, data: dict) -> Path:
    p = path(session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def load(session_id: str) -> dict | None:
    p = path(session_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def at(data: dict, t: float):
    """t 시점의 [{id, x, y}] — 아직 안 나간 사람만.

    궤적은 0.1초 격자인데 영상은 30fps 다. 반올림해서 집으면 같은 자리를 세 번
    그리다 툭 건너뛰어 **자글자글해 보인다**. 사이를 선형으로 메워 매 프레임
    조금씩 움직이게 한다 — 좌표를 지어내는 게 아니라 이미 정해진 두 점 사이를
    지날 뿐이고, 지표 계산에는 쓰지 않는다.
    """
    out = []
    for p in data["people"]:
        u = (t - p["t0"]) / data["dt"]
        if u < 0:
            # 기본은 첫 자리에 세워 둔다(경보 순간부터 거기 있었다는 각본).
            # 계단에서 **나오는** 각본은 나오기 전엔 보이면 안 된다.
            if not data.get("hold", True):
                continue
            u = 0.0
        i = int(u)
        if i >= len(p["xy"]):
            continue                              # 나갔다
        a = p["xy"][i]
        b = p["xy"][min(len(p["xy"]) - 1, i + 1)]
        f = u - i
        out.append({"gid": p["id"],
                    "x": a[0] + (b[0] - a[0]) * f,
                    "y": a[1] + (b[1] - a[1]) * f})
    return out


def trail(data: dict, p: dict, t: float, back: float | None = None):
    """그 사람이 t 까지 지나온 자취. back 이 None 이면 전 구간."""
    i = int(round((t - p["t0"]) / data["dt"]))
    if i <= 0:
        return []
    j = 0 if back is None else max(0, i - int(back / data["dt"]))
    return p["xy"][j:min(i, len(p["xy"]))]
