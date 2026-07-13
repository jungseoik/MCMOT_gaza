"""P1 평가 세션 + 4대 지표(SEI·CBS·EPFI·IDR) 단위테스트 — 요구사항 §8 완료 기준.

합성 TrackedObject 궤적 기반 (GPU 불필요).
맵: 1000×1000 px, 축척 0.01 m/px (100 px = 1 m). 카메라: 항등 호모그래피.
판정 임계값은 Thresholds 디폴트(v_th=0.5, a_th=0.7, r_th=0.5,
dt_hold=3.0, d_allow=2.0)를 site.thresholds로 사용한다.
"""
import pytest

from system.config.schema import (
    Bottleneck,
    CameraConfig,
    CameraMapping,
    ExitLine,
    GraphNode,
    MapSpec,
    Route,
    SiteConfig,
    SpatialGraph,
    Thresholds,
    Zone,
)
from system.contracts import EvaluationResult, TrackedObject
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


ROUTE = Route(id="r1", points=[(100, 500), (900, 500)])   # +x 방향 경로


# ================================================================ EPFI (FR-05)


class TestEPFI:
    def feed_line(self, eng, tid, x0, y, v_px_s, t0, t1, dt=0.2, cam="cam01"):
        """등속 +x 직선 궤적을 t0..t1 구간에 공급."""
        n = int(round((t1 - t0) / dt))
        for i in range(n + 1):
            ts = t0 + i * dt
            eng.on_tracks(cam, ts, [tr(cam, tid, x0 + v_px_s * (ts - t0), y, ts)])

    def test_perfect_follow_epfi_100(self):
        """권장경로 완전 추종 궤적 → EPFI = 100 (§8 완료 기준)."""
        eng = MetricsEngine(make_site(routes=[ROUTE]), [make_cam()])
        eng.start_session((100, 500), t_alarm=100.0)
        self.feed_line(eng, 1, 200, 500, 100, 100.0, 106.0)   # 경로 위 1 m/s
        res = eng.stop_session()
        pm = res.person_metrics[0]
        assert pm.assigned_route_id == "r1"
        assert pm.epfi == pytest.approx(100.0)
        assert pm.mean_deviation_m == pytest.approx(0.0, abs=1e-9)
        assert pm.max_deviation_m == pytest.approx(0.0, abs=1e-9)
        assert res.epfi_avg == pytest.approx(100.0)

    def test_constant_offset_epfi_50(self):
        """경로에서 상시 1 m 이탈, d_allow=2 m → EPFI = 50."""
        eng = MetricsEngine(make_site(routes=[ROUTE]), [make_cam()])
        eng.start_session((100, 500), t_alarm=100.0)
        self.feed_line(eng, 1, 200, 600, 100, 100.0, 104.0)   # y=600 → 1 m 이탈
        res = eng.stop_session()
        pm = res.person_metrics[0]
        assert pm.mean_deviation_m == pytest.approx(1.0)
        assert pm.max_deviation_m == pytest.approx(1.0)
        assert pm.epfi == pytest.approx(50.0)

    def test_route_assignment_nearest_first_position(self):
        """배정 경로 = 세션 중 첫 관측 위치의 최근접 Route."""
        r1 = Route(id="r1", points=[(100, 200), (900, 200)])
        r2 = Route(id="r2", points=[(100, 800), (900, 800)])
        eng = MetricsEngine(make_site(routes=[r1, r2]), [make_cam()])
        eng.start_session((100, 500), t_alarm=100.0)
        self.feed_line(eng, 1, 200, 780, 100, 100.0, 103.0)   # r2 근처 시작
        res = eng.stop_session()
        assert res.person_metrics[0].assigned_route_id == "r2"

    def test_short_track_excluded(self):
        """T_i < 최소관측시간(2 s) → epfi=None, 평균에서도 제외."""
        eng = MetricsEngine(make_site(routes=[ROUTE]), [make_cam()])
        eng.start_session((100, 500), t_alarm=100.0)
        self.feed_line(eng, 1, 200, 500, 100, 100.0, 101.0)   # 1 s 관측
        res = eng.stop_session()
        pm = res.person_metrics[0]
        assert pm.duration_sec == pytest.approx(1.0)
        assert pm.epfi is None
        assert res.epfi_avg is None

    def test_no_routes_unassigned(self):
        """경로 미등록 → 경로 미배정: epfi=None, person은 포함 (§8 예외)."""
        eng = MetricsEngine(make_site(), [make_cam()])
        eng.start_session((100, 500), t_alarm=100.0)
        self.feed_line(eng, 1, 200, 500, 100, 100.0, 104.0)
        res = eng.stop_session()
        pm = res.person_metrics[0]
        assert pm.assigned_route_id is None
        assert pm.epfi is None
        assert res.epfi_avg is None


