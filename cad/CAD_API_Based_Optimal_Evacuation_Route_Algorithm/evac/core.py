"""
evac.core — 피난경로 알고리즘 코어 (순수 numpy/scipy, CAD·플롯 의존 없음).

삼성 TravelDistance_Analyzer(C#)와 동일 로직:
  격자화(mesh) → 멀티소스 다익스트라(8방향) → prev 역추적 + String-Pulling(LOS).

이 모듈은 좌표/도면 포맷을 모른다. 입력은 "도면단위(mm) 선분·점"과 범위(bounds)뿐이라
어느 파이프라인에서도 그대로 가져다 쓸 수 있다(예: 트래킹 좌표를 start로 주입).
"""
import math
from collections import deque

import numpy as np

# 원본 상수 (기본값; 모두 함수 인자로 재정의 가능)
CELL_SIZE = 50.0        # mm
CLEARANCE = 304.8       # mm (=1ft)


def world_to_grid(x, y, minx, miny, cell):
    return int(math.floor((x - minx) / cell)), int(math.floor((y - miny) / cell))


def grid_to_world(c, r, minx, miny, cell):
    return (minx + (c + 0.5) * cell, miny + (r + 0.5) * cell)


# ───────────────────────────────────────────── 격자화: 벽=True(1), 공간=False(0)
def build_obstacle_grid(obstacles, minx, miny, cols, rows, cell,
                        clearance=CLEARANCE, exit_cells=()):
    """선분 리스트(N,4) → bool[cols,rows]. 셀중심~선분 거리 < clearance 면 벽.
    exit_cells 는 강제로 통행가능(원본과 동일)."""
    grid = np.zeros((cols, rows), dtype=bool)
    m = clearance
    for (x1, y1, x2, y2) in obstacles:
        bx1, by1 = min(x1, x2) - m, min(y1, y2) - m
        bx2, by2 = max(x1, x2) + m, max(y1, y2) + m
        c1 = max(0, int(math.floor((bx1 - minx) / cell)))
        r1 = max(0, int(math.floor((by1 - miny) / cell)))
        c2 = min(cols - 1, int(math.floor((bx2 - minx) / cell)))
        r2 = min(rows - 1, int(math.floor((by2 - miny) / cell)))
        if c2 < c1 or r2 < r1:
            continue
        cc = minx + (np.arange(c1, c2 + 1) + 0.5) * cell
        rr = miny + (np.arange(r1, r2 + 1) + 0.5) * cell
        PX, PY = np.meshgrid(cc, rr, indexing="ij")
        dx, dy = x2 - x1, y2 - y1
        lensq = dx * dx + dy * dy
        if lensq < 1e-10:
            d = np.hypot(PX - x1, PY - y1)
        else:
            t = np.clip(((PX - x1) * dx + (PY - y1) * dy) / lensq, 0.0, 1.0)
            d = np.hypot(PX - (x1 + t * dx), PY - (y1 + t * dy))
        grid[c1:c2 + 1, r1:r2 + 1] |= (d < clearance)
    for (c, r) in exit_cells:
        if 0 <= c < cols and 0 <= r < rows:
            grid[c, r] = False
    return grid


