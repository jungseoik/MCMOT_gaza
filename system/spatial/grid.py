"""IDR 격자 BFS 거리 모듈 (v1.6).

Zone polygon 내 격자 셀 centroid들의 BFS 최단거리(m) 평균으로
D(zone, origin)을 산출한다.

설계:
- 격자 셀 크기 = cell_size_m / m_per_px  (px 단위로 작업)
- BFS: 4방향 인접, 가중치 균일(cell_size_m) → BFS = 최단거리
- 벽 미지원 (현재 스펙 외): 격자 전 셀이 이동 가능 공간으로 취급
"""
from __future__ import annotations

import math
from collections import deque

Point = tuple[float, float]
_Cell = tuple[int, int]  # (row, col)


# ---------------------------------------------------------------- 격자 생성

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
    cells = []
    for r in range(n_rows):
        for c in range(n_cols):
            cx = (c + 0.5) * cell_px
            cy = (r + 0.5) * cell_px
            cells.append((r, c, cx, cy))
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
    """격자 셀 중 polygon centroid 포함 셀만 반환."""
    return [(r, c, cx, cy) for r, c, cx, cy in cells
            if _point_in_polygon(cx, cy, polygon)]


# ---------------------------------------------------------------- BFS 최단거리

def bfs_distances(n_rows: int, n_cols: int,
                  origin_rc: _Cell,
                  ) -> dict[_Cell, int]:
    """격자 (n_rows×n_cols)에서 origin_rc → 각 셀 BFS 홉 수.
    4방향 인접. 도달 불가 셀은 결과에 없음."""
    dist: dict[_Cell, int] = {origin_rc: 0}
    q: deque[_Cell] = deque([origin_rc])
    while q:
        r, c = q.popleft()
        d = dist[(r, c)]
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nb = (r + dr, c + dc)
            if 0 <= nb[0] < n_rows and 0 <= nb[1] < n_cols and nb not in dist:
                dist[nb] = d + 1
                q.append(nb)
    return dist


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
    3. BFS (홉 수) × cell_size_m → 미터 변환
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
    hop_dist = bfs_distances(n_rows, n_cols, src_rc)

    dists_m: list[float] = []
    for r, c, _, _ in zone_cells:
        hops = hop_dist.get((r, c))
        if hops is not None:
            dists_m.append(hops * cell_size_m)

    if not dists_m:
        # BFS 도달 불가 (이론상 발생 안 함 — 벽 없는 격자)
        cx = sum(p[0] for p in zone_polygon) / len(zone_polygon)
        cy = sum(p[1] for p in zone_polygon) / len(zone_polygon)
        return math.dist(origin_px, (cx, cy)) * m_per_px

    return sum(dists_m) / len(dists_m)