# ================================================================ CBS (FR-06)


BN = Bottleneck(id="b1", rho_crit=1.0, weight=2.0,
                polygon=[(100, 100), (300, 100), (300, 300), (100, 300)])
# 200×200 px = 2×2 m = 4 m²


class TestCBS:
    def feed_static(self, eng, n, t0, t1, dt=0.2):
        """병목 안에 n명 정지 상태로 t0..t1 공급."""
        steps = int(round((t1 - t0) / dt))
        for i in range(steps + 1):
            ts = t0 + i * dt
            eng.on_tracks("cam01", ts,
                          [tr("cam01", k, 150 + k * 10, 150, ts)
                           for k in range(n)])

    def test_under_threshold_cbs_zero(self):
        """임계밀도 미초과(3명/4m²=0.75 < 1.0) → CBS = 0 (§8 완료 기준)."""
        eng = MetricsEngine(make_site(bottlenecks=[BN]), [make_cam()])
        eng.start_session((100, 500), t_alarm=100.0)
        self.feed_static(eng, 3, 100.0, 110.0)
        res = eng.stop_session()
        bm = res.bottleneck_metrics[0]
        assert bm.cbs == 0.0
        assert bm.over_threshold_sec == 0.0
        assert bm.peak_density == pytest.approx(0.75)
        assert bm.risk_level == "low"
        assert res.cbs_total == 0.0

    def test_over_threshold_accumulates(self):
        """8명/4m²=2.0, ρcrit=1.0, w=2 → 초과분 1.0×2 = 2.0/s 누적.

        좌리만 1초 격자: 시작(100 s) 시 0명 → [100,101)은 0,
        이후 [101,110) 9구간 × 2.0 = 18.0. over_threshold_sec = 9 s.
        """
        eng = MetricsEngine(make_site(bottlenecks=[BN]), [make_cam()])
        eng.start_session((100, 500), t_alarm=100.0)
        self.feed_static(eng, 8, 100.0, 110.0)
        res = eng.stop_session()
        bm = res.bottleneck_metrics[0]
        assert bm.peak_density == pytest.approx(2.0)
        assert bm.cbs == pytest.approx(18.0)
        assert bm.over_threshold_sec == pytest.approx(9.0)
        assert bm.risk_level == "high"           # 최대 CBS 병목 → high
        assert res.cbs_total == pytest.approx(18.0)


# ================================================================ SEI (FR-07)


E1 = ExitLine(id="e1", line=((300, 400), (300, 600)), inside=(250, 500),
              design_capacity=60)
E2 = ExitLine(id="e2", line=((700, 400), (700, 600)), inside=(650, 500),
              design_capacity=40)


