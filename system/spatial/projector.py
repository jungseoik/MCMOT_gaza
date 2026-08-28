"""카메라 발끝점 → 공통 2D 맵 투영 (M4).

CameraConfig.mapping.H (row-major 9원소, 카메라 px → 맵 px)로
발끝점 (u,v)를 맵 (x,y)로 투영한다 (cv2.perspectiveTransform).

- cctv_pts 컨벡스 헐 밖 검출은 자동 제외(외삽 방지) — valid_roi 미지정 시 적용
- valid_roi(카메라 px polygon) 지정 시 해당 ROI로 대체
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


def _convex_hull(pts: list) -> list[tuple[float, float]]:
    """cctv_pts → 컨벡스 헐 (cv2 사용)."""
    arr = np.array(pts, dtype=np.float32).reshape(-1, 1, 2)
    hull = cv2.convexHull(arr)
    return [tuple(p[0]) for p in hull]


class CameraProjector:
    """카메라 1대의 호모그래피 투영기.

    map_w/map_h는 site.map(MapSpec)의 w/h — 없으면 경계 플래그는 항상 True.

    ROI 우선순위:
      1. cam.valid_roi 지정 시 → 해당 polygon 사용
      2. 미지정 시 → cctv_pts 컨벡스 헐 자동 적용 (외삽 방지)
    """

    def __init__(self, cam: CameraConfig,
                 map_w: int | None = None, map_h: int | None = None):
        if cam.mapping is None:
            raise ValueError(f"{cam.cam_id}: mapping(H) 없음 — 맵 투영 불가")
        self.cam_id = cam.cam_id
        self.H = np.asarray(cam.mapping.H, dtype=np.float64).reshape(3, 3)
        self.map_w = map_w
        self.map_h = map_h

        if cam.valid_roi:
            # 사용자가 명시적으로 지정한 ROI
            self._roi = [tuple(p) for p in cam.valid_roi]
        else:
            # cctv_pts 컨벡스 헐 — 보간 범위만 투영 (외삽 자동 차단)
            self._roi = _convex_hull(cam.mapping.cctv_pts)

    def in_valid_roi(self, u: float, v: float) -> bool:
        """카메라 px 유효영역 포함 여부."""
        return point_in_polygon((u, v), self._roi)

    def roi_map_polygon(self) -> list[tuple[float, float]]:
        """유효영역(헐/ROI)을 맵 px 로 투영한 다각형 — 외삽 허용 거리 판정용."""
        arr = np.array(self._roi, dtype=np.float64).reshape(-1, 1, 2)
        out = cv2.perspectiveTransform(arr, self.H).reshape(-1, 2)
        return [(float(x), float(y)) for x, y in out]

    def project_raw(self, foot_uv: tuple[float, float]) -> ProjectedPoint:
        """ROI 검사 없이 투영(외삽 허용). 출입구 통과 판정 전용 — 문은 대개 헐 경계 밖이라
        일반 규칙(헐 밖 폐기)대로면 영영 안 세진다. 외삽 오차가 커지므로 호출부가
        '출입구 선 근처(exit_extrap_m)' 로 범위를 제한한다."""
        u, v = float(foot_uv[0]), float(foot_uv[1])
        w = cv2.perspectiveTransform(np.array([[[u, v]]], dtype=np.float64), self.H)[0, 0]
        x, y = float(w[0]), float(w[1])
        in_bounds = True
        if self.map_w is not None and self.map_h is not None:
            in_bounds = 0.0 <= x <= float(self.map_w) and 0.0 <= y <= float(self.map_h)
        return ProjectedPoint(x=x, y=y, in_bounds=in_bounds)

    def project(self, foot_uv: tuple[float, float]) -> ProjectedPoint | None:
        """발끝점 (u,v) → 맵 (x,y). ROI 밖이면 None."""
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
