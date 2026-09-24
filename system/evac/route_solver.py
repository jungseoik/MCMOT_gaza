# -*- coding: utf-8 -*-
"""경보 시점 재실자 위치 → 각자의 최단 피난경로 (맵 px polyline).

**CAD 원본(DWG/DXF)을 건드리지 않는다.** 벽은 이미 렌더된 `map.png` 에서 읽는다 —
그 그림 자체가 CAD 를 터치업까지 반영해 그린 결과물이라, 편집기가 쓰는 기하와
같은 것을 보게 된다. 덕분에 층마다 거리장을 즉석에서 만들 수 있고, CAD 편집기를
거치지 않은 층(floor.json 이 없는 층)도 맵만 있으면 된다.

알고리즘은 CAD 편집기(evac/core.py)와 같다:
  ① 격자화(벽 + clearance 팽창) → ② 출구 동시 시드 멀티소스 다익스트라(8방향)
  → ③ 거리장 내리막 역추적 → ④ String-Pulling(시야선)으로 계단 펴기

좌표계는 **맵 원본 px** 하나로 통일한다(엔진·세션·프런트가 모두 맵 px 를 쓴다).
도면 mm 로 오가면 층마다 bounds·축척을 들고 다녀야 해서 실수가 난다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# 벽 판정 임계(그레이). map.png 는 흰 배경(255)에 검은 선이라 넉넉히 잡아도 된다
# — 실측 3개 층 모두 250 이상이 94.8%, 벽선은 1.6% 다.
WALL_GRAY = 200
# 사람이 벽에 붙어 걷지 않도록 벽을 부풀리는 여유(m). CAD 편집기 CLEARANCE(1ft)와 동일.
CLEARANCE_M = 0.3048
# 격자 한 칸의 실거리(m). CAD 편집기는 50mm 인데 맵 px 해상도(1.9~3.0cm)를 고려해
# 비슷한 수준으로 잡되, 칸 수가 과하지 않게 한다(2000px 도면 → 약 500x500).
CELL_M = 0.08


@dataclass
class DistField:
    """한 층의 거리장. dist/grid 는 (cols, rows) — 인덱스는 격자 좌표."""
    dist: np.ndarray            # float32, 못 가는 칸은 inf
    wall: np.ndarray            # bool, True=벽
    cell_px: float              # 격자 한 칸이 맵 px 로 몇 px
    m_per_px: float
    shape_px: tuple[int, int]   # (w, h) 맵 px

    def to_cell(self, x_px, y_px):
        return int(x_px / self.cell_px), int(y_px / self.cell_px)

    def to_px(self, c, r):
        return ((c + 0.5) * self.cell_px, (r + 0.5) * self.cell_px)


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    """bool 마스크를 r 칸만큼 부풀린다 (체비셰프 근사 — 축별 누적 최대)."""
    if r <= 0:
        return mask
    out = mask.copy()
    for _ in range(r):
        out[1:, :] |= mask[:-1, :]
        out[:-1, :] |= mask[1:, :]
        out[:, 1:] |= mask[:, :-1]
        out[:, :-1] |= mask[:, 1:]
        mask = out.copy()
    return out


def build_distfield(map_png: str, exits_px, m_per_px: float, *,
                    cell_m: float = CELL_M, clearance_m: float = CLEARANCE_M,
                    wall_gray: int = WALL_GRAY, extra_walls=None,
                    carve_paths=None, carve_w_m: float = 1.2) -> DistField:
    """map.png + 출구선(맵 px) → 거리장.

    exits_px    : [[(x1,y1),(x2,y2)], ...] 출구 통과선 (맵 px)
    extra_walls : 추가로 막을 다각형 [[(x,y),...], ...] (맵 px)
    carve_paths : **뚫어야 할 통로** [[(x,y),...], ...] (맵 px). 보통 ① 맵 설정에서
                  사람이 그린 피난경로를 넣는다.

    carve_paths 가 왜 필요한가 — map.png 는 도면을 **그린 그림**이라 두 가지가 빠진다:
      · 편집기의 개구부 뚫기(문 열기)는 격자에만 적용되고 그림에는 안 들어간다
        (게다가 floor.json 에도 저장되지 않는다 — 복구할 길이 없다)
      · 문짝·문 스윙 호가 선으로 그려져 있어 **실제 문이 벽으로 잡힌다**
    사람이 그린 피난경로는 **실제 문을 지나도록 그어져 있으므로**, 그 선을 따라
    통로 폭만큼 뚫으면 진짜 문이 열린다. 실측: 사람 관측이 벽 위에 찍히는 비율이
    32.2% → 대폭 감소(아래 검증).
    """
    import cv2
    im = cv2.imread(map_png, cv2.IMREAD_GRAYSCALE)
    if im is None:
        raise FileNotFoundError(map_png)
    h_px, w_px = im.shape[:2]
    cell_px = max(1.0, cell_m / m_per_px)
    cols = int(math.ceil(w_px / cell_px))
    rows = int(math.ceil(h_px / cell_px))

    # 벽 마스크 — 격자 칸 안에 어두운 픽셀이 하나라도 있으면 벽으로 본다.
    # 평균이 아니라 최대(어두움)를 쓰는 이유: 얇은 벽선이 평균에 묻혀 사라지면
    # 사람이 벽을 통과하는 경로가 나온다. 과하게 막히는 쪽이 안전하다.
    dark = (im < wall_gray).astype(np.uint8)
    pad_h = rows * int(round(cell_px)) - h_px
    pad_w = cols * int(round(cell_px)) - w_px
    cpx = int(round(cell_px))
    if pad_h > 0 or pad_w > 0:
        dark = cv2.copyMakeBorder(dark, 0, max(0, pad_h), 0, max(0, pad_w),
                                  cv2.BORDER_CONSTANT, value=0)
    blk = dark[:rows * cpx, :cols * cpx].reshape(rows, cpx, cols, cpx)
    wall = blk.max(axis=(1, 3)).astype(bool).T            # (cols, rows)

    if extra_walls:
        for poly in extra_walls:
            m = np.zeros((rows, cols), np.uint8)
            pts = np.array([[int(x / cell_px), int(y / cell_px)] for x, y in poly], np.int32)
            cv2.fillPoly(m, [pts], 1)
            wall |= m.astype(bool).T

    wall = _dilate(wall, int(round(clearance_m / cell_m)))

    # 통로 뚫기 — clearance 팽창 **뒤에** 해야 한다. 앞서 뚫으면 팽창이 다시 덮는다.
    if carve_paths:
        half = max(1, int(round((carve_w_m / 2) / cell_m)))
        cm = np.zeros((rows, cols), np.uint8)
        for pts in carve_paths:
            q = [(int(x / cell_px), int(y / cell_px)) for x, y in pts]
            for i in range(len(q) - 1):
                cv2.line(cm, q[i], q[i + 1], 1, thickness=half * 2 + 1)
        wall &= ~cm.astype(bool).T

    # 출구 셀 — 선분을 따라 샘플링. 출구는 벽에 묻혀도 무조건 통행 가능으로 연다
    # (문틀 선 때문에 출구가 막히면 거리장이 통째로 무한대가 된다).
    seeds = []
    for (x1, y1), (x2, y2) in exits_px:
        n = max(2, int(math.hypot(x2 - x1, y2 - y1) / cell_px) + 1)
        for i in range(n + 1):
            t = i / n
            c = int((x1 + (x2 - x1) * t) / cell_px)
            r = int((y1 + (y2 - y1) * t) / cell_px)
            if 0 <= c < cols and 0 <= r < rows:
                wall[c, r] = False
                seeds.append((c, r))
    if not seeds:
        raise ValueError("출구가 없습니다 — 거리장을 만들 수 없습니다.")

    dist = _dijkstra(wall, seeds, cell_m)
    return DistField(dist=dist, wall=wall, cell_px=cell_px, m_per_px=m_per_px,
                     shape_px=(w_px, h_px))


def _dijkstra(wall: np.ndarray, seeds, cell_m: float) -> np.ndarray:
    """출구 전부를 dist=0 으로 동시 시드한 8방향 다익스트라 (실거리 m)."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra as sp_dijkstra

    cols, rows = wall.shape
    free = ~wall
    cs, rs = np.where(free)
    n = cs.size
    if n == 0:
        raise ValueError("통행 가능한 칸이 없습니다 — 벽 임계를 확인하세요.")
    idx = -np.ones((cols, rows), np.int64)
    idx[cs, rs] = np.arange(n)

    diag = cell_m * math.sqrt(2.0)
    ri, ci, data = [], [], []

    def add(a_idx, b_idx, w):
        a, b = a_idx.ravel(), b_idx.ravel()
        m = (a >= 0) & (b >= 0)
        ri.append(a[m]); ci.append(b[m]); data.append(np.full(int(m.sum()), w))

    add(idx[:-1, :], idx[1:, :], cell_m)
    add(idx[:, :-1], idx[:, 1:], cell_m)
    add(idx[:-1, :-1], idx[1:, 1:], diag)
    add(idx[:-1, 1:], idx[1:, :-1], diag)
    g = csr_matrix((np.concatenate(data), (np.concatenate(ri), np.concatenate(ci))),
                   shape=(n, n))
    src = [int(idx[c, r]) for (c, r) in seeds if idx[c, r] >= 0]
    if not src:
        raise ValueError("출구 셀이 전부 벽입니다.")
    d = sp_dijkstra(g, directed=False, indices=src, min_only=True)
    out = np.full((cols, rows), np.inf, np.float32)
    out[cs, rs] = d
    return out


