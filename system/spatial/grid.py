"""IDR 격자 BFS 거리 모듈 (v1.6).

Zone polygon 내 격자 셀 centroid들의 BFS 최단거리(m) 평균으로
D(zone, origin)을 산출한다.

설계:
- 격자 셀 크기 = cell_size_m / m_per_px  (px 단위로 작업)
- BFS: 4방향 인접, 가중치 균일(cell_size_m) → BFS = 최단거리
- 벽 미지원 (현재 스펙 외): 격자 전 셀이 이동 가능 공간으로 취급
"""
from __future__ import annotations

import heapq
import math
from collections import OrderedDict, deque

Point = tuple[float, float]
_Cell = tuple[int, int]  # (row, col)


# ---------------------------------------------------------------- 격자 생성

_CELLS_CACHE: "OrderedDict[tuple, list[tuple[int, int, float, float]]]" = OrderedDict()
_CELLS_CACHE_MAX = 4


def make_grid_cells(map_w: float, map_h: float,
                    m_per_px: float, cell_size_m: float,
                    ) -> list[tuple[int, int, float, float]]:
    """맵 전체를 정사각형 격자로 분할, 셀 목록을 반환.

    Returns:
        list of (row, col, cx_px, cy_px)
    """
    cell_px = cell_size_m / m_per_px
    n_rows = max(1, math.ceil(map_h / cell_px))
    n_cols = max(1, math.ceil(map_w / cell_px))
    key = (n_rows, n_cols, cell_px)
    hit = _CELLS_CACHE.get(key)
    if hit is not None:                 # 격자 모양이 같으면 그대로 쓴다 —
        _CELLS_CACHE.move_to_end(key)   # 구역마다 4만 개를 다시 만들 이유가 없다
        return hit
    cells = []
    for r in range(n_rows):
        for c in range(n_cols):
            cx = (c + 0.5) * cell_px
            cy = (r + 0.5) * cell_px
            cells.append((r, c, cx, cy))
    _CELLS_CACHE[key] = cells
    if len(_CELLS_CACHE) > _CELLS_CACHE_MAX:
        _CELLS_CACHE.popitem(last=False)
    return cells


# ---------------------------------------------------------------- 폴리곤 내 셀

def _point_in_polygon(px: float, py: float, polygon: list[Point]) -> bool:
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def cells_in_polygon(cells: list[tuple[int, int, float, float]],
                     polygon: list[Point],
                     ) -> list[tuple[int, int, float, float]]:
    """격자 셀 중 polygon centroid 포함 셀만 반환.

    바깥 사각(bbox)으로 먼저 걸러낸다 — 구역을 도면 전체에 타일처럼 깔면
    셀 전부를 구역 수만큼 점-다각형 판정하게 되는데, 대부분은 애초에 구역
    근처도 아니다(실측 0.5m 셀·25구역에서 이 필터가 대부분의 비용을 없앤다).
    """
    if not polygon:
        return []
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    return [(r, c, cx, cy) for r, c, cx, cy in cells
            if x0 <= cx <= x1 and y0 <= cy <= y1
            and _point_in_polygon(cx, cy, polygon)]


# ---------------------------------------------------------------- BFS 최단거리

