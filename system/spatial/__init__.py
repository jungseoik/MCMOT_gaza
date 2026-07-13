"""system.spatial — 카메라→맵 투영 + 맵 좌표계 순수 기하 (M4, GPU 불필요).

축척(px↔m)은 `MapSpec.resolve_m_per_px()` 한 곳에서만 얻는다 (계약 §1).
"""
from system.spatial.geometry import (
    DirectionalLine,
    PolylineHit,
    nearest_on_polyline,
    point_in_polygon,
    polygon_area_m2,
    polygon_area_px2,
)
from system.spatial.graph import nearest_node_id, shortest_dist_px
from system.spatial.projector import CameraProjector, ProjectedPoint

__all__ = [
    "CameraProjector",
    "ProjectedPoint",
    "DirectionalLine",
    "PolylineHit",
    "nearest_on_polyline",
    "nearest_node_id",
    "point_in_polygon",
    "polygon_area_m2",
    "polygon_area_px2",
    "shortest_dist_px",
]