def carve_free(grid, lines, minx, miny, cell, width=900.0):
    """개구부(문) 마커: 선분 주변 width/2 이내 셀을 강제 통행가능 처리.

    CAD에서 문짝을 지우는 터치업의 격자 레벨 등가물 — 원본 도면 무손상.
    lines = [(x1,y1,x2,y2), ...] (도면단위 mm). grid 를 in-place 수정."""
    cols, rows = grid.shape
    r = width / 2.0
    for (x1, y1, x2, y2) in lines:
        bx1, by1 = min(x1, x2) - r, min(y1, y2) - r
        bx2, by2 = max(x1, x2) + r, max(y1, y2) + r
        c1 = max(0, int(math.floor((bx1 - minx) / cell)))
        r1 = max(0, int(math.floor((by1 - miny) / cell)))
        c2 = min(cols - 1, int(math.floor((bx2 - minx) / cell)))
        r2 = min(rows - 1, int(math.floor((by2 - miny) / cell)))
        if c2 < c1 or r2 < r1:
            continue
        cc = minx + (np.arange(c1, c2 + 1) + 0.5) * cell
        rr = miny + (np.arange(r1, r2 + 1) + 0.5) * cell
        PX, PY = np.meshgrid(cc, rr, indexing="ij")
        dx, dy = x2 - x1, y2 - y1
        lensq = dx * dx + dy * dy
        if lensq < 1e-10:
            d = np.hypot(PX - x1, PY - y1)
        else:
            t = np.clip(((PX - x1) * dx + (PY - y1) * dy) / lensq, 0.0, 1.0)
            d = np.hypot(PX - (x1 + t * dx), PY - (y1 + t * dy))
        grid[c1:c2 + 1, r1:r2 + 1] &= ~(d < r)
    return grid


# ───────────────────────────────────────────── 편집 도형(뚫기·막기)
def _shape_mask(shape, minx, miny, cell, cols, rows):
    """편집 도형 → (슬라이스, bool 마스크). 범위 밖이면 None.

    kind: line(선분 주변 w/2) · rect(대각 2점) · poly(N점 다각형).
    rect는 poly의 특수형이지만 슬라이싱만으로 끝나 훨씬 빠르므로 따로 둔다.
    """
    kind = shape.get("kind", "line")
    pts = [float(v) for v in shape.get("pts", [])]
    if kind == "line":
        if len(pts) < 4:
            return None
        x1, y1, x2, y2 = pts[:4]
        r = float(shape.get("w", 900.0)) / 2.0
        bx1, by1, bx2, by2 = (min(x1, x2) - r, min(y1, y2) - r,
                              max(x1, x2) + r, max(y1, y2) + r)
    elif kind == "rect":
        if len(pts) < 4:
            return None
        x1, y1, x2, y2 = pts[:4]
        bx1, by1, bx2, by2 = min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
    elif kind == "poly":
        if len(pts) < 6:
            return None
        xs, ys = pts[0::2], pts[1::2]
        bx1, by1, bx2, by2 = min(xs), min(ys), max(xs), max(ys)
    else:
        return None

    c1 = max(0, int(math.floor((bx1 - minx) / cell)))
    r1 = max(0, int(math.floor((by1 - miny) / cell)))
    c2 = min(cols - 1, int(math.floor((bx2 - minx) / cell)))
    r2 = min(rows - 1, int(math.floor((by2 - miny) / cell)))
    if c2 < c1 or r2 < r1:
        return None
    sl = (slice(c1, c2 + 1), slice(r1, r2 + 1))

    if kind == "rect":                       # bbox 전체가 곧 사각형
        return sl, True

    cc = minx + (np.arange(c1, c2 + 1) + 0.5) * cell
    rr = miny + (np.arange(r1, r2 + 1) + 0.5) * cell
    PX, PY = np.meshgrid(cc, rr, indexing="ij")

    if kind == "line":
        x1, y1, x2, y2 = pts[:4]
        r = float(shape.get("w", 900.0)) / 2.0
        dx, dy = x2 - x1, y2 - y1
        lensq = dx * dx + dy * dy
        if lensq < 1e-10:
            d = np.hypot(PX - x1, PY - y1)
        else:
            t = np.clip(((PX - x1) * dx + (PY - y1) * dy) / lensq, 0.0, 1.0)
            d = np.hypot(PX - (x1 + t * dx), PY - (y1 + t * dy))
        return sl, (d < r)

    # poly — ray casting (짝수/홀수 규칙)
    xs, ys = np.array(pts[0::2]), np.array(pts[1::2])
    inside = np.zeros(PX.shape, dtype=bool)
    n = len(xs)
    for i in range(n):
        j = (i - 1) % n
        cond = ((ys[i] > PY) != (ys[j] > PY))
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (xs[j] - xs[i]) * (PY - ys[i]) / (ys[j] - ys[i]) + xs[i]
        inside ^= cond & (PX < xint)
    return sl, inside


