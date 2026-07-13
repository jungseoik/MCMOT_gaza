"""카메라 발끝점 → 공통 2D 맵 투영 (M4).

CameraConfig.mapping.H (row-major 9원소, 카메라 px → 맵 px)로
발끝점 (u,v)를 맵 (x,y)로 투영한다 (cv2.perspectiveTransform).

- valid_roi(카메라 px polygon) 밖 검출은 **제외**(None 반환)
- 맵 경계(w×h) 밖 투영은 제외하지 않고 in_bounds=False 플래그만
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from system.config.schema import CameraConfig
from system.spatial.geometry import point_in_polygon


@dataclass(frozen=True)
class ProjectedPoint:
    """맵 투영 결과 (맵 원본 px)."""
    x: float
    y: float
    in_bounds: bool     # 맵 이미지 경계(0..w, 0..h) 안 여부


class CameraProjector:
    """카메라 1대의 호모그래피 투영기.

    map_w/map_h는 site.map(MapSpec)의 w/h — 없으면 경계 플래그는 항상 True.
    """

    def __init__(self, cam: CameraConfig,
                 map_w: int | None = None, map_h: int | None = None):
        if cam.mapping is None:
            raise ValueError(f"{cam.cam_id}: mapping(H) 없음 — 맵 투영 불가")
        self.cam_id = cam.cam_id
        self.H = np.asarray(cam.mapping.H, dtype=np.float64).reshape(3, 3)
        self.valid_roi = ([tuple(p) for p in cam.valid_roi]
                          if cam.valid_roi else None)   # 카메라 px polygon
        self.map_w = map_w
        self.map_h = map_h

    def in_valid_roi(self, u: float, v: float) -> bool:
        """카메라 px 유효영역 포함 여부 (미지정 시 전체 유효)."""
        if self.valid_roi is None:
            return True
        return point_in_polygon((u, v), self.valid_roi)

    def project(self, foot_uv: tuple[float, float]) -> ProjectedPoint | None:
        """발끝점 (u,v) → 맵 (x,y). valid_roi 밖이면 None."""
        u, v = float(foot_uv[0]), float(foot_uv[1])
        if not self.in_valid_roi(u, v):
            return None
        p = np.array([[[u, v]]], dtype=np.float64)
        w = cv2.perspectiveTransform(p, self.H)[0, 0]
        x, y = float(w[0]), float(w[1])
        in_bounds = True
        if self.map_w is not None and self.map_h is not None:
            in_bounds = (0.0 <= x <= float(self.map_w)
                         and 0.0 <= y <= float(self.map_h))
        return ProjectedPoint(x=x, y=y, in_bounds=in_bounds)