class TestSEI:
    def cross_out(self, eng, tid, xs, y=500.0, t0=0.0):
        """x 좌표열을 따라 이동 — inside→바깥이면 out 통과."""
        for i, x in enumerate(xs):
            ts = t0 + i * 0.2
            eng.on_tracks("cam01", ts, [tr("cam01", tid, x, y, ts)])

    def test_matching_distribution_sei_100(self):
        """실제 출구분포(6:4) = 설계분포(60:40) → SEI = 100 (§8 완료 기준)."""
        eng = MetricsEngine(make_site(exits=[E1, E2]), [make_cam()])
        eng.start_session((500, 500), t_alarm=0.0)
        for k in range(6):                                   # e1로 6명 out
            self.cross_out(eng, 1 + k, [260, 340], t0=1.0 + k)
        for k in range(4):                                   # e2로 4명 out
            self.cross_out(eng, 11 + k, [660, 740], t0=10.0 + k)
        res = eng.stop_session()
        assert res.sei == pytest.approx(100.0)
        em = {m.exit_id: m for m in res.exit_metrics}
        assert em["e1"].actual_count == 6
        assert em["e2"].actual_count == 4
        assert em["e1"].actual_share == pytest.approx(0.6)
        assert em["e1"].design_share == pytest.approx(0.6)
        assert em["e2"].actual_share == pytest.approx(0.4)

    def test_skewed_distribution(self):
        """전원 e1 쏠림(1.0:0.0) vs 설계(0.6:0.4) → SEI = (1−0.4)×100 = 60."""
        eng = MetricsEngine(make_site(exits=[E1, E2]), [make_cam()])
        eng.start_session((500, 500), t_alarm=0.0)
        for k in range(10):
            self.cross_out(eng, 1 + k, [260, 340], t0=1.0 + k)
        res = eng.stop_session()
        assert res.sei == pytest.approx(60.0)

    def test_zero_pass_insufficient_data(self):
        """ΣE = 0 → sei = None (insufficient_data, §8 예외)."""
        eng = MetricsEngine(make_site(exits=[E1, E2]), [make_cam()])
        eng.start_session((500, 500), t_alarm=0.0)
        self.cross_out(eng, 1, [500, 500, 500], t0=1.0)      # 통과 없음
        res = eng.stop_session()
        assert res.sei is None

    def test_roundtrip_debounce_unique_first_pass(self):
        """왕복 통과 중복 없음 — 같은 gid의 out 재통과는 미집계 (§8 예외)."""
        eng = MetricsEngine(make_site(exits=[E1, E2]), [make_cam()])
        eng.start_session((500, 500), t_alarm=0.0)
        self.cross_out(eng, 1, [260, 340, 260, 340, 260, 340], t0=1.0)
        res = eng.stop_session()
        em = {m.exit_id: m for m in res.exit_metrics}
        assert em["e1"].actual_count == 1                    # 고유 최초 out만

    def test_exit_without_capacity_excluded_from_distribution(self):
        """C_j 미설정 출구는 분포에서 제외, exit_metrics에는 포함."""
        e3 = ExitLine(id="e3", line=((500, 100), (500, 200)),
                      inside=(450, 150))                     # 용량 미설정
        eng = MetricsEngine(make_site(exits=[E1, E2, e3]), [make_cam()])
        eng.start_session((500, 500), t_alarm=0.0)
        self.cross_out(eng, 1, [260, 340], t0=1.0)           # e1 out 1명
        self.cross_out(eng, 2, [460, 540], y=150.0, t0=2.0)  # e3 out 1명
        res = eng.stop_session()
        # 분포는 e1·e2만: (1.0, 0.0) vs (0.6, 0.4) → SEI 60
        assert res.sei == pytest.approx(60.0)
        em = {m.exit_id: m for m in res.exit_metrics}
        assert em["e3"].actual_count == 1
        assert em["e3"].design_capacity is None
        assert em["e3"].actual_share is None
        assert em["e3"].design_share is None


# ================================================================ IDR (FR-03·04)


ZONE = Zone(id="z1", node_id="n2",
            polygon=[(100, 100), (900, 100), (900, 900), (100, 900)])
GRAPH = SpatialGraph(nodes=[GraphNode(id="n1", xy=(100, 500)),
                            GraphNode(id="n2", xy=(600, 500))],
                     edges=[("n1", "n2")])
IDR_ROUTE = Route(id="r1", points=[(0, 500), (1000, 500)])


def make_idr_site(**kw) -> SiteConfig:
    base = dict(zones=[ZONE], routes=[IDR_ROUTE], graph=GRAPH)
    base.update(kw)
    return make_site(**base)