def _los(wall: np.ndarray, a, b) -> bool:
    """시야선 — Bresenham. 대각 이동에서 양옆이 모두 벽이면 막힌 것으로 본다."""
    c0, r0 = a; c1, r1 = b
    dc, dr = abs(c1 - c0), abs(r1 - r0)
    sc = 1 if c0 < c1 else -1
    sr = 1 if r0 < r1 else -1
    err = dc - dr
    cols, rows = wall.shape
    while True:
        if not (0 <= c0 < cols and 0 <= r0 < rows) or wall[c0, r0]:
            return False
        if c0 != c1 and r0 != r1:
            if (wall[min(cols - 1, c0 + sc), r0] and wall[c0, min(rows - 1, r0 + sr)]):
                return False
        if c0 == c1 and r0 == r1:
            return True
        e2 = 2 * err
        if e2 > -dr:
            err -= dr; c0 += sc
        if e2 < dc:
            err += dc; r0 += sr


def _nearest_free(df: DistField, c, r, max_cells: int) -> tuple[int, int, int] | None:
    """벽 안·도달불가 지점이면 근처의 통행·도달 가능 칸을 찾는다 (8방향 BFS)."""
    from collections import deque
    cols, rows = df.wall.shape
    c = max(0, min(cols - 1, c)); r = max(0, min(rows - 1, r))
    if (not df.wall[c, r]) and np.isfinite(df.dist[c, r]):
        return c, r, 0
    seen = np.zeros((cols, rows), bool)
    q = deque([(c, r)]); seen[c, r] = True
    D = ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1))
    while q:
        cc, rr = q.popleft()
        if abs(cc - c) > max_cells or abs(rr - r) > max_cells:
            continue
        if (not df.wall[cc, rr]) and np.isfinite(df.dist[cc, rr]):
            return cc, rr, max(abs(cc - c), abs(rr - r))
        for dc, dr in D:
            nc, nr = cc + dc, rr + dr
            if 0 <= nc < cols and 0 <= nr < rows and not seen[nc, nr]:
                seen[nc, nr] = True; q.append((nc, nr))
    return None


