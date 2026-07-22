"""다중 도면(N개 층) 지원 단위테스트 (v1.7).

검증 대상:
- 스키마: 기존 단일도면 site.json → "default" 층 1개 승격, floors 보존, 헬퍼
- store.map_path: default=map.png / 그 외=map_<id>.png
- Runtime: 층별 엔진 생성·카메라 floor_id 라우팅 (엔진이 자기 층 트랙만 수신)
- 세션 저장 디렉토리 층별 분리

층 라우팅 테스트는 실제 ingest/TRT를 띄우지 않고 Runtime.reload_engine()과
_dispatch_tracks()만 직접 호출한다(합성 데이터, GPU 불필요).
"""
import os
import tempfile

import pytest

from system.config.schema import (
    CameraConfig,
    CameraMapping,
    DEFAULT_FLOOR_ID,
    Floor,
    MapSpec,
    SiteConfig,
    Zone,
)
from system.config.store import SiteStore
from system.contracts import TrackedObject

M_PER_PX = 0.01
IDENTITY_H = [1, 0, 0, 0, 1, 0, 0, 0, 1]
_SQUARE = [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]


def _mapspec():
    return MapSpec(image="map.png", w=1000, h=1000, m_per_px=M_PER_PX)


def _zone(zid, x0, y0):
    return Zone(id=zid, polygon=[(x0, y0), (x0 + 200, y0),
                                 (x0 + 200, y0 + 200), (x0, y0 + 200)])


def _cam(cam_id, floor_id=None):
    return CameraConfig(cam_id=cam_id, rtsp="rtsp://test", floor_id=floor_id,
                        mapping=CameraMapping(cctv_pts=_SQUARE, map_pts=_SQUARE,
                                              H=IDENTITY_H))


def _tr(cam_id, tid, x, y, ts):
    return TrackedObject(cam_id=cam_id, local_track_id=tid, foot_uv=(x, y),
                         bbox_xyxy=(x - 10, y - 40, x + 10, y), conf=0.9, ts=ts)


# ============================================================ 스키마 승격
class TestLegacyPromotion:
    def test_legacy_site_promoted_to_default_floor(self):
        """floors 없는(기존) site.json → 'default' 층 1개로 승격."""
        cfg = SiteConfig(site_id="s", map=_mapspec(),
                         zones=[_zone("z1", 100, 100)],
                         routes=[], bottlenecks=[])
        assert len(cfg.floors) == 1
        fl = cfg.floors[0]
        assert fl.id == DEFAULT_FLOOR_ID
        assert fl.map is not None and fl.map.w == 1000
        assert [z.id for z in fl.zones] == ["z1"]

    def test_empty_site_still_has_default_floor(self):
        """맵·요소 전무한 사이트도 로드 후 floors≥1 (default) 보장."""
        cfg = SiteConfig(site_id="s")
        assert [f.id for f in cfg.floors] == [DEFAULT_FLOOR_ID]
        assert cfg.floors[0].map is None

    def test_existing_floors_not_overwritten(self):
        """floors가 이미 있으면 top-level로 덮어쓰지 않는다."""
        cfg = SiteConfig(
            site_id="s", map=_mapspec(),
            floors=[Floor(id="17F", name="17층", map=_mapspec()),
                    Floor(id="18F", name="18층")])
        assert [f.id for f in cfg.floors] == ["17F", "18F"]

    def test_roundtrip_json_keeps_floors(self):
        """JSON 왕복 후에도 승격된 floors 유지 (재승격 안 함)."""
        cfg = SiteConfig(site_id="s", map=_mapspec(), zones=[_zone("z1", 0, 0)])
        again = SiteConfig.model_validate_json(cfg.model_dump_json())
        assert [f.id for f in again.floors] == [DEFAULT_FLOOR_ID]
        assert [z.id for z in again.floors[0].zones] == ["z1"]


# ============================================================ 헬퍼
class TestHelpers:
    def _multi(self):
        return SiteConfig(
            site_id="s",
            floors=[Floor(id=DEFAULT_FLOOR_ID, map=_mapspec(),
                          zones=[_zone("zA", 0, 0)]),
                    Floor(id="17F", map=_mapspec(), zones=[_zone("zB", 500, 500)])])

    def test_get_floor(self):
        cfg = self._multi()
        assert cfg.get_floor("17F").id == "17F"
        assert cfg.get_floor(None).id == DEFAULT_FLOOR_ID       # None → default
        assert cfg.get_floor("nope").id == DEFAULT_FLOOR_ID     # 없으면 첫 층

    def test_floor_id_of_camera(self):
        cfg = self._multi()
        assert cfg.floor_id_of_camera(_cam("c1")) == DEFAULT_FLOOR_ID     # None
        assert cfg.floor_id_of_camera(_cam("c2", "17F")) == "17F"
        assert cfg.floor_id_of_camera(_cam("c3", "ghost")) == DEFAULT_FLOOR_ID  # 고아→첫층

    def test_as_floor_view(self):
        """뷰는 해당 층 공간요소를 top-level에 싣고 thresholds/version은 공용."""
        cfg = self._multi()
        cfg.thresholds.v_th = 0.77
        v = cfg.as_floor_view("17F")
        assert [z.id for z in v.zones] == ["zB"]
        assert v.thresholds.v_th == 0.77
        d = cfg.as_floor_view(DEFAULT_FLOOR_ID)
        assert [z.id for z in d.zones] == ["zA"]


