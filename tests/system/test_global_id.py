"""글로벌 ID (v1.13) — 카메라 간 동일인 연결·여정·온오프 회귀 (GPU 불필요).

전제(사용자 확정 설계): 카메라 매핑 헐은 서로 겹치지 않는다.
맵: 1000×1000 px, 축척 0.01 m/px. cam1 = 좌측 절반, cam2 = 우측 절반(항등 투영).
"""
import numpy as np
import pytest

from system.config.schema import (
    CameraConfig,
    CameraMapping,
    ExitLine,
    MapSpec,
    SiteConfig,
    Zone,
)
from system.contracts import TrackedObject
from system.identity import GlobalIdService
from system.identity import global_id as gidmod
from system.metrics import MetricsEngine
from system.metrics import recorder as rec

M_PER_PX = 0.01


def emb(seed: int, dim: int = 64) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype(np.float32)
    return v / np.linalg.norm(v)


def make_site(**kw) -> SiteConfig:
    return SiteConfig(site_id="t", version=1,
                      map=MapSpec(image="m.png", w=1000, h=1000, m_per_px=M_PER_PX), **kw)


def cam_region(cam_id: str, x0: float, x1: float) -> CameraConfig:
    """맵의 x∈[x0,x1] 세로 띠만 커버하는 항등 투영 카메라 — 배타 헐."""
    pts = [(float(x0), 0.0), (float(x1), 0.0), (float(x1), 1000.0), (float(x0), 1000.0)]
    return CameraConfig(cam_id=cam_id, rtsp="rtsp://t",
                        mapping=CameraMapping(cctv_pts=pts, map_pts=pts,
                                              H=[1, 0, 0, 0, 1, 0, 0, 0, 1]))


def tr(cam, tid, x, y, ts, e=None, hint=None) -> TrackedObject:
    return TrackedObject(cam_id=cam, local_track_id=tid, foot_uv=(x, y),
                         bbox_xyxy=(x - 10, y - 40, x + 10, y), conf=0.9, ts=ts,
                         emb=e, gid_hint=hint)


@pytest.fixture()
def gid_on(tmp_path, monkeypatch):
    """토글 on (임시 설정 파일 + 모듈 캐시 격리)."""
    monkeypatch.setattr(gidmod, "STATE_FILE", tmp_path / "global_id.json")
    gidmod._cache = None
    gidmod.save_settings({"enabled": True})
    yield
    gidmod._cache = None


@pytest.fixture()
def gid_off(tmp_path, monkeypatch):
    monkeypatch.setattr(gidmod, "STATE_FILE", tmp_path / "none.json")
    gidmod._cache = None
    yield
    gidmod._cache = None


# ------------------------------------------------------------ 설정


def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(gidmod, "STATE_FILE", tmp_path / "g.json")
    gidmod._cache = None
    assert gidmod.get_settings()["enabled"] is False       # 기본 off = 현행 동작
    d = gidmod.save_settings({"enabled": True, "ttl_sec": 120, "cos_th": 0.6})
    assert d["enabled"] is True and d["ttl_sec"] == 120.0 and d["cos_th"] == 0.6
    gidmod._cache = None                                    # 캐시 무효화 후 재로드
    assert gidmod.get_settings()["ttl_sec"] == 120.0
    gidmod._cache = None


# ------------------------------------------------------------ 서비스 단위


