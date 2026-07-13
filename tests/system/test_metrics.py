"""M5 MetricsEngine 단위테스트 — 합성 TrackedObject 궤적 기반 (GPU 불필요).

맵: 1000×1000 px, 축척 0.01 m/px (100 px = 1 m).
카메라: 항등 호모그래피 — foot_uv(카메라 px)가 그대로 맵 px가 된다.
"""
import pytest

from system.config.schema import (
    Bottleneck,
    CameraConfig,
    CameraMapping,
    ExitLine,
    MapSpec,
    Route,
    SiteConfig,
    Zone,
)
from system.contracts import MapState, TrackedObject
from system.metrics import MetricsEngine

M_PER_PX = 0.01     # 100 px = 1 m


def make_site(**kw) -> SiteConfig:
    return SiteConfig(
        site_id="test", version=1,
        map=MapSpec(image="map.png", w=1000, h=1000, m_per_px=M_PER_PX),
        **kw)


def make_cam(cam_id="cam01") -> CameraConfig:
    pts = [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]
    return CameraConfig(
        cam_id=cam_id, rtsp="rtsp://test",
        mapping=CameraMapping(cctv_pts=pts, map_pts=pts,
                              H=[1, 0, 0, 0, 1, 0, 0, 0, 1]))


def tr(cam_id, tid, x, y, ts) -> TrackedObject:
    return TrackedObject(cam_id=cam_id, local_track_id=tid, foot_uv=(x, y),
                         bbox_xyxy=(x - 10, y - 40, x + 10, y), conf=0.9, ts=ts)


ZONE = Zone(id="z1", polygon=[(100, 100), (300, 100), (300, 300), (100, 300)])
# 200px × 200px = 2m × 2m = 4 m²


# ------------------------------------------------------------ 구역·밀도


class TestZones:
    def test_count_and_density(self):
        """정사각 구역 안 2명 → count=2, density = 2/4m² = 0.5 명/m²."""
        eng = MetricsEngine(make_site(zones=[ZONE]), [make_cam()])
        eng.on_tracks("cam01", 100.0, [
            tr("cam01", 1, 150, 150, 100.0),
            tr("cam01", 2, 250, 250, 100.0),
            tr("cam01", 3, 500, 500, 100.0),   # 구역 밖
        ])
        snap = eng.snapshot()
        z = snap.zones[0]
        assert z.id == "z1"
        assert z.count == 2
        assert z.density == pytest.approx(2 / 4.0)

    def test_enter_leave(self):
        """구역 출입 — 나간 객체는 다음 스냅샷 count에서 제외."""
        eng = MetricsEngine(make_site(zones=[ZONE]), [make_cam()])
        eng.on_tracks("cam01", 10.0, [tr("cam01", 1, 200, 200, 10.0)])
        assert eng.snapshot().zones[0].count == 1
        eng.on_tracks("cam01", 10.5, [tr("cam01", 1, 600, 600, 10.5)])
        assert eng.snapshot().zones[0].count == 0


# ------------------------------------------------------------ 병목


class TestBottlenecks:
    BN = Bottleneck(id="b1", rho_crit=1.0,
                    polygon=[(100, 100), (300, 100), (300, 300), (100, 300)])

    def feed(self, n):
        eng = MetricsEngine(make_site(bottlenecks=[self.BN]), [make_cam()])
        tracks = [tr("cam01", i, 150 + i * 10, 150, 5.0) for i in range(n)]
        eng.on_tracks("cam01", 5.0, tracks)
        return eng.snapshot().bottlenecks[0]

    def test_under_threshold(self):
        """임계밀도(1명/m²) 미초과 — 3명/4m² = 0.75 → over=False."""
        b = self.feed(3)
        assert b.count == 3
        assert b.density == pytest.approx(0.75)
        assert b.over is False

    def test_over_threshold(self):
        """5명/4m² = 1.25 > rho_crit=1.0 → over=True."""
        b = self.feed(5)
        assert b.density == pytest.approx(1.25)
        assert b.over is True


# ------------------------------------------------------------ 통과선


EXIT = ExitLine(id="e1", line=((500, 400), (500, 600)), inside=(400, 500))


class TestExits:
    def walk(self, eng, xs, tid=1, y=500.0, t0=0.0):
        for i, x in enumerate(xs):
            ts = t0 + i * 0.2
            eng.on_tracks("cam01", ts, [tr("cam01", tid, x, y, ts)])

    def test_directional_in_out(self):
        eng = MetricsEngine(make_site(exits=[EXIT]), [make_cam()])
        self.walk(eng, [560, 520, 480, 440])       # 바깥→안
        e = eng.snapshot().exits[0]
        assert (e.in_count, e.out_count) == (1, 0)

    def test_roundtrip_debounce(self):
        """1인 왕복 2회 → in 1 유지 (방향별 최초 통과만 유효)."""
        eng = MetricsEngine(make_site(exits=[EXIT]), [make_cam()])
        path = [560, 440, 560, 440, 560]           # in-out-in-out 왕복 2회
        self.walk(eng, path)
        e = eng.snapshot().exits[0]
        assert e.in_count == 1
        assert e.out_count == 1

    def test_two_people(self):
        eng = MetricsEngine(make_site(exits=[EXIT]), [make_cam()])
        self.walk(eng, [560, 440], tid=1)
        self.walk(eng, [560, 440], tid=2, t0=1.0)
        e = eng.snapshot().exits[0]
        assert e.in_count == 2


# ------------------------------------------------------------ 속도·정렬도


ROUTE = Route(id="r1", points=[(100, 500), (900, 500)])   # +x 방향 경로