def apply_shapes(grid, shapes, minx, miny, cell):
    """편집 도형을 격자에 적용 — in-place.

    op="open"  → 통행 가능(False)으로 뚫는다 (carve_free의 일반화).
    op="block" → 통행 불가(True)로 막는다  (그 대칭 연산).

    **open을 먼저, block을 나중에** 적용한다. 그래야 "문은 뚫려 있으나 지금은
    잠김/적치물로 막힘" 같은 현장 상황을 표현할 수 있다. 순서를 바꾸면 뚫기가
    항상 이겨 차단이 무의미해진다.

    도면 원본(엔티티)은 건드리지 않는다 — 통행 판정 격자에만 반영되므로
    map.png 그림과 CAD 파일은 그대로다.
    """
    if not shapes:
        return grid
    cols, rows = grid.shape
    for op, value in (("open", False), ("block", True)):
        for sh in shapes:
            if sh.get("op", "open") != op:
                continue
            m = _shape_mask(sh, minx, miny, cell, cols, rows)
            if m is None:
                continue
            sl, mask = m
            if mask is True:                 # rect — 슬라이스 전체
                grid[sl] = value
            elif value:
                grid[sl] |= mask
            else:
                grid[sl] &= ~mask
    return grid


def legacy_openings_to_shapes(openings, width=900.0):
    """구 형식 openings([[x1,y1,x2,y2], ...]) → shapes 승격."""
    return [{"op": "open", "kind": "line", "pts": list(map(float, o)), "w": width}
            for o in (openings or [])]


# ───────────────────────────────────────────── 멀티소스 다익스트라 (scipy)
def run_dijkstra(grid, exit_cells, cell):
    """모든 exit_cells 를 dist=0 으로 동시 시드. 8방향(직교 cell, 대각 cell·√2).
    반환: dist[cols,rows], pred(node), inv_c(node), inv_r(node), idx[cols,rows]."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra

    cols, rows = grid.shape
    free = ~grid
    cs, rs = np.where(free)
    n = cs.size
    idx = -np.ones((cols, rows), dtype=np.int64)
    idx[cs, rs] = np.arange(n)

    diag = cell * math.sqrt(2.0)
    ri, ci, data = [], [], []

    def add(a_idx, b_idx, w):
        a = a_idx.ravel(); b = b_idx.ravel()
        mask = (a >= 0) & (b >= 0)
        ri.append(a[mask]); ci.append(b[mask]); data.append(np.full(int(mask.sum()), w))

    add(idx[:-1, :], idx[1:, :], cell)
    add(idx[:, :-1], idx[:, 1:], cell)
    add(idx[:-1, :-1], idx[1:, 1:], diag)
    add(idx[:-1, 1:], idx[1:, :-1], diag)

    g = csr_matrix((np.concatenate(data), (np.concatenate(ri), np.concatenate(ci))),
                   shape=(n, n))

    sources = [int(idx[c, r]) for (c, r) in exit_cells
               if 0 <= c < cols and 0 <= r < rows and idx[c, r] >= 0]
    if not sources:
        raise ValueError("통행가능한 Exit 셀이 없음(Exit가 벽에 묻힘).")

    dist_flat, pred, _ = dijkstra(g, directed=False, indices=sources,
                                  min_only=True, return_predecessors=True)
    dist = np.full((cols, rows), np.inf)
    dist[cs, rs] = dist_flat
    return dist, pred, cs, rs, idx


# ───────────────────────────────────────────── 시야확인 + String-Pulling
def has_line_of_sight(grid, a, b):
    c0, r0 = a; c1, r1 = b
    dc = abs(c1 - c0); dr = abs(r1 - r0)
    sc = 1 if c0 < c1 else -1
    sr = 1 if r0 < r1 else -1
    err = dc - dr
    while True:
        if grid[c0, r0]:
            return False
        if c0 != c1 and r0 != r1:
            if grid[c0 + sc, r0] and grid[c0, r0 + sr]:
                return False
        if c0 == c1 and r0 == r1:
            break
        e2 = 2 * err
        if e2 > -dr:
            err -= dr; c0 += sc
        if e2 < dc:
            err += dc; r0 += sr
    return True


def trace_path(pred, inv_c, inv_r, grid, start_node):
    raw = []
    node = int(start_node)
    guard = 0
    while node >= 0 and guard < 500000:
        guard += 1
        raw.append((int(inv_c[node]), int(inv_r[node])))
        p = pred[node]
        if p < 0:
            break
        node = int(p)
    if len(raw) < 2:
        return raw
    smoothed = [raw[0]]
    anchor = 0
    while anchor < len(raw) - 1:
        farthest = anchor + 1
        for i in range(anchor + 2, len(raw)):
            if has_line_of_sight(grid, raw[anchor], raw[i]):
                farthest = i
            else:
                break
        smoothed.append(raw[farthest])
        anchor = farthest
    return smoothed


def nearest_reachable(grid, dist, oc, or_, clearance, cell):
    """출발점 셀이 벽/도달불가면 8방향 BFS로 가장 가까운 통행·도달가능 셀 탐색."""
    cols, rows = grid.shape
    oc = max(0, min(cols - 1, oc)); or_ = max(0, min(rows - 1, or_))
    if (not grid[oc, or_]) and np.isfinite(dist[oc, or_]):
        return oc, or_
    max_radius = int(math.ceil(clearance * 3 / cell))
    visited = np.zeros((cols, rows), bool)
    q = deque([(oc, or_)]); visited[oc, or_] = True
    dc = [0, 0, 1, -1, 1, 1, -1, -1]; dr = [1, -1, 0, 0, 1, -1, 1, -1]
    while q:
        c, r = q.popleft()
        if abs(c - oc) > max_radius or abs(r - or_) > max_radius:
            continue
        if (not grid[c, r]) and np.isfinite(dist[c, r]):
            return c, r
        for i in range(8):
            nc, nr = c + dc[i], r + dr[i]
            if 0 <= nc < cols and 0 <= nr < rows and not visited[nc, nr]:
                visited[nc, nr] = True; q.append((nc, nr))
    return None


# ───────────────────────────────────────────── 고수준 API
class Analysis:
    """analyze() 결과 컨테이너. paths: [{'path_m','dist_mm','is_pass','start_m'}]."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


