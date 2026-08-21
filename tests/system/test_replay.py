"""세션 녹화·리플레이·재계산 (계약 v1.10) 단위테스트.

핵심 검증:
- 동치: 같은 임계값으로 재계산하면 원본 result와 (generated_at 제외) 동일 —
  엔진의 결정성 + 녹화 스트림 재생이 원본을 정확히 재현함을 보장.
- 재파라미터화: d_allow를 좁히면 EPFI가 하락, rho_crit를 낮추면 CBS 증가 —
  '역방향 재파라미터화'가 실제로 동작.

합성 궤적 기반 (GPU 불필요). test_session.py 헬퍼 재사용.
"""
from system.config.schema import Bottleneck
from system.metrics import MetricsEngine
from system.metrics.recorder import SessionRecorder
from system.metrics.replay import run_replay

from tests.system.test_session import ROUTE, make_cam, make_site, tr


def _feed_line(eng, tid, x0, y, v_px_s, t0, t1, dt=0.2, cam="cam01"):
    n = int(round((t1 - t0) / dt))
    for i in range(n + 1):
        ts = t0 + i * dt
        eng.on_tracks(cam, ts, [tr(cam, tid, x0 + v_px_s * (ts - t0), y, ts)])


def _record(db, site, cam, feed):
    """라이브 엔진에 녹화기를 달고 feed 실행 → (원본 result, meta 저장 완료)."""
    eng = MetricsEngine(site, [cam])
    live = eng.start_session((100, 500), t_alarm=100.0)
    meta = {
        "session_id": live.session_id, "floor_id": "default", "site_id": "test",
        "alarm_ts": live.alarm_ts,
        "alarm_origins": [list(o) for o in live.alarm_origins],
        "site_version": site.version,
        "site_view": site.as_floor_view().model_dump(),
        "cameras": [cam.model_dump()],
    }
    rec = SessionRecorder(db, meta)
    eng.attach_recorder(rec)
    feed(eng)
    result = eng.stop_session()
    eng.detach_recorder()
    rec.close()
    return result


def test_replay_equivalence(tmp_path):
    """같은 임계값 재계산 = 원본 (generated_at 제외 완전 일치)."""
    site, cam = make_site(routes=[ROUTE]), make_cam()
    db = tmp_path / "sess.db"
    orig = _record(db, site, cam,
                   lambda e: _feed_line(e, 1, 200, 500, 100, 100.0, 106.0))
    result, timeline, frames, meta = run_replay(db, {}, fps=5.0)
    a, b = orig.model_dump(), result.model_dump()
    a.pop("generated_at"), b.pop("generated_at")
    assert a == b, "재계산 결과가 원본과 불일치 (결정성 위반)"
    assert frames, "재생 프레임이 비어 있음"
    assert meta["track_row_count"] > 0


def test_recompute_d_allow_changes_epfi(tmp_path):
    """경로 이탈 궤적 → d_allow 좁히면 EPFI 하락 (역방향 재파라미터화)."""
    site, cam = make_site(routes=[ROUTE]), make_cam()
    db = tmp_path / "sess.db"
    # route는 y=500, 궤적은 y=600 (100px=1m 이탈)
    _record(db, site, cam,
            lambda e: _feed_line(e, 1, 200, 600, 100, 100.0, 106.0))
    r_wide, *_ = run_replay(db, {"thresholds": {"d_allow": 2.0}}, 5.0)
    r_narrow, *_ = run_replay(db, {"thresholds": {"d_allow": 0.5}}, 5.0)
    assert r_wide.epfi_avg is not None and r_narrow.epfi_avg is not None
    assert r_narrow.epfi_avg < r_wide.epfi_avg


def test_recompute_rho_crit_changes_cbs(tmp_path):
    """밀집 궤적 → rho_crit 낮추면 CBS 증가 (병목 재파라미터화)."""
    bn = Bottleneck(id="b1", polygon=[(400, 400), (600, 400), (600, 600), (400, 600)],
                    rho_crit=2.0)
    site, cam = make_site(bottlenecks=[bn]), make_cam()
    db = tmp_path / "sess.db"

    def feed(e):
        # 병목(2m×2m=4m²) 안에 5명 정지 → 밀도 1.25명/m²
        for k in range(30):
            ts = 100.0 + k * 0.2
            e.on_tracks("cam01", ts, [tr("cam01", i, 450 + i * 20, 500, ts)
                                      for i in range(5)])
    _record(db, site, cam, feed)
    r_hi, *_ = run_replay(db, {"rho_crit": 2.0}, 5.0)     # 임계 초과 없음
    r_lo, *_ = run_replay(db, {"rho_crit": 0.5}, 5.0)     # 임계 초과 → 누적
    assert r_lo.cbs_total > r_hi.cbs_total


def test_replay_camera_zone_exit_needs_bbox(tmp_path):
    """화면 영역 출입구(ZoneGate) 카운트가 리플레이에서 재현된다 (v1.12).

    ZoneGate는 발끝점이 문틀에 잘리는 것을 bbox 겹침으로 보정한다. 녹화가
    bbox를 안 남기면 그 보정이 죽어 통과 인원이 리플레이에서 빠진다
    (16F 실측: 라이브 19명 → 리플레이 6명). bbox를 기록하므로 일치해야 한다.
    """
    from system.config.schema import ExitLine
    # 화면 영역: 카메라 px (0..200, 0..200). 발끝점은 영역 밖으로만 두고
    # bbox 절반이 영역과 겹치게 만들어 "bbox 보정으로만 잡히는" 통과를 만든다.
    ex = ExitLine(id="e1", line=((300, 400), (300, 600)), inside=(250, 500),
                  width_m=2.0, q_design=30, count_cam="cam01",
                  cam_zone=[(0, 0), (200, 0), (200, 200), (0, 200)],
                  cam_zone_dwell=2)
    site, cam = make_site(exits=[ex]), make_cam()
    db = tmp_path / "sess.db"

    def feed(e):
        from system.contracts import TrackedObject
        for k in range(10):
            ts = 100.0 + k * 0.2
            # k<3: 영역에서 완전히 떨어짐(밖에서 본 이력) / 이후: bbox만 겹침
            if k < 3:
                bbox = (400.0, 400.0, 460.0, 500.0); foot = (430.0, 500.0)
            else:
                bbox = (100.0, 100.0, 260.0, 260.0); foot = (240.0, 260.0)
            e.on_tracks("cam01", ts, [TrackedObject(
                cam_id="cam01", local_track_id=1, foot_uv=foot,
                bbox_xyxy=bbox, conf=0.9, ts=ts)])

    orig = _record(db, site, cam, feed)
    assert orig.exit_metrics[0].actual_count == 1, "bbox 보정 통과가 라이브에서 안 잡힘"
    result, *_ = run_replay(db, {}, 5.0)
    assert result.exit_metrics[0].actual_count == 1, "리플레이에서 통과가 누락됨(bbox 미기록)"