# 8방향 이웃과 이동 비용 — 대각은 √2 셀.
# 4방향만 쓰면 홉 수가 곧 **맨해튼 거리**가 되어, 대각 방향 구역의 거리가 최대
# √2(+41%) 부풀었다(실측: 45° 방향 42.4m → 59.0m). IDR = D/지연 이라 그대로
# IDR 이 41% 부풀고, 더 나쁜 건 **도면을 45° 돌리면 값이 변한다**는 점이다.
_NEIGHBORS: tuple[tuple[int, int, float], ...] = (
    (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
    (-1, -1, _SQRT2 := math.sqrt(2.0)), (-1, 1, _SQRT2),
    (1, -1, _SQRT2), (1, 1, _SQRT2),
)


def bfs_distances(n_rows: int, n_cols: int,
                  origin_rc: _Cell,
                  ) -> dict[_Cell, float]:
    """격자에서 origin_rc → 각 셀 최단거리 (**셀 단위**, 대각 √2).

    8방향 다익스트라. 반환값은 홉 수가 아니라 셀 단위 거리라 실수다 —
    호출부에서 `× cell_size_m` 하면 미터가 된다.

    장애물이 없는 지금은 결과가 octile 거리(유클리드 대비 최대 +7.6%)이고,
    나중에 통행 불가 셀을 넣으면 그대로 우회 경로 거리가 된다.
    """
    dist: dict[_Cell, float] = {origin_rc: 0.0}
    pq: list[tuple[float, _Cell]] = [(0.0, origin_rc)]
    while pq:
        d, rc = heapq.heappop(pq)
        if d > dist.get(rc, math.inf):
            continue                      # 오래된 항목
        r, c = rc
        for dr, dc, w in _NEIGHBORS:
            nb = (r + dr, c + dc)
            if not (0 <= nb[0] < n_rows and 0 <= nb[1] < n_cols):
                continue
            nd = d + w
            if nd < dist.get(nb, math.inf) - 1e-12:
                dist[nb] = nd
                heapq.heappush(pq, (nd, nb))
    return dist


# 같은 원점의 거리장(distance field)은 어느 구역을 재든 동일하다.
# 구역마다 다시 돌리면 구역 수만큼 낭비다 — 구역을 도면 전체에 타일처럼 깔면
# 25개 × 3원점 = 75회가 된다(실측 0.5m 셀에서 1.6s → 4.8s).
# 격자 모양과 원점이 같으면 재사용한다. 세션 시작 때만 쓰이므로 작게 유지.
_FIELD_CACHE: "OrderedDict[tuple, dict[_Cell, float]]" = OrderedDict()
_FIELD_CACHE_MAX = 32


def _distance_field(n_rows: int, n_cols: int, origin_rc: _Cell) -> dict[_Cell, float]:
    key = (n_rows, n_cols, origin_rc)
    hit = _FIELD_CACHE.get(key)
    if hit is not None:
        _FIELD_CACHE.move_to_end(key)
        return hit
    field = bfs_distances(n_rows, n_cols, origin_rc)
    _FIELD_CACHE[key] = field
    if len(_FIELD_CACHE) > _FIELD_CACHE_MAX:
        _FIELD_CACHE.popitem(last=False)
    return field


def clear_distance_cache() -> None:
    """거리장·셀목록 캐시 비우기 — 테스트·격자 설정 변경 시."""
    _FIELD_CACHE.clear()
    _CELLS_CACHE.clear()


def _origin_cell(origin_px: Point, cell_px: float,
                 n_rows: int, n_cols: int) -> _Cell:
    """경보 발생원 (맵 px) → 격자 (row, col) (범위 클램프)."""
    r = int(origin_px[1] / cell_px)
    c = int(origin_px[0] / cell_px)
    return (max(0, min(n_rows - 1, r)), max(0, min(n_cols - 1, c)))


# ---------------------------------------------------------------- 공개 API

def zone_grid_distance_m(zone_polygon: list[Point],
                         origin_px: Point,
                         map_w: float, map_h: float,
                         m_per_px: float,
                         cell_size_m: float,
                         ) -> float | None:
    """경보 발생원 → Zone polygon BFS 평균거리 (m).

    1. 격자 셀 생성
    2. Zone 내 포함 셀 필터
    3. 8방향 다익스트라 (셀 단위 거리, 대각 √2) × cell_size_m → 미터
    4. Zone 내 셀 평균

    Zone에 셀이 없거나 축척이 없으면 None 반환.
    """
    if m_per_px <= 0 or cell_size_m <= 0:
        return None
    cell_px = cell_size_m / m_per_px
    n_rows = max(1, math.ceil(map_h / cell_px))
    n_cols = max(1, math.ceil(map_w / cell_px))

    cells = make_grid_cells(map_w, map_h, m_per_px, cell_size_m)
    zone_cells = cells_in_polygon(cells, zone_polygon)
    if not zone_cells:
        # Zone이 셀보다 작으면 centroid로 폴백 (직선 유클리드)
        cx = sum(p[0] for p in zone_polygon) / len(zone_polygon)
        cy = sum(p[1] for p in zone_polygon) / len(zone_polygon)
        return math.dist(origin_px, (cx, cy)) * m_per_px

    src_rc = _origin_cell(origin_px, cell_px, n_rows, n_cols)
    cell_dist = _distance_field(n_rows, n_cols, src_rc)

    dists_m: list[float] = []
    for r, c, _, _ in zone_cells:
        d_cells = cell_dist.get((r, c))
        if d_cells is not None:
            dists_m.append(d_cells * cell_size_m)

    if not dists_m:
        # BFS 도달 불가 (이론상 발생 안 함 — 벽 없는 격자)
        cx = sum(p[0] for p in zone_polygon) / len(zone_polygon)
        cy = sum(p[1] for p in zone_polygon) / len(zone_polygon)
        return math.dist(origin_px, (cx, cy)) * m_per_px

    return sum(dists_m) / len(dists_m)