class TestService:
    def test_handover_same_person(self):
        """A헐 이탈 → 잠시 뒤 B헐 등장, 같은 특징 → 같은 id (핸드오버)."""
        s = GlobalIdService(cos_th=0.45, min_new_obs=3)
        assert s.resolve("cam1", 1, emb(1), 100.0) is None   # 관측 1·2회 — 생성 보류
        assert s.resolve("cam1", 1, emb(1), 100.2) is None
        assert s.resolve("cam1", 1, emb(1), 100.4) == "g1"   # 3회째 생성
        assert s.resolve("cam2", 7, emb(1), 102.0) == "g1"   # 기존 정체성 매칭은 즉시

    def test_ghost_track_gets_no_id(self):
        """1~2프레임 유령 트랙 — id·여정을 만들지 않는다 (id 남발 방지)."""
        s = GlobalIdService(min_new_obs=3)
        assert s.resolve("cam1", 5, emb(3), 100.0) is None
        assert s.resolve("cam1", 5, emb(3), 100.2) is None
        assert s.lookup("cam1", 5) is None

    def test_reject_when_active_elsewhere(self):
        """동시 활성 기각 — 한 사람이 같은 순간 두 트랙일 수 없다(배타 헐).
        다른 카메라뿐 아니라 같은 카메라의 옆 사람(동시 트랙)도 기각된다."""
        s = GlobalIdService(cos_th=0.45, min_new_obs=1)
        s.resolve("cam1", 1, emb(1), 100.0)
        s.resolve("cam1", 1, emb(1), 100.2)                  # 계속 관측 중(활성)
        assert s.resolve("cam2", 9, emb(1), 100.3) == "g2"   # 타 카메라 동시 → 기각
        s.resolve("cam1", 1, emb(1), 100.4)
        assert s.resolve("cam1", 2, emb(1), 100.5) == "g3"   # 같은 카메라 옆 트랙도 기각

    def test_ttl_expiry_new_id(self):
        s = GlobalIdService(ttl_sec=10.0, cos_th=0.45, min_new_obs=1)
        s.resolve("cam1", 1, emb(1), 100.0)
        assert s.resolve("cam2", 2, emb(1), 200.0) == "g2"   # 기억 시간 지남

    def test_dissimilar_new_id(self):
        s = GlobalIdService(cos_th=0.45, min_new_obs=1)
        s.resolve("cam1", 1, emb(1), 100.0)
        assert s.resolve("cam2", 2, emb(2), 105.0) == "g2"   # 특징 다름

    def test_dummy_emb_returns_none(self):
        """use_reid off 의 더미 임베딩(size 1) — 매칭 불가, None."""
        s = GlobalIdService()
        assert s.resolve("cam1", 1, np.ones((1,)), 100.0) is None

    def test_binding_sticky(self):
        """한 번 묶인 (cam,local) 은 특징이 흔들려도 같은 id 유지."""
        s = GlobalIdService(cos_th=0.45, min_new_obs=1)
        g = s.resolve("cam1", 1, emb(1), 100.0)
        assert s.resolve("cam1", 1, emb(2), 100.2) == g

    def test_speed_gate_rejects_teleport(self):
        """속도 게이트 — 물리적으로 불가능한 재등장(50m/2s=25m/s)은 닮아도 타인.
        외형이 비슷한 두 사람이 번갈아 한 id 로 스왑되는 핑퐁 오병합 차단."""
        s = GlobalIdService(cos_th=0.45, min_new_obs=1, max_speed_mps=3.0)
        s.resolve("cam1", 1, emb(1), 100.0, pos_m=(0.0, 0.0))
        assert s.resolve("cam2", 2, emb(1), 102.0, pos_m=(50.0, 0.0)) == "g2"
        # 같은 거리라도 시간이 충분하면(50m/60s) 정상 매칭
        s2 = GlobalIdService(cos_th=0.45, min_new_obs=1, max_speed_mps=3.0)
        s2.resolve("cam1", 1, emb(1), 100.0, pos_m=(0.0, 0.0))
        assert s2.resolve("cam2", 2, emb(1), 160.0, pos_m=(50.0, 0.0)) == "g1"


# ------------------------------------------------------------ 엔진 통합


def _eng(exits=(), zones=()):
    site = make_site(exits=list(exits), zones=list(zones))
    return MetricsEngine(site, [cam_region("cam1", 0, 500),
                                cam_region("cam2", 500, 1000)])


class TestEngineToggle:
    def test_off_mode_keeps_local_gid(self, gid_off):
        """off(기본) 회귀 — emb 가 있어도 gid 는 카메라 로컬 합성키."""
        eng = _eng()
        eng.on_tracks("cam1", 100.0, [tr("cam1", 1, 100, 500, 100.0, e=emb(1))])
        ms = eng.snapshot()
        assert [o.gid for o in ms.objects] == ["cam1:1"]

    def test_off_mode_result_has_no_journeys(self, gid_off):
        eng = _eng()
        eng.start_session(origin_xy=(500.0, 500.0), t_alarm=100.0)
        eng.on_tracks("cam1", 100.2, [tr("cam1", 1, 100, 500, 100.2, e=emb(1))])
        res = eng.stop_session()
        assert res.global_id is False and res.journeys == []


