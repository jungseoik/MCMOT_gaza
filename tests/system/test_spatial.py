"""M4 spatial 단위테스트 — 합성 좌표 기하 정답 검증 (GPU 불필요)."""
import math

import cv2
import numpy as np
import pytest

from system.config.schema import CameraConfig, CameraMapping
from system.spatial import (
    CameraProjector,
    DirectionalLine,
    nearest_on_polyline,
    point_in_polygon,
    polygon_area_m2,
    polygon_area_px2,
)

# ------------------------------------------------------------ 헬퍼


def make_cam(H, valid_roi=None, cctv_pts=None, map_pts=None):
    pts4 = [(0.0, 0.0), (1920.0, 0.0), (1920.0, 1080.0), (0.0, 1080.0)]
    return CameraConfig(
        cam_id="cam01", rtsp="rtsp://test",
        mapping=CameraMapping(
            cctv_pts=cctv_pts or pts4, map_pts=map_pts or pts4,
            H=[float(v) for v in np.asarray(H).reshape(-1)]),
        valid_roi=valid_roi)


def real_homography():
    """비자명 사영변환 — 카메라 사다리꼴 → 맵 사각형."""
    src = np.float32([[0, 0], [1920, 0], [1920, 1080], [0, 1080]])
    dst = np.float32([[100, 100], [700, 120], [650, 800], [120, 760]])
    return cv2.getPerspectiveTransform(src, dst)


# ------------------------------------------------------------ 투영


class TestCameraProjector:
    def test_roundtrip_error(self):
        """H 투영 → 역행렬 역투영 왕복 오차가 수치오차 수준."""
        H = real_homography()
        proj = CameraProjector(make_cam(H))
        Hinv = np.linalg.inv(H)
        for uv in [(960.0, 540.0), (100.0, 900.0), (1800.0, 200.0)]:
            p = proj.project(uv)
            assert p is not None
            back = cv2.perspectiveTransform(
                np.array([[[p.x, p.y]]], dtype=np.float64), Hinv)[0, 0]
            assert math.hypot(back[0] - uv[0], back[1] - uv[1]) < 1e-6

    def test_known_point(self):
        """대응점 자체는 정확히 매핑된다."""
        H = real_homography()
        p = CameraProjector(make_cam(H)).project((0.0, 0.0))
        assert abs(p.x - 100.0) < 1e-6 and abs(p.y - 100.0) < 1e-6

    def test_valid_roi_filter(self):
        """valid_roi(카메라 px) 밖 발끝점은 None으로 제외."""
        roi = [(0, 0), (960, 0), (960, 1080), (0, 1080)]   # 좌측 절반만 유효
        proj = CameraProjector(make_cam(np.eye(3), valid_roi=roi))
        assert proj.project((500.0, 500.0)) is not None
        assert proj.project((1500.0, 500.0)) is None

    def test_no_roi_means_all_valid(self):
        proj = CameraProjector(make_cam(np.eye(3)))
        assert proj.project((123.0, 456.0)) is not None

    def test_map_bounds_flag(self):
        """맵 경계 밖은 제외하지 않고 in_bounds=False 플래그."""
        proj = CameraProjector(make_cam(np.eye(3)), map_w=1000, map_h=1000)
        assert proj.project((500.0, 500.0)).in_bounds is True
        p = proj.project((1500.0, 500.0))
        assert p is not None and p.in_bounds is False

    def test_requires_mapping(self):
        cam = CameraConfig(cam_id="cam02", rtsp="rtsp://x")   # mapping 없음
        with pytest.raises(ValueError):
            CameraProjector(cam)


# ------------------------------------------------------------ polygon


class TestPolygon:
    SQUARE = [(100, 100), (300, 100), (300, 300), (100, 300)]

    def test_point_in_polygon(self):
        assert point_in_polygon((200, 200), self.SQUARE)
        assert point_in_polygon((100, 200), self.SQUARE)      # 경계 포함
        assert not point_in_polygon((99, 200), self.SQUARE)
        assert not point_in_polygon((500, 500), self.SQUARE)

    def test_area(self):
        """200px 정사각 × 축척 0.01 m/px → 4 m²."""
        assert polygon_area_px2(self.SQUARE) == pytest.approx(200.0 ** 2)
        assert polygon_area_m2(self.SQUARE, 0.01) == pytest.approx(4.0)


# ------------------------------------------------------------ polyline


class TestNearestOnPolyline:
    LSHAPE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]

    def test_first_segment(self):
        hit = nearest_on_polyline((5.0, 3.0), self.LSHAPE)
        assert hit.dist_px == pytest.approx(3.0)
        assert hit.tangent == pytest.approx((1.0, 0.0))
        assert hit.seg_idx == 0
        assert hit.point == pytest.approx((5.0, 0.0))

    def test_second_segment(self):
        hit = nearest_on_polyline((12.0, 5.0), self.LSHAPE)
        assert hit.dist_px == pytest.approx(2.0)
        assert hit.tangent == pytest.approx((0.0, 1.0))
        assert hit.seg_idx == 1
        assert hit.point == pytest.approx((10.0, 5.0))

    def test_endpoint_clamp(self):
        """polyline 밖(연장선)의 점은 끝점으로 클램프."""
        hit = nearest_on_polyline((-3.0, 4.0), self.LSHAPE)
        assert hit.dist_px == pytest.approx(5.0)
        assert hit.point == pytest.approx((0.0, 0.0))

    def test_on_line_zero_distance(self):
        hit = nearest_on_polyline((7.0, 0.0), self.LSHAPE)
        assert hit.dist_px == pytest.approx(0.0)


# ------------------------------------------------------------ 방향성 crossing


class TestDirectionalLine:
    def make(self, **kw):
        # 수직선 x=500 (y 400~600), 안쪽 = 왼쪽(x<500)
        return DirectionalLine(((500, 400), (500, 600)), (400, 500), **kw)

    def test_in_out_direction(self):
        line = self.make()
        assert line.observe("a", (560, 500)) is None     # 최초 관측
        assert line.observe("a", (440, 500)) == "in"     # 바깥→안
        assert line.observe("a", (560, 500)) == "out"    # 안→바깥

    def test_margin_deadband(self):
        line = self.make(margin_px=10.0)
        assert line.observe("a", (560, 500)) is None
        assert line.observe("a", (505, 500)) is None     # 데드밴드 — 보류
        assert line.observe("a", (440, 500)) == "in"

    def test_segment_only(self):
        """선분 범위 밖(y=900)에서 반평면만 넘으면 무시."""
        line = self.make()
        assert line.observe("a", (560, 900)) is None
        assert line.observe("a", (440, 900)) is None     # 선분 밖 crossing

    def test_multi_key_independent(self):
        line = self.make()
        line.observe("a", (560, 500))
        line.observe("b", (440, 500))
        assert line.observe("a", (440, 500)) == "in"
        assert line.observe("b", (560, 500)) == "out"

    def test_forget(self):
        line = self.make()
        line.observe("a", (560, 500))
        line.forget("a")
        assert line.observe("a", (440, 500)) is None     # 재관측 = 최초 취급
