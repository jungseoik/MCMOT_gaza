"""IDR 공간그래프 최단거리 (계약 v1.2 — 요구사항 FR-04·D-5).

SpatialGraph(수동 정의 노드·엣지) 위에서 경보위치→구역 최단거리를 구한다.
- 엣지 가중치 = 노드 간 유클리드 거리 (맵 px). m 환산은 호출자 소관
  (축척은 MapSpec.resolve_m_per_px() 단일 지점 — 계약 §1).
- 그래프가 비었거나 도달 불가면 None — 호출자(metrics 세션 층)가
  직선거리로 폴백한다.
"""
from __future__ import annotations

import heapq
import math

from system.config.schema import SpatialGraph

Point = tuple[float, float]


def nearest_node_id(graph: SpatialGraph, xy: Point) -> str | None:
    """점(맵 px)에서 유클리드 최근접 노드 id. 노드 없으면 None."""
    if not graph.nodes:
        return None
    return min(graph.nodes, key=lambda n: math.dist(n.xy, xy)).id


def shortest_dist_px(graph: SpatialGraph,
                     src_id: str | None, dst_id: str | None) -> float | None:
    """다익스트라 최단거리 (맵 px). 노드 미존재·도달 불가 → None."""
    pos = {n.id: n.xy for n in graph.nodes}
    if src_id not in pos or dst_id not in pos:
        return None
    adj: dict[str, list[tuple[str, float]]] = {nid: [] for nid in pos}
    for a, b in graph.edges:
        if a in pos and b in pos:
            w = math.dist(pos[a], pos[b])
            adj[a].append((b, w))
            adj[b].append((a, w))
    dist: dict[str, float] = {src_id: 0.0}
    pq: list[tuple[float, str]] = [(0.0, src_id)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == dst_id:
            return d
        if d > dist.get(u, math.inf):
            continue
        for v, w in adj[u]:
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return None
