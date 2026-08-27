"""IDR 거리 D 는 '사람이 있는 곳' 기준이다 (요구사항 §4.1, 2026-08-27 개정).

구역 polygon 안 모든 셀을 평균하면 사람이 어디 있든 같은 값이 나온다 —
도면 전체를 한 구역으로 잡으면 전원이 경보 바로 옆에 있어도 79.1 m 였다.
"""
import math

import pytest

from system.config.schema import (GridConfig, MapSpec, Route, SiteConfig, Zone)
from system.metrics.engine import MetricsEngine
from system.config.schema import CameraConfig, CameraMapping
from tests.system.test_session import tr

M_PER_PX = 0.05
PX = 1.0 / M_PER_PX
SIDE_M = 100.0
ORIGIN = (0.0, 0.0)


def make_cam():
    """맵 전체를 덮는 카메라 — 기본 픽스처는 1000px 까지만 덮어 맵 밖 사람이
    투영 단계에서 버려진다(valid_roi = 대응점 convex hull)."""
    w = SIDE_M * PX
    pts = [(0.0, 0.0), (w, 0.0), (w, w), (0.0, w)]
    return CameraConfig(cam_id="cam01", rtsp="rtsp://t",
                        mapping=CameraMapping(cctv_pts=pts, map_pts=pts,
                                              H=[1, 0, 0, 0, 1, 0, 0, 0, 1]))


def _site(zones):
    return SiteConfig(
        site_id="t", version=1,
        map=MapSpec(image="m.png", w=SIDE_M * PX, h=SIDE_M * PX, m_per_px=M_PER_PX),
        grid=GridConfig(cell_size_m=2.0), zones=zones,
        routes=[Route(id="r", points=[(0, 0), (SIDE_M * PX, SIDE_M * PX)])])


WHOLE = Zone(id="all", polygon=[(0, 0), (SIDE_M * PX, 0),
                               (SIDE_M * PX, SIDE_M * PX), (0, SIDE_M * PX)])
AREA_AVG_M = 79.06          # 이 구역·격자의 면적 평균 (폴백 값)


def _d_for(people_m):
    """경보 시점에 people_m 에 사람이 있을 때의 D."""
    eng = MetricsEngine(_site([WHOLE]), [make_cam()])
    for t in (-0.4, -0.2):
        eng.on_tracks("cam01", t, [tr("cam01", n + 1, x * PX, y * PX, t)
                                   for n, (x, y) in enumerate(people_m)])
    eng.start_session(ORIGIN, t_alarm=0.0)
    return eng._session.zones[0].graph_distances_m[0]


class TestDistanceFollowsPeople:
    def test_near_alarm_gives_short_distance(self):
        d = _d_for([(5, 5), (7, 7), (9, 9)])
        assert d < 20, f"경보 옆에 몰려 있는데 {d:.1f} m"

    def test_far_corner_gives_long_distance(self):
        d = _d_for([(80, 80), (85, 85), (90, 90)])
        assert d > 100, f"반대편 구석인데 {d:.1f} m"

    def test_distance_actually_moves_with_people(self):
        """면적 평균이면 이 둘이 같은 값이었다 — 그게 원래 문제였다."""
        near = _d_for([(5, 5), (7, 7), (9, 9)])
        far = _d_for([(80, 80), (85, 85), (90, 90)])
        assert far > near * 4, f"near={near:.1f} far={far:.1f}"

    def test_matches_straight_line_within_grid_resolution(self):
        people = [(20, 20), (30, 30), (40, 40)]
        d = _d_for(people)
        eu = sum(math.hypot(x, y) for x, y in people) / len(people)
        assert abs(d - eu) / eu < 0.10, f"격자 {d:.1f} vs 직선 {eu:.1f}"


class TestFallback:
    def test_no_people_falls_back_to_area_average(self):
        """경보 시점에 그 구역에 아무도 없으면 기존 면적 평균을 쓴다."""
        eng = MetricsEngine(_site([WHOLE]), [make_cam()])
        eng.start_session(ORIGIN, t_alarm=0.0)      # 아무도 안 넣고 시작
        d = eng._session.zones[0].graph_distances_m[0]
        assert d == pytest.approx(AREA_AVG_M, abs=0.5)

    def test_people_outside_the_zone_do_not_count(self):
        """다른 구역 사람은 이 구역 D 에 안 섞인다."""
        left = Zone(id="L", polygon=[(0, 0), (50 * PX, 0),
                                     (50 * PX, SIDE_M * PX), (0, SIDE_M * PX)])
        right = Zone(id="R", polygon=[(50 * PX, 0), (SIDE_M * PX, 0),
                                      (SIDE_M * PX, SIDE_M * PX), (50 * PX, SIDE_M * PX)])
        eng = MetricsEngine(_site([left, right]), [make_cam()])
        for t in (-0.4, -0.2):
            eng.on_tracks("cam01", t, [
                tr("cam01", 1, 10 * PX, 10 * PX, t),    # 왼쪽
                tr("cam01", 2, 90 * PX, 90 * PX, t)])   # 오른쪽
        eng.start_session(ORIGIN, t_alarm=0.0)
        dl, dr = (z.graph_distances_m[0] for z in eng._session.zones)
        assert dl < 20 and dr > 100, f"L={dl:.1f} R={dr:.1f}"