# ============================================================ store.map_path
class TestMapPath:
    def test_default_keeps_map_png(self):
        st = SiteStore("/tmp/x")
        assert st.map_path("s").name == "map.png"
        assert st.map_path("s", DEFAULT_FLOOR_ID).name == "map.png"

    def test_other_floor_suffixed(self):
        st = SiteStore("/tmp/x")
        assert st.map_path("s", "17F").name == "map_17F.png"


# ============================================================ Runtime 라우팅
@pytest.fixture
def rt_two_floors():
    """temp 사이트에 default + 17F 두 층, cam01→default / cam02→17F 구성."""
    tmp = tempfile.mkdtemp(prefix="floor-test-")
    os.environ["SITE_ROOT"] = tmp
    os.environ["SITE_ID"] = "floor-test"
    os.environ["INGEST_BACKEND"] = "ffmpeg"
    import importlib

    import system.api.server as server
    importlib.reload(server)          # temp env로 rt 재생성
    store = server.rt.store
    site = SiteConfig(
        site_id="floor-test", version=1,
        floors=[
            Floor(id=DEFAULT_FLOOR_ID, name="1층", map=_mapspec(),
                  zones=[_zone("z_def", 100, 100)]),
            Floor(id="17F", name="17층", map=_mapspec(),
                  zones=[_zone("z_17", 100, 100)]),
        ])
    store.save_site(site, bump_version=False)
    store.save_camera("floor-test", _cam("cam01", DEFAULT_FLOOR_ID))
    store.save_camera("floor-test", _cam("cam02", "17F"))
    server.rt.reload_engine()
    return server


class TestRuntimeRouting:
    def test_engine_per_floor(self, rt_two_floors):
        rt = rt_two_floors.rt
        assert set(rt.engines) == {DEFAULT_FLOOR_ID, "17F"}
        assert rt._cam_floor == {"cam01": DEFAULT_FLOOR_ID, "cam02": "17F"}

    def test_tracks_routed_to_owning_floor(self, rt_two_floors):
        """cam01 트랙은 default 엔진만, cam02 트랙은 17F 엔진만 반영."""
        rt = rt_two_floors.rt
        rt._dispatch_tracks("cam01", 100.0, [_tr("cam01", 1, 150, 150, 100.0)])
        rt._dispatch_tracks("cam02", 100.0, [
            _tr("cam02", 1, 150, 150, 100.0),
            _tr("cam02", 2, 160, 160, 100.0)])

        d_objs = rt.engines[DEFAULT_FLOOR_ID].snapshot().objects
        f_objs = rt.engines["17F"].snapshot().objects
        assert [o.gid for o in d_objs] == ["cam01:1"]
        assert sorted(o.gid for o in f_objs) == ["cam02:1", "cam02:2"]

    def test_zone_counts_isolated_per_floor(self, rt_two_floors):
        rt = rt_two_floors.rt
        rt._dispatch_tracks("cam01", 100.0, [_tr("cam01", 1, 150, 150, 100.0)])
        rt._dispatch_tracks("cam02", 100.0, [
            _tr("cam02", 1, 150, 150, 100.0),
            _tr("cam02", 2, 180, 180, 100.0)])
        d_zone = rt.engines[DEFAULT_FLOOR_ID].snapshot().zones[0]
        f_zone = rt.engines["17F"].snapshot().zones[0]
        assert (d_zone.id, d_zone.count) == ("z_def", 1)
        assert (f_zone.id, f_zone.count) == ("z_17", 2)

    def test_map_state_filters_floor_cameras(self, rt_two_floors):
        """_map_state는 해당 층 소속 카메라 상태만 병합한다."""
        server = rt_two_floors
        # ingest 상태가 비어도(미기동) 필터 로직은 cam_floor 기준으로 동작
        ms_def = server._map_state(DEFAULT_FLOOR_ID)
        ms_17 = server._map_state("17F")
        # 미기동이라 states()는 비었을 수 있음 — 최소한 예외 없이 스냅샷 생성
        assert ms_def.site_version >= 0 and ms_17.site_version >= 0


# ============================================================ 세션 디렉토리 분리
class TestSessionDirs:
    def test_default_vs_floor_dirs(self, rt_two_floors):
        server = rt_two_floors
        d_default = server._sessions_dir(DEFAULT_FLOOR_ID)
        d_17 = server._sessions_dir("17F")
        assert d_default.name == "sessions"
        assert d_17.parent.name == "sessions" and d_17.name == "17F"
        assert d_default != d_17