class TestIDR:
    def feed_two(self, eng, t0, t1, move_from, move_until=None, dt=0.2):
        """2명(y=480/520) — move_from 이후 +x 1 m/s, move_until 이후 정지."""
        steps = int(round((t1 - t0) / dt))
        for i in range(steps + 1):
            ts = round(t0 + i * dt, 6)
            m0 = max(0.0, min(ts, move_until or ts) - move_from)
            x = 200 + 100 * max(0.0, m0)
            eng.on_tracks("cam01", ts, [tr("cam01", 1, x, 480, ts),
                                        tr("cam01", 2, x, 520, ts)])

    def test_start_detected_at_expected_time(self):
        """조건이 dt_hold(3 s) 유지 → 유지구간 시작 샘플에 t_e,start (§8).

        정지(100~105 s) 후 1 m/s 이동: 1초 창 속도가 0.5 m/s를 넘는
        최초 정수 샘플은 106 s → cond_since=106, 109 s에 3 s 유지 확정.
        """
        eng = MetricsEngine(make_idr_site(), [make_cam()])
        eng.start_session((100, 500), t_alarm=100.0)
        self.feed_two(eng, 100.0, 110.0, move_from=105.0)
        res = eng.stop_session()
        zm = res.zone_metrics[0]
        assert zm.status == "started"
        assert zm.evacuation_start_at == pytest.approx(106.0)
        assert zm.response_delay_sec == pytest.approx(6.0)
        # 그래프 거리: n1(경보 최근접)→n2(zone.node_id) = 500 px = 5 m
        assert zm.graph_distance == pytest.approx(5.0)
        assert zm.idr == pytest.approx(5.0 / 6.0)
        assert zm.participant_ratio == pytest.approx(1.0)

    def test_stationary_not_started(self):
        """이동 없음 → not_started, idr=None (§8 예외)."""
        eng = MetricsEngine(make_idr_site(), [make_cam()])
        eng.start_session((100, 500), t_alarm=100.0)
        self.feed_two(eng, 100.0, 110.0, move_from=999.0)    # 계속 정지
        res = eng.stop_session()
        zm = res.zone_metrics[0]
        assert zm.status == "not_started"
        assert zm.evacuation_start_at is None
        assert zm.response_delay_sec is None
        assert zm.idr is None
        assert zm.graph_distance == pytest.approx(5.0)       # 거리는 산출

    def test_short_burst_below_hold_not_started(self):
        """2 s만 이동(dt_hold=3 미달) → not_started."""
        eng = MetricsEngine(make_idr_site(), [make_cam()])
        eng.start_session((100, 500), t_alarm=100.0)
        self.feed_two(eng, 100.0, 111.0, move_from=105.0, move_until=107.0)
        res = eng.stop_session()
        assert res.zone_metrics[0].status == "not_started"

    def test_graph_empty_straight_line_fallback(self):
        """그래프 미정의 → 경보위치→구역 중심 직선거리 폴백.
        origin(100,500)→중심(500,500) = 400 px = 4 m."""
        eng = MetricsEngine(
            make_idr_site(graph=SpatialGraph(),
                          zones=[Zone(id="z1", polygon=ZONE.polygon)]),
            [make_cam()])
        eng.start_session((100, 500), t_alarm=100.0)
        self.feed_two(eng, 100.0, 102.0, move_from=999.0)
        res = eng.stop_session()
        assert res.zone_metrics[0].graph_distance == pytest.approx(4.0)


# ================================================================ 세션 수명주기


class TestSessionLifecycle:
    def test_start_stop_live_result(self):
        eng = MetricsEngine(make_site(zones=[ZONE]), [make_cam()])
        live = eng.start_session((100, 500), t_alarm=100.0)
        assert live.session_id == "sess-100000"
        assert live.zones_total == 1
        assert live.elapsed_sec == 0.0
        eng.on_tracks("cam01", 103.0, [tr("cam01", 1, 200, 500, 103.0)])
        assert eng.session_live().elapsed_sec == pytest.approx(3.0)
        assert eng.snapshot().session is not None            # MapState.session
        res = eng.stop_session()
        assert isinstance(res, EvaluationResult)
        assert res.ended_at == pytest.approx(103.0)          # 관측 최신 ts
        assert res.calibration_version == 1
        assert eng.session_live() is None
        assert eng.snapshot().session is None
        assert eng.session_result() is res                   # 결과 보존

    def test_double_start_raises(self):
        eng = MetricsEngine(make_site(), [make_cam()])
        eng.start_session((0, 0), t_alarm=0.0)
        with pytest.raises(RuntimeError):
            eng.start_session((0, 0), t_alarm=1.0)

    def test_stop_without_session_raises(self):
        eng = MetricsEngine(make_site(), [make_cam()])
        with pytest.raises(RuntimeError):
            eng.stop_session()

    def test_start_resets_counters_and_objects(self):
        """세션 시작 시 통과선 카운트·debounce·객체이력 reset (v1.1 예약분)."""
        eng = MetricsEngine(make_site(exits=[E1]), [make_cam()])
        for i, x in enumerate([260, 340]):                   # 세션 전 out 1
            eng.on_tracks("cam01", i * 0.2, [tr("cam01", 1, x, 500, i * 0.2)])
        assert eng.snapshot().exits[0].out_count == 1
        eng.start_session((500, 500), t_alarm=10.0)
        snap = eng.snapshot()
        assert snap.exits[0].out_count == 0                  # 카운터 reset
        assert snap.objects == []                            # 객체이력 reset
        # reset 후 같은 gid가 다시 나가면 새로 1회 집계 (debounce도 reset)
        for i, x in enumerate([260, 340]):
            ts = 10.0 + i * 0.2
            eng.on_tracks("cam01", ts, [tr("cam01", 1, x, 500, ts)])
        assert eng.stop_session().exit_metrics[0].actual_count == 1

    def test_timeline_sampling(self):
        """1초 샘플 타임라인 — ts 단조증가·누적 카운트·종료 후에도 조회."""
        eng = MetricsEngine(make_site(exits=[E1], bottlenecks=[BN]),
                            [make_cam()])
        eng.start_session((500, 500), t_alarm=0.0)
        for i in range(26):                                  # 0.0~5.0 s
            ts = i * 0.2
            eng.on_tracks("cam01", ts, [tr("cam01", 1, 260 + i * 8, 500, ts)])
        tl = eng.session_timeline()
        assert len(tl) >= 5
        tss = [p.ts for p in tl]
        assert tss == sorted(tss) and len(set(tss)) == len(tss)
        assert all("e1" in p.exit_counts for p in tl)
        res = eng.stop_session()
        tl2 = eng.session_timeline()                         # 종료 후 보존
        assert len(tl2) >= len(tl)
        assert tl2[-1].cbs_total == pytest.approx(res.cbs_total)

    def test_quality_unmapped_ratio(self):
        """quality.unmapped_point_ratio — 매핑 없는 카메라 관측은 제외분."""
        eng = MetricsEngine(make_site(), [make_cam("cam01")])
        eng.start_session((0, 0), t_alarm=0.0)
        eng.on_tracks("cam01", 1.0, [tr("cam01", 1, 200, 500, 1.0)])
        eng.on_tracks("cam99", 1.0, [tr("cam99", 1, 200, 500, 1.0)])  # 미매핑
        res = eng.stop_session()
        assert res.quality["unmapped_point_ratio"] == pytest.approx(0.5)
        assert res.quality["cameras_observed"] == ["cam01"]