def analyze(obstacles, exits, bounds, *, starts=None, mode="occupant",
            worst_n=5, cell=CELL_SIZE, clearance=CLEARANCE,
            threshold_mm=30000.0, max_cells=30_000_000,
            openings=None, opening_width=900.0, shapes=None):
    """
    피난경로 산출 (재사용 진입점).

    obstacles : (N,4) 선분 [x1,y1,x2,y2] (도면단위 mm)
    exits     : [(x,y), ...] 도착점(도면단위)
    bounds    : (minx,miny,maxx,maxy) 탐색범위(도면단위)
    starts    : occupant 모드 출발점 [(x,y)]. None이면 worstn 필요.
    mode      : 'occupant' | 'worstn'
    반환      : Analysis(paths, grid, dist, bounds, cols, rows, cell, clearance,
                         n_free, n_wall, skipped)
    """
    minx, miny, maxx, maxy = bounds
    cols = int(math.ceil((maxx - minx) / cell))
    rows = int(math.ceil((maxy - miny) / cell))
    if cols * rows > max_cells:
        raise ValueError(f"격자 과대({cols}x{rows}) — cell 키우거나 bounds 축소.")

    exit_cells = list({world_to_grid(x, y, minx, miny, cell) for (x, y) in exits})
    grid = build_obstacle_grid(obstacles, minx, miny, cols, rows, cell,
                               clearance, exit_cells)
    if openings:
        carve_free(grid, openings, minx, miny, cell, width=opening_width)
    if shapes:                       # 뚫기·막기 편집 도형 (open → block 순)
        apply_shapes(grid, shapes, minx, miny, cell)
        for (c, r) in exit_cells:    # Exit는 막히면 안 된다 — 항상 통행 가능
            if 0 <= c < cols and 0 <= r < rows:
                grid[c, r] = False
    dist, pred, inv_c, inv_r, idx = run_dijkstra(grid, exit_cells, cell)

    if mode == "occupant":
        if not starts:
            raise ValueError("occupant 모드엔 starts 필요.")
        seeds = list(starts)
    else:
        seeds = _worst_seeds(dist, grid, bounds, cell, worst_n)

    paths = []
    skipped = 0
    # 버려진 출발점의 이유 — occupant 모드에서 사용자가 아무 데나 찍을 수 있어
    # "왜 경로가 안 나왔는지"를 돌려줘야 한다(조용히 사라지면 원인을 알 수 없다).
    dropped = []
    for (wx, wy) in seeds:
        oc, or_ = world_to_grid(wx, wy, minx, miny, cell)
        best = nearest_reachable(grid, dist, oc, or_, clearance, cell)
        if best is None:
            skipped += 1
            dropped.append({"start_m": (wx, wy), "reason": "unreachable"})
            continue
        bc, br = best
        d_mm = float(dist[bc, br])
        cells = trace_path(pred, inv_c, inv_r, grid, idx[bc, br])
        if len(cells) < 2:
            # Exit 셀 위(또는 바로 옆)라 역추적 경로가 1칸 이하 — 이미 출구다
            skipped += 1
            dropped.append({"start_m": (wx, wy), "reason": "at_exit"})
            continue
        path_m = [grid_to_world(c, r, minx, miny, cell) for c, r in cells]
        paths.append({"path_m": path_m, "dist_mm": d_mm,
                      "is_pass": d_mm <= threshold_mm,
                      "start_m": (wx, wy)})
    return Analysis(paths=paths, dropped=dropped, grid=grid, dist=dist, bounds=bounds,
                    cols=cols, rows=rows, cell=cell, clearance=clearance,
                    n_free=int((~grid).sum()), n_wall=int(grid.sum()),
                    skipped=skipped)


