"""평가 세션 + 4대 지표(IDR·EPFI·CBS·SEI) 계산 계층 (P1, 계약 v1.2).

MetricsEngine 위에 올라타는 세션 층 — 엔진이 on_tracks 흐름에서 훅
(observe_point / note_dropped / maybe_sample)을 호출하고, 종료 시
finalize()가 EvaluationResult를 조립한다. 수식·예외 정의는 요구사항 문서
§2·§8 그대로: docs/requirements/삼성화재-피난훈련-정량평가-4대지표-요구사항.md

수식 해석상 결정(구현 노트):
- SEI(FR-07): 통과 방향은 **out**(inside 반평면 → 바깥) 기준 — 피난은
  건물 '안'에서 밖으로 나가는 흐름이므로 E_j = 고유 최초 out 통과 수.
  debounce는 엔진 _ExitCounter(gid별 방향당 최초 1회) 재사용.
  C_j 미설정 출구는 분포에서 제외하되 exit_metrics에는 포함.
  ΣE=0 또는 분포 구성 출구 없음 → sei=None (insufficient_data).
- CBS(FR-06): 1초 샘플 격자 **좌리만** 적분 — 직전 샘플에서 관측된
  밀도가 다음 샘플까지 유지된 것으로 본다(over_threshold_sec 동일 기준).
- EPFI(FR-05): 배정 경로 = 세션 중 **첫 관측 위치**의 최근접 Route.
  d_i(t)는 관측 시각 기반 사다리꼴 적분(객체별 관측 주기 그대로).
- IDR(FR-03·04): v_e·a_e·r_e를 1초 샘플로 판정, 조건이 dt_hold 이상
  연속 유지된 **구간의 시작 샘플 시각**을 t_e,start로 본다.
  D = SpatialGraph 다익스트라(m) — 경보위치·구역 각각 최근접 노드
  (구역은 zone.node_id 우선), 그래프 비었거나 도달 불가면 직선거리 폴백.

시간: wall-clock ts(초). 샘플 격자는 t_alarm + k·SAMPLE_INTERVAL_SEC —
같은 입력·같은 설정이면 같은 결과(§8 결정성, generated_at 제외).
판정 임계값(v_th 등)은 전부 site.thresholds에서 읽는다(D-6) — 아래
상수는 알고리즘 파라미터다.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from system.config.schema import Bottleneck, Zone
from system.contracts import (
    BottleneckMetric,
    EvaluationResult,
    ExitMetric,
    PersonMetric,
    SessionLive,
    TimelinePoint,
    ZoneMetric,
)
from system.spatial import (
    nearest_node_id,
    nearest_on_polyline,
    point_in_polygon,
    shortest_dist_px,
)

if TYPE_CHECKING:                       # 순환 import 방지 (타입 전용)
    from system.metrics.engine import MetricsEngine

# ---------------------------------------------------------- 알고리즘 상수
SAMPLE_INTERVAL_SEC = 1.0        # IDR 판정·CBS 적분·타임라인 샘플 주기 (s)
PERSON_SERIES_MAX = 7200         # 객체별 d_i(t) 시계열 상한 (1초 샘플 2시간)
TIMELINE_MAXLEN = 7200           # 타임라인 링버퍼 (1초 샘플 × 2시간)
MIN_TRACK_DURATION_SEC = 2.0     # EPFI 유효 최소 관측시간 T_i (s)
IDR_EPS_SEC = 1e-6               # IDR 분모 ε (delay=0 방지)
RISK_MID_FRAC = 1.0 / 3.0        # risk_level 3분위 경계 (병목 최대 CBS 대비)
RISK_HIGH_FRAC = 2.0 / 3.0


def _centroid(polygon) -> tuple[float, float]:
    """polygon 꼭짓점 평균 (구역 대표점 — 그래프 최근접 노드 탐색용)."""
    xs = [float(p[0]) for p in polygon]
    ys = [float(p[1]) for p in polygon]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


# ------------------------------------------------------------ 누적 상태


@dataclass
class _PersonAcc:
    """EPFI — 객체(gid) 1개의 경로 이탈 누적."""
    gid: str
    route_id: str | None            # 배정 경로 (없으면 미배정 — epfi=None)
    route_pts: np.ndarray | None
    first_ts: float
    last_ts: float
    last_d_m: float | None          # 직전 관측의 이탈거리 (m, 축척 없으면 None)
    integral_dm: float = 0.0        # ∫ d_i dt (m·s, 사다리꼴)
    max_d_m: float | None = None
    series: list = field(default_factory=list)   # d_i(t) 1초 샘플 [(t, d_m), ...]
                                                  # — 지연 표출·역추적용 (FR-05 보강)


@dataclass
class _ZoneAcc:
    """IDR — 구역 1개의 피난개시 판정 상태."""
    zone: Zone
    graph_distance_m: float | None  # D(e, S_origin) — 세션 시작 시 1회 계산
    cond_since: float | None = None    # 조건 연속 성립 시작 샘플 시각
    started_at: float | None = None    # t_e,start
    participant_ratio: float = 0.0     # 판정 시점 r_e


@dataclass
class _BnAcc:
    """CBS — 병목 1개의 혼잡 누적."""
    prev_density: float | None = None  # 직전 샘플 밀도 (좌리만 적분용)
    cbs: float = 0.0
    over_sec: float = 0.0
    peak: float = 0.0


class EvaluationSession:
    """평가 세션 1회의 누적·산출. 엔진 락 안에서만 호출된다(스레드 안전은
    MetricsEngine._lock 소관). 세션 중 설정(reload) 변경은 미지원 —
    시작 시점의 site 설정 사본으로 판정한다."""

    def __init__(self, engine: "MetricsEngine",
                 origin_xy: tuple[float, float], t_alarm: float):
        self._eng = engine
        self.alarm_ts = float(t_alarm)
        self.origin = (float(origin_xy[0]), float(origin_xy[1]))
        # 결정성(§8): session_id는 입력(t_alarm)에서 유도 — uuid 금지
        self.session_id = f"sess-{int(round(self.alarm_ts * 1000))}"

        site = engine._site
        self.site_version = int(site.version)
        self.thresholds = site.thresholds
        self.m_per_px: float | None = engine._m_per_px
        self._graph = site.graph
        # 경로 (id 보존 — 엔진 _routes는 id가 없어 별도 보관)
        self._routes: list[tuple[str, np.ndarray]] = [
            (r.id, np.asarray(r.points, dtype=np.float64))
            for r in site.routes if len(r.points) >= 2]
        # 병목·출구 설정 사본
        self._bn_cfg: list[tuple[Bottleneck, float | None]] = list(
            engine._bottlenecks)
        self._exit_cfg: list[tuple[str, int | None]] = [
            (ex.id, ex.design_capacity) for ex in site.exits]

        # 누적 상태
        self.persons: dict[str, _PersonAcc] = {}
        self.zones: list[_ZoneAcc] = [
            _ZoneAcc(zone=z, graph_distance_m=self._graph_distance_m(z))
            for z, _area in engine._zones]
        self.bns: dict[str, _BnAcc] = {b.id: _BnAcc() for b, _ in self._bn_cfg}
        self.timeline: deque[TimelinePoint] = deque(maxlen=TIMELINE_MAXLEN)
        self.last_sample_ts = self.alarm_ts
        self.last_ts = self.alarm_ts       # 최신 관측 ts
        self.n_points = 0                  # 투영 성공 관측 수
        self.n_dropped = 0                 # 투영 제외(valid_roi 밖·매핑 없음) 수
        self.cams_seen: set[str] = set()
        # 세션 시작 시점 밀도 — 좌리만 적분의 초기 유지값
        for bid, d in self._bn_densities().items():
            self.bns[bid].prev_density = d

    # ---------------------------------------------------- IDR 그래프 거리

    def _graph_distance_m(self, zone: Zone) -> float | None:
        """경보위치→구역 최단거리 (m). 그래프 비었거나 도달 불가 → 직선 폴백.
        축척 없으면 None (실단위 불가 → idr=None)."""
        if self.m_per_px is None:
            return None
        c = _centroid(zone.polygon)
        d_px: float | None = None
        if self._graph.nodes:
            node_ids = {n.id for n in self._graph.nodes}
            src = nearest_node_id(self._graph, self.origin)
            dst = (zone.node_id if zone.node_id in node_ids
                   else nearest_node_id(self._graph, c))   # node_id 우선
            d_px = shortest_dist_px(self._graph, src, dst)
        if d_px is None:                    # 그래프 없음/도달 불가 — 직선 폴백
            d_px = math.dist(self.origin, c)
        return d_px * self.m_per_px

    # ---------------------------------------------------- EPFI 관측 누적

    def _assign_route(self, xy) -> tuple[str | None, np.ndarray | None]:
        """첫 관측 위치의 최근접 Route 배정. 경로 없으면 미배정."""
        if not self._routes:
            return None, None
        rid, pts = min(self._routes,
                       key=lambda r: nearest_on_polyline(xy, r[1]).dist_px)
        return rid, pts

    def _dev_m(self, xy, route_pts: np.ndarray | None) -> float | None:
        """점→배정경로 최근접거리 (m). 경로 미배정·축척 없음 → None."""
        if route_pts is None or self.m_per_px is None:
            return None
        return nearest_on_polyline(xy, route_pts).dist_px * self.m_per_px

    def observe_point(self, gid: str, ts: float, x: float, y: float) -> None:
        """투영 성공한 관측 1점 반영 (엔진 on_tracks 루프에서 호출)."""
        self.n_points += 1
        if ts > self.last_ts:
            self.last_ts = ts
        p = self.persons.get(gid)
        if p is None:
            rid, rpts = self._assign_route((x, y))
            d = self._dev_m((x, y), rpts)
            self.persons[gid] = _PersonAcc(
                gid=gid, route_id=rid, route_pts=rpts,
                first_ts=ts, last_ts=ts, last_d_m=d, max_d_m=d)
            return
        d = self._dev_m((x, y), p.route_pts)
        dt = ts - p.last_ts
        if d is not None and p.last_d_m is not None and dt > 0:
            p.integral_dm += 0.5 * (d + p.last_d_m) * dt   # 사다리꼴
        if d is not None:
            p.max_d_m = d if p.max_d_m is None else max(p.max_d_m, d)
            p.last_d_m = d
        if ts > p.last_ts:
            p.last_ts = ts

    def note_dropped(self, n: int = 1) -> None:
        """투영 제외(valid_roi 밖·매핑 없는 카메라) 관측 수 — quality용."""
        self.n_dropped += int(n)

    # ---------------------------------------------------- 1초 샘플 (IDR·CBS)

    def maybe_sample(self, now: float) -> None:
        """샘플 격자(t_alarm + k·SAMPLE_INTERVAL_SEC)를 now까지 소화."""
        while now - self.last_sample_ts >= SAMPLE_INTERVAL_SEC:
            self._sample(self.last_sample_ts + SAMPLE_INTERVAL_SEC)

    def _obj_rows(self) -> list[tuple[float, float, float | None, float | None]]:
        """현재 추적 객체들의 (x, y, speed_mps, align)."""
        rows = []
        for st in self._eng._objects.values():
            _, x, y = st.hist[-1]
            _vx, _vy, speed, align = self._eng._obj_kinematics(st)
            rows.append((x, y, speed, align))
        return rows

    def _bn_densities(self) -> dict[str, float | None]:
        """병목별 현재 밀도 (명/m²). 면적 미산출(축척 없음) → None."""
        positions = [(st.hist[-1][1], st.hist[-1][2])
                     for st in self._eng._objects.values()]
        out: dict[str, float | None] = {}
        for b, area in self._bn_cfg:
            if not area or area <= 0:
                out[b.id] = None
                continue
            n = sum(1 for p in positions if point_in_polygon(p, b.polygon))
            out[b.id] = n / area
        return out

    def _sample(self, t: float) -> None:
        """샘플 1회 — CBS 좌리만 적분 + IDR 판정 + 타임라인 1점."""
        dt = t - self.last_sample_ts
        # CBS: 직전 샘플에서 유지된 밀도로 [last, t) 구간 적분
        for b, _area in self._bn_cfg:
            acc = self.bns[b.id]
            if acc.prev_density is not None and dt > 0:
                over = acc.prev_density - b.rho_crit
                if over > 0:
                    acc.cbs += over * b.weight * dt
                    acc.over_sec += dt
        densities = self._bn_densities()
        for bid, d in densities.items():
            acc = self.bns[bid]
            if d is not None and d > acc.peak:
                acc.peak = d
            acc.prev_density = d

        # EPFI 보조: 객체별 d_i(t) 1초 시계열 — 최근 관측(3초 내) 객체만 기록
        for p in self.persons.values():
            if p.last_d_m is not None and t - p.last_ts <= 3.0 \
                    and len(p.series) < PERSON_SERIES_MAX:
                p.series.append((t, p.last_d_m))

        # IDR: 구역별 v_e·a_e·r_e → dt_hold 연속 유지 판정
        th = self.thresholds
        rows = self._obj_rows()
        for zacc in self.zones:
            if zacc.started_at is not None:
                continue                          # 이미 개시 판정 완료
            members = [(s, a) for x, y, s, a in rows
                       if point_in_polygon((x, y), zacc.zone.polygon)]
            cond = False
            r_e = 0.0
            if members:
                speeds = [s for s, _ in members if s is not None]
                aligns = [a for _, a in members if a is not None]
                v_e = sum(speeds) / len(speeds) if speeds else None
                a_e = sum(aligns) / len(aligns) if aligns else None
                r_e = sum(1 for s, a in members
                          if s is not None and a is not None
                          and s >= th.v_th and a >= th.a_th) / len(members)
                cond = (v_e is not None and a_e is not None
                        and v_e >= th.v_th and a_e >= th.a_th
                        and r_e >= th.r_th)
            if cond:
                if zacc.cond_since is None:
                    zacc.cond_since = t
                if t - zacc.cond_since >= th.dt_hold:
                    zacc.started_at = zacc.cond_since   # 유지구간 시작 시각
                    zacc.participant_ratio = r_e        # 판정 시점 r_e
            else:
                zacc.cond_since = None

        # 타임라인 1점 (링버퍼)
        self.timeline.append(TimelinePoint(
            ts=t,
            sei=self._sei(),
            cbs_total=self._cbs_total(),
            epfi_avg=self._epfi_avg(),
            zones_started=sum(1 for z in self.zones if z.started_at is not None),
            exit_counts={eid: ec.out_count
                         for eid, ec in self._eng._exits.items()},
            bottleneck_density={bid: round(d, 4)
                                for bid, d in densities.items()
                                if d is not None},
        ))
        self.last_sample_ts = t

    # ---------------------------------------------------- 지표 산출

    def _sei(self) -> float | None:
        """SEI = (1 − ½Σ|E_j/ΣE − C_j/ΣC|) × 100 — E_j는 고유 최초 out 통과.
        C_j 미설정 출구 제외. ΣE=0 → None (insufficient_data)."""
        pairs = [(self._eng._exits[eid].out_count, cj)
                 for eid, cj in self._exit_cfg
                 if cj is not None and eid in self._eng._exits]
        sum_e = sum(e for e, _ in pairs)
        sum_c = sum(c for _, c in pairs)
        if not pairs or sum_e <= 0 or sum_c <= 0:
            return None
        tvd = 0.5 * sum(abs(e / sum_e - c / sum_c) for e, c in pairs)
        return (1.0 - tvd) * 100.0

    def _cbs_total(self) -> float:
        return sum(acc.cbs for acc in self.bns.values())

    def _person_metric(self, p: _PersonAcc) -> PersonMetric:
        """EPFI_i = max(0, 1 − ∫d_i dt / (T_i·d_allow)) × 100.
        T_i < MIN_TRACK_DURATION_SEC·경로 미배정·축척 없음 → epfi=None."""
        t_i = p.last_ts - p.first_ts
        epfi = mean_d = None
        if (p.route_id is not None and p.last_d_m is not None
                and t_i >= MIN_TRACK_DURATION_SEC):
            d_allow = self.thresholds.d_allow
            epfi = max(0.0, 1.0 - p.integral_dm / (t_i * d_allow)) * 100.0
            mean_d = p.integral_dm / t_i
        return PersonMetric(
            global_track_id=p.gid, assigned_route_id=p.route_id,
            duration_sec=t_i, mean_deviation_m=mean_d,
            max_deviation_m=p.max_d_m, epfi=epfi)

    def _epfi_avg(self) -> float | None:
        vals = [m.epfi for m in map(self._person_metric, self.persons.values())
                if m.epfi is not None]
        return sum(vals) / len(vals) if vals else None

    # ---------------------------------------------------- 스냅샷·최종 결과

    def person_series(self) -> dict:
        """객체별 d_i(t) 1초 시계열 — 지연 표출·역추적용 (FR-05 보강, 계약 v1.4)."""
        return {gid: {"route_id": p.route_id,
                      "series": [[round(t, 3), round(d, 3)] for t, d in p.series]}
                for gid, p in self.persons.items() if p.series}

    def live(self, now: float) -> SessionLive:
        return SessionLive(
            session_id=self.session_id,
            alarm_ts=self.alarm_ts,
            alarm_origin=self.origin,
            config_version=self.site_version,
            elapsed_sec=max(0.0, now - self.alarm_ts),
            sei=self._sei(),
            cbs_total=self._cbs_total(),
            epfi_avg=self._epfi_avg(),
            zones_started=sum(1 for z in self.zones if z.started_at is not None),
            zones_total=len(self.zones),
        )

    def finalize(self, now: float) -> EvaluationResult:
        """세션 종료 — 남은 샘플 소화 후 EvaluationResult 조립."""
        self.maybe_sample(now)
        if now > self.last_sample_ts:      # 마지막 부분 구간(<1s)도 적분·판정
            self._sample(now)

        th_eps = IDR_EPS_SEC
        zone_metrics = []
        for zacc in self.zones:
            started = zacc.started_at is not None
            delay = (zacc.started_at - self.alarm_ts) if started else None
            idr = None
            if started and zacc.graph_distance_m is not None:
                idr = zacc.graph_distance_m / max(delay, th_eps)
            zone_metrics.append(ZoneMetric(
                zone_id=zacc.zone.id,
                evacuation_start_at=zacc.started_at,
                response_delay_sec=delay,
                graph_distance=zacc.graph_distance_m,
                idr=idr,
                participant_ratio=zacc.participant_ratio,
                status="started" if started else "not_started"))

        person_metrics = [self._person_metric(p)
                          for p in self.persons.values()]

        # risk_level: 병목 최대 CBS 대비 3분위 (기본 휴리스틱 — 상수 상단)
        max_cbs = max((acc.cbs for acc in self.bns.values()), default=0.0)

        def _risk(c: float) -> str:
            if c <= 0 or max_cbs <= 0:
                return "low"
            f = c / max_cbs
            return ("low" if f <= RISK_MID_FRAC
                    else "mid" if f <= RISK_HIGH_FRAC else "high")

        bottleneck_metrics = [
            BottleneckMetric(
                bottleneck_id=b.id,
                peak_density=round(self.bns[b.id].peak, 4),
                over_threshold_sec=self.bns[b.id].over_sec,
                cbs=self.bns[b.id].cbs,
                risk_level=_risk(self.bns[b.id].cbs))
            for b, _area in self._bn_cfg]

        # 출구 분포 (C_j 설정 출구만) — exit_metrics에는 전 출구 포함
        dist_pairs = [(eid, self._eng._exits[eid].out_count, cj)
                      for eid, cj in self._exit_cfg
                      if cj is not None and eid in self._eng._exits]
        sum_e = sum(e for _, e, _ in dist_pairs)
        sum_c = sum(c for _, _, c in dist_pairs)
        exit_metrics = []
        for eid, cj in self._exit_cfg:
            ec = self._eng._exits.get(eid)
            e = ec.out_count if ec is not None else 0
            in_dist = cj is not None
            exit_metrics.append(ExitMetric(
                exit_id=eid, actual_count=e, design_capacity=cj,
                actual_share=(e / sum_e) if in_dist and sum_e > 0 else None,
                design_share=(cj / sum_c) if in_dist and sum_c > 0 else None))

        quality: dict[str, Any] = {
            "unmapped_point_ratio": (
                round(self.n_dropped / (self.n_points + self.n_dropped), 4)
                if (self.n_points + self.n_dropped) > 0 else 0.0),
            "cameras_observed": sorted(self.cams_seen),
            "track_count": len(self.persons),
            "warnings": ([] if self.m_per_px is not None
                         else ["no_map_scale: 실단위 지표(IDR·EPFI·CBS) 미산출"]),
        }

        return EvaluationResult(
            session_id=self.session_id,
            calibration_version=self.site_version,
            config_version=self.site_version,
            alarm_ts=self.alarm_ts,
            alarm_origin=self.origin,
            ended_at=now,
            zone_metrics=zone_metrics,
            person_metrics=person_metrics,
            bottleneck_metrics=bottleneck_metrics,
            exit_metrics=exit_metrics,
            sei=self._sei(),
            epfi_avg=self._epfi_avg(),
            cbs_total=self._cbs_total(),
            quality=quality,
            generated_at=time.time(),      # 결정성 비교에서 제외되는 유일 필드
        )