class TestKinematics:
    def feed_line(self, eng, x0, dx_px, n=11, dt=0.2, y=500.0, cam="cam01"):
        """등속 직선 궤적: 스텝당 dx_px, dt초."""
        for i in range(n):
            ts = 100.0 + i * dt
            eng.on_tracks(cam, ts, [tr(cam, 1, x0 + i * dx_px, y, ts)])

    def test_speed_1mps(self):
        """1 m/s(=100 px/s) 궤적 → speed_mps ≈ 1.0 (축척 적용)."""
        eng = MetricsEngine(make_site(), [make_cam()])
        self.feed_line(eng, 200, dx_px=20, dt=0.2)   # 100 px/s
        o = eng.snapshot().objects[0]
        assert o.speed_mps == pytest.approx(1.0, rel=1e-6)

    def test_stationary_zero_speed(self):
        eng = MetricsEngine(make_site(), [make_cam()])
        self.feed_line(eng, 200, dx_px=0)
        o = eng.snapshot().objects[0]
        assert o.speed_mps == 0.0
        assert (o.vx, o.vy) == (0.0, 0.0)

    def test_align_forward(self):
        """경로 순방향(+x) 이동 → align ≈ 1.0, 방향벡터 (1,0)."""
        eng = MetricsEngine(make_site(routes=[ROUTE]), [make_cam()])
        self.feed_line(eng, 200, dx_px=20)
        o = eng.snapshot().objects[0]
        assert o.align == pytest.approx(1.0)
        assert (o.vx, o.vy) == pytest.approx((1.0, 0.0))

    def test_align_backward(self):
        """경로 역방향(−x) 이동 → align ≈ −1.0."""
        eng = MetricsEngine(make_site(routes=[ROUTE]), [make_cam()])
        self.feed_line(eng, 800, dx_px=-20)
        o = eng.snapshot().objects[0]
        assert o.align == pytest.approx(-1.0)

    def test_align_none_without_routes(self):
        """경로 미등록 → align=None (계약: routes 없으면 None)."""
        eng = MetricsEngine(make_site(), [make_cam()])
        self.feed_line(eng, 200, dx_px=20)
        assert eng.snapshot().objects[0].align is None


# ------------------------------------------------------------ 소실 timeout


class TestTimeout:
    def test_lost_object_removed(self):
        eng = MetricsEngine(make_site(), [make_cam()],
                            lost_timeout_sec=3.0)
        eng.on_tracks("cam01", 10.0, [tr("cam01", 1, 200, 200, 10.0)])
        eng.on_tracks("cam01", 11.0, [tr("cam01", 2, 400, 400, 11.0)])
        assert len(eng.snapshot().objects) == 2
        # id=2만 계속 관측 — 4초 뒤 id=1은 timeout 제거
        eng.on_tracks("cam01", 15.0, [tr("cam01", 2, 400, 400, 15.0)])
        objs = eng.snapshot().objects
        assert [o.gid for o in objs] == ["cam01:2"]


# ------------------------------------------------------------ reload


class TestReload:
    def test_reload_applies_new_zones_and_keeps_exit_counts(self):
        site = make_site(exits=[EXIT])
        eng = MetricsEngine(site, [make_cam()])
        for i, x in enumerate([560, 440]):
            ts = i * 0.2
            eng.on_tracks("cam01", ts, [tr("cam01", 1, x, 500, ts)])
        assert eng.snapshot().exits[0].in_count == 1

        site2 = make_site(exits=[EXIT], zones=[ZONE])
        site2.version = 2
        eng.reload(site2, [make_cam()])
        snap = eng.snapshot()
        assert snap.site_version == 2
        assert [z.id for z in snap.zones] == ["z1"]
        assert snap.exits[0].in_count == 1        # 카운트 보존


# ------------------------------------------------------------ e2e


class TestEndToEnd:
    def test_synthetic_sequence_yields_valid_mapstate(self):
        """멀티카메라 합성 시퀀스 → 스키마에 맞는 MapState."""
        site = make_site(zones=[ZONE], exits=[EXIT], routes=[ROUTE],
                         bottlenecks=[Bottleneck(
                             id="b1", rho_crit=2.0,
                             polygon=[(400, 400), (600, 400),
                                      (600, 600), (400, 600)])])
        cams = [make_cam("cam01"), make_cam("cam02")]
        eng = MetricsEngine(site, cams)
        for i in range(10):
            ts = 1000.0 + i * 0.2
            eng.on_tracks("cam01", ts, [
                tr("cam01", 1, 150 + i * 20, 150, ts),   # 구역 안 이동
                tr("cam01", 2, 560 - i * 20, 500, ts),   # 통과선 in
            ])
            eng.on_tracks("cam02", ts, [
                tr("cam02", 1, 500, 500, ts),            # 병목 안 정지
            ])
        snap = eng.snapshot()
        assert isinstance(snap, MapState)
        MapState.model_validate(snap.model_dump())        # 계약 스키마 왕복
        assert snap.ts == pytest.approx(1000.0 + 9 * 0.2)
        gids = {o.gid for o in snap.objects}
        assert gids == {"cam01:1", "cam01:2", "cam02:1"}  # gid = cam:id
        assert snap.exits[0].in_count == 1
        assert snap.bottlenecks[0].count == 1
        assert snap.bottlenecks[0].over is False          # 임계 미초과 성질
        for o in snap.objects:
            if o.gid == "cam02:1":
                assert o.speed_mps == 0.0
            else:
                assert o.speed_mps > 0.5

    def test_unmapped_camera_ignored(self):
        """mapping 없는 카메라 트랙은 조용히 제외 (계약: 처리 제외)."""
        eng = MetricsEngine(make_site(), [make_cam("cam01")])
        eng.on_tracks("cam99", 1.0, [tr("cam99", 1, 100, 100, 1.0)])
        assert eng.snapshot().objects == []