def _worst_seeds(dist, grid, bounds, cell, n):
    """도달가능 셀 중 거리 최대 지점을 최소이격으로 분산 추출(원본 WorstN 취지)."""
    minx, miny, maxx, maxy = bounds
    free_idx = np.argwhere(np.isfinite(dist) & (~grid))
    if free_idx.size == 0:
        return []
    order = np.argsort(-dist[free_idx[:, 0], free_idx[:, 1]])
    diag = math.hypot(maxx - minx, maxy - miny)
    sep = max(diag * 0.15, 5000.0)
    picked = []
    while len(picked) < n and sep > 1000:
        picked = []
        for k in order:
            c, r = int(free_idx[k, 0]), int(free_idx[k, 1])
            if all(math.hypot((c - pc) * cell, (r - pr) * cell) >= sep
                   for pc, pr in picked):
                picked.append((c, r))
                if len(picked) >= n:
                    break
        sep *= 0.7
    return [grid_to_world(c, r, minx, miny, cell) for c, r in picked]


def connectivity(obstacles, bounds, cell=CELL_SIZE, clearance=CLEARANCE,
                 openings=None, opening_width=900.0, shapes=None):
    """보행공간 연결성 진단(도면 품질 점검용). 반환 dict."""
    from scipy.ndimage import label
    minx, miny, maxx, maxy = bounds
    cols = int(math.ceil((maxx - minx) / cell)); rows = int(math.ceil((maxy - miny) / cell))
    grid = build_obstacle_grid(obstacles, minx, miny, cols, rows, cell, clearance, ())
    if openings:
        carve_free(grid, openings, minx, miny, cell, width=opening_width)
    if shapes:
        apply_shapes(grid, shapes, minx, miny, cell)
    free = ~grid
    lab, n = label(free)
    sizes = np.bincount(lab.ravel()); sizes[0] = 0
    main = int(sizes.argmax())
    return {"n_components": int(n), "largest_frac": float(sizes[main] / free.sum()),
            "labels": lab, "main": main, "grid": grid, "cols": cols, "rows": rows}