class TestEngineGlobal:
    EXIT = ExitLine(id="e1", line=((900, 400), (900, 600)), inside=(800, 500))
    ZONE = Zone(id="z1", polygon=[(50, 450), (150, 450), (150, 550), (50, 550)])

    def _run_handover(self, eng):
        """cam1 x100→480 · 3s 갭 · cam2 x520→950(출구 900 통과). 같은 특징."""
        e = emb(5)
        t = 100.0
        for x in range(100, 481, 20):
            eng.on_tracks("cam1", t, [tr("cam1", 3, float(x), 500.0, t, e=e)])
            t += 0.2
        t += 3.0
        for x in range(520, 951, 20):
            eng.on_tracks("cam2", t, [tr("cam2", 11, float(x), 500.0, t, e=e)])
            t += 0.2
        return t

    def test_handover_journey_and_exit(self, gid_on):
        eng = _eng(exits=[self.EXIT], zones=[self.ZONE])
        eng.start_session(origin_xy=(500.0, 500.0), t_alarm=100.0)
        self._run_handover(eng)
        ms = eng.snapshot()
        assert all(o.gid == "g1" for o in ms.objects)        # 두 카메라 = 같은 사람
        res = eng.stop_session()
        assert res.global_id is True
        assert len(res.journeys) == 1
        j = res.journeys[0]
        assert j.gid == "g1"
        assert j.start_zone == "z1"                          # 어디서 시작했나
        assert j.exit_id == "e1" and j.exit_ts is not None   # 어느 출구로 나갔나
        assert [s.cam_id for s in j.segments] == ["cam1", "cam2"]
        # 거리 = cam1 3.4m(바인딩이 3관측째 x=140 부터) + 갭 브리지 0.4m + cam2 4.3m
        assert j.total_dist_m == pytest.approx(8.1, abs=0.3)
        assert 0.0 < j.coverage_ratio < 1.0                  # 갭이 커버리지에 드러남
        assert j.avg_speed_mps is not None and j.avg_speed_mps > 0
        # 출구 debounce 는 글로벌 키 — 같은 사람은 1명만
        assert sum(em.actual_count for em in res.exit_metrics) == 1

    def test_kinematics_stay_per_camera(self, gid_on):
        """핸드오버 순간이 순간속도(v_th 판정)를 오염시키지 않는다 —
        운동학 이력은 카메라 로컬 키라 갭 직후 속도는 새 구간에서 다시 시작."""
        eng = _eng()
        eng.start_session(origin_xy=(500.0, 500.0), t_alarm=100.0)
        e = emb(6)
        for i, x in enumerate((440.0, 460.0, 480.0)):        # 3관측 — g1 생성
            eng.on_tracks("cam1", 100.0 + 0.2 * i,
                          [tr("cam1", 3, x, 500.0, 100.0 + 0.2 * i, e=e)])
        # 0.5초 뒤 cam2 에서 등장 — 기존 정체성 매칭은 1프레임에 즉시.
        # (이력이 섞였다면 window 양끝이 카메라를 넘어 속도 스파이크로 보였을 것)
        eng.on_tracks("cam2", 100.9, [tr("cam2", 11, 520.0, 500.0, 100.9, e=e)])
        ms = eng.snapshot()
        cam2_obj = next(o for o in ms.objects if o.cam_id == "cam2")
        assert cam2_obj.gid == "g1"
        assert (cam2_obj.speed_mps or 0.0) == 0.0            # 새 구간 첫 샘플 — 속도 없음
        eng.stop_session()


# ------------------------------------------------------------ 녹화·리플레이


class TestRecorderGid:
    def test_roundtrip_gid_column(self, tmp_path):
        db = tmp_path / "s.db"
        r = rec.SessionRecorder(db, {"session_id": "s", "alarm_ts": 1.0})
        r.record("cam1", 100.0, [tr("cam1", 1, 100, 500, 100.0)], gids=["g1"])
        r.record("cam1", 100.2, [tr("cam1", 1, 102, 500, 100.2)], gids=[None])
        r.close()
        calls = list(rec.iter_calls(db))
        assert calls[0][2][0].gid_hint == "g1"
        assert calls[1][2][0].gid_hint is None

    def test_replay_hint_without_service(self, gid_off):
        """리플레이 결정성 — 서비스 없이 녹화된 gid 힌트만으로 같은 결과."""
        eng = _eng(zones=[TestEngineGlobal.ZONE])
        eng.start_session(origin_xy=(500.0, 500.0), t_alarm=100.0)
        t = 100.0
        for x in range(100, 481, 20):
            eng.on_tracks("cam1", t, [tr("cam1", 3, float(x), 500.0, t, hint="g7")])
            t += 0.2
        for x in range(520, 951, 20):
            eng.on_tracks("cam2", t, [tr("cam2", 11, float(x), 500.0, t, hint="g7")])
            t += 0.2
        res = eng.stop_session()
        assert res.global_id is True
        assert len(res.journeys) == 1 and res.journeys[0].gid == "g7"
        assert len(res.journeys[0].segments) == 2