# ================================================================ 결정성 (§8)


def _run_full_scenario() -> EvaluationResult:
    """4대 지표가 모두 값을 갖는 복합 시나리오 1회 실행."""
    # 구역(맵 전체)에 이동 2명 + 병목 정지 5명 공존 → r_e=2/7≈0.29,
    # v_e도 정지 인원에 희석되어 ≈0.29 m/s. 스펙상 올바른 희석이므로
    # 기본 임계(0.5)로는 개시 미달 — 시나리오 임계값을 명시(설정형 — D-6).
    site = make_site(zones=[ZONE], routes=[IDR_ROUTE], graph=GRAPH,
                     exits=[E1, E2], bottlenecks=[BN],
                     thresholds=Thresholds(v_th=0.2, r_th=0.25))
    eng = MetricsEngine(site, [make_cam()])
    eng.start_session((100, 500), t_alarm=100.0)
    for i in range(51):                                      # 100.0~110.0 s
        ts = round(100.0 + i * 0.2, 6)
        move = max(0.0, ts - 105.0)
        tracks = [tr("cam01", 1, 200 + 100 * move, 480, ts),  # 이동 → IDR·EPFI
                  tr("cam01", 2, 200 + 100 * move, 520, ts)]
        tracks += [tr("cam01", 10 + k, 150 + k * 10, 150, ts)  # 병목 5명
                   for k in range(5)]
        eng.on_tracks("cam01", ts, tracks)
    # 출구 통과: e1 2명, e2 1명
    for k, (tid, xs, t0) in enumerate([(21, [260, 340], 111.0),
                                       (22, [260, 340], 112.0),
                                       (23, [660, 740], 113.0)]):
        for j, x in enumerate(xs):
            ts = t0 + j * 0.2
            eng.on_tracks("cam01", ts, [tr("cam01", tid, x, 500, ts)])
    return eng.stop_session()


class TestDeterminism:
    def test_same_input_same_result(self):
        """같은 입력·같은 설정 두 번 → 동일 EvaluationResult
        (generated_at 제외, §8 결정성)."""
        d1 = _run_full_scenario().model_dump()
        d2 = _run_full_scenario().model_dump()
        d1.pop("generated_at")
        d2.pop("generated_at")
        assert d1 == d2

    def test_full_scenario_all_metrics_present(self):
        """복합 시나리오에서 4대 지표가 모두 산출되는지 스모크."""
        res = _run_full_scenario()
        assert res.sei is not None
        assert res.epfi_avg is not None
        assert res.cbs_total > 0.0
        assert any(z.status == "started" for z in res.zone_metrics)
        assert res.session_id == "sess-100000"
        EvaluationResult.model_validate(res.model_dump())    # 계약 왕복