# 벽 안에 찍힌 점을 빈 칸으로 끌어올 때 허용하는 최대 거리(m).
# 이보다 멀리 끌려갔으면 **그 점은 믿지 않는다** — 투영 오차로 벽 건너편 방에
# 스냅되면, 멀쩡해 보이지만 엉뚱한 출구로 향하는 경로가 하나 만들어진다.
# 그 경로가 전역 집합에 들어가면 근처를 지나는 다른 사람들까지 그쪽에 배정돼
# 한 명의 오차가 여러 명의 EPFI 를 오염시킨다. 못 만드는 것보다 나쁘다 —
# 못 만들면 눈에 보이지만(산출 불가 N명), 잘못 만들면 안 보인다.
SNAP_LIMIT_M = 0.5
SNAP_SEARCH_M = 1.5          # 빈 칸을 찾아보는 최대 반경 (이 안에서 찾되 위 한도로 거른다)


def solve_path(df: DistField, x_px: float, y_px: float, *,
               snap_limit_m: float = SNAP_LIMIT_M, max_steps: int = 20000):
    """한 지점 → 최단 피난경로.

    반환 {points(맵 px), dist_m, snap_m} 또는 None. None 의 이유는 두 가지고
    둘 다 "이 사람 경로는 못 만든다"로 같게 취급한다:
      · 도달 불가   — 벽에 막혔거나 출구와 끊긴 구역
      · 스냅 과다   — 벽 안에 찍혀 너무 멀리 끌어와야 함(위 SNAP_LIMIT_M 주석)

    거리장의 **내리막**을 따라 내려간다(pred 가 필요 없다 — 이웃 중 dist 최소로).
    그 결과는 격자 계단이라, CAD 편집기와 같은 String-Pulling 으로 편다.
    """
    cols, rows = df.wall.shape
    c, r = df.to_cell(x_px, y_px)
    cell_m = df.cell_px * df.m_per_px
    got = _nearest_free(df, c, r, int(SNAP_SEARCH_M / cell_m) + 1)
    if got is None:
        return None
    c, r, snap_cells = got
    snap_m = snap_cells * cell_m
    if snap_m > snap_limit_m:
        return None
    if not np.isfinite(df.dist[c, r]):
        return None

    D = ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1))
    raw = [(c, r)]
    total = float(df.dist[c, r])
    for _ in range(max_steps):
        cur = df.dist[c, r]
        if cur <= 1e-6:
            break
        best, bd = None, cur
        for dc, dr in D:
            nc, nr = c + dc, r + dr
            if 0 <= nc < cols and 0 <= nr < rows and df.dist[nc, nr] < bd:
                bd = df.dist[nc, nr]; best = (nc, nr)
        if best is None:
            break                              # 국소 최저 — 더 못 내려간다
        c, r = best
        raw.append((c, r))
    if len(raw) < 2:
        return None

    # String-Pulling — 시야선이 닿는 가장 먼 점까지 직선으로 잇는다
    sm = [raw[0]]
    a = 0
    while a < len(raw) - 1:
        far = a + 1
        for i in range(a + 2, len(raw)):
            if _los(df.wall, raw[a], raw[i]):
                far = i
            else:
                break
        sm.append(raw[far]); a = far
    return {"points": [[round(v, 1) for v in df.to_px(cc, rr)] for cc, rr in sm],
            "dist_m": round(total, 2), "snap_m": round(snap_m, 2)}
