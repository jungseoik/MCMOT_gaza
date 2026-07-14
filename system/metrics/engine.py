"""맵 좌표 기반 지표 엔진 (M5).

webui/speed.py(속도 sliding-window·밀도)·webui/counter.py(방향성 crossing)
의 검증된 로직을 **맵 좌표계·실단위(m/s, 명/m²)·다중 인스턴스**
(구역 N · 병목 N · 통과선 N · 경로 M)로 이식한 엔진.

- 입력: 트랙 A가 `on_tracks(cam_id, ts, tracks)`로 카메라 px TrackedObject 공급
  → 내부에서 카메라별 호모그래피로 맵 투영(spatial 소유) 후 지표 갱신
- 출력: `snapshot()` → contracts.MapState (SSE/폴링용 스냅샷)
- 시간: wall-clock ts(초, float)만 사용 — 프레임 인덱스 가정 금지.
  "현재"는 관측된 최신 ts를 쓴다(합성 데이터·녹화 재생 모두 결정적, 요구사항 §8).
- 임계값(rho_crit 등)·구역·경로는 전부 SiteConfig에서 읽는다 — 하드코딩 금지(D-6/D-10).
  생성자 키워드 인자는 판정 임계값이 아니라 알고리즘 파라미터(창 길이 등)다.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from system.config.schema import CameraConfig, SiteConfig
from system.contracts import (
    BottleneckState,
    EvaluationResult,
    ExitState,
    MapObject,
    MapState,
    SessionLive,
    TimelinePoint,
    TrackedObject,
    ZoneState,
)
from system.metrics.session import EvaluationSession
from system.spatial import (
    CameraProjector,
    DirectionalLine,
    nearest_on_polyline,
    point_in_polygon,
    polygon_area_m2,
)


@dataclass
class _ObjState:
    """객체 1개의 맵 좌표 이력 (sliding window)."""
    cam_id: str
    local_id: int
    gid: str
    hist: deque = field(default_factory=deque)   # (ts, x, y) 맵 px
    last_ts: float = 0.0
    first_ts: float = 0.0                        # 첫 관측 (체류시간용, v1.5)
    conf: float = 0.0                            # 최근 검출 신뢰도
    in_bounds: bool = True


@dataclass
class _ExitCounter:
    """통과선 1개의 카운터 + gid별 왕복 debounce 상태.

    debounce 정책(요구사항 FR-07): 같은 gid는 방향별로 **최초 통과만** 유효 —
    왕복 재진입은 중복 집계하지 않는다 (1인 왕복 N회 → in 1 · out 1 유지).
    """
    line: DirectionalLine
    cfg_key: tuple                                # 선 기하 변경 감지용
    in_count: int = 0
    out_count: int = 0
    counted_in: set = field(default_factory=set)   # 이미 in 집계된 gid
    counted_out: set = field(default_factory=set)  # 이미 out 집계된 gid

    def observe(self, gid: str, pt: tuple[float, float]) -> None:
        ev = self.line.observe(gid, pt)
        if ev == "in" and gid not in self.counted_in:
            self.counted_in.add(gid)
            self.in_count += 1
        elif ev == "out" and gid not in self.counted_out:
            self.counted_out.add(gid)
            self.out_count += 1


class MetricsEngine:
    """멀티카메라 맵 지표 엔진 — 트랙 A의 분석 스레드가 on_tracks를 호출하고,
    API 스레드가 snapshot을 읽는다 (내부 락으로 보호).

    알고리즘 파라미터 (판정 임계값 아님 — 임계값은 site.thresholds 소관):
      window_sec       속도/방향 sliding window 길이 (초)
      min_move_m       이 미만 이동은 speed=0 처리 (검출 지터 흡수, m)
      lost_timeout_sec 이 시간 이상 미관측 객체 제거 (초)
      margin_m         통과선 데드밴드 반폭 (m) — 축척으로 px 환산
    """

    def __init__(self, site: SiteConfig, cameras: list[CameraConfig], *,
                 window_sec: float = 1.0, min_move_m: float = 0.05,
                 lost_timeout_sec: float = 3.0, margin_m: float = 0.1):
        self.window_sec = float(window_sec)
        self.min_move_m = float(min_move_m)
        self.lost_timeout_sec = float(lost_timeout_sec)
        self.margin_m = float(margin_m)
        self._lock = threading.Lock()
        self._objects: dict[str, _ObjState] = {}   # gid -> 상태
        self._latest_ts: float | None = None
        self._exits: dict[str, _ExitCounter] = {}
        # 평가 세션 (계약 v1.2) — 진행 중 세션·마지막 결과·타임라인
        self._session: EvaluationSession | None = None
        self._last_result: EvaluationResult | None = None
        self._last_timeline: list[TimelinePoint] = []
        self._last_person_series: dict = {}
        self.reload(site, cameras)

    # ------------------------------------------------------------ 설정 반영

    def reload(self, site: SiteConfig, cameras: list[CameraConfig]) -> None:
        """운영 중 설정 갱신 반영 — 통과선 카운트는 id 기준으로 보존한다."""
        with self._lock:
            self._site = site
            spec = site.map
            # px↔m 축척 — MapSpec.resolve_m_per_px() 단일 지점 (계약 §1)
            self._m_per_px: float | None = spec.resolve_m_per_px() if spec else None
            map_w = spec.w if spec else None
            map_h = spec.h if spec else None

            # 카메라별 투영기 (mapping 없는 카메라는 처리 제외 — 계약)
            self._projectors = {
                cam.cam_id: CameraProjector(cam, map_w, map_h)
                for cam in cameras if cam.mapping is not None
            }

            # 구역/병목 — polygon + 면적(m²)은 설정 시점에 1회 계산
            self._zones = [
                (z, self._area_m2(z.polygon)) for z in site.zones]
            self._bottlenecks = [
                (b, self._area_m2(b.polygon)) for b in site.bottlenecks]

            # 경로 polyline (정렬도용) — 없으면 align=None
            self._routes = [np.asarray(r.points, dtype=np.float64)
                            for r in site.routes if len(r.points) >= 2]

            # 통과선 — 기하가 같으면 부호상태까지, 다르면 카운트만 승계
            margin_px = (self.margin_m / self._m_per_px
                         if self._m_per_px else 0.0)
            old = self._exits
            self._exits = {}
            for ex in site.exits:
                key = (tuple(map(tuple, ex.line)), tuple(ex.inside))
                line = DirectionalLine(ex.line, ex.inside, margin_px=margin_px)
                st = _ExitCounter(line=line, cfg_key=key)
                prev = old.get(ex.id)
                if prev is not None:
                    st.in_count, st.out_count = prev.in_count, prev.out_count
                    st.counted_in = prev.counted_in
                    st.counted_out = prev.counted_out
                    if prev.cfg_key == key:      # 선 기하 동일 → 부호상태 유지
                        st.line = prev.line
                self._exits[ex.id] = st

    def _area_m2(self, polygon) -> float | None:
        if self._m_per_px is None:
            return None
        return polygon_area_m2(polygon, self._m_per_px)

    # ------------------------------------------------------------ 트랙 입력

    def on_tracks(self, cam_id: str, ts: float,
                  tracks: list[TrackedObject]) -> None:
        """트랙 A 진입점 — 카메라 1대의 한 프레임 트랙 묶음을 반영한다."""
        with self._lock:
            ts = float(ts)
            if self._latest_ts is None or ts > self._latest_ts:
                self._latest_ts = ts
            sess = self._session                 # 평가 세션 (없으면 None)
            proj = self._projectors.get(cam_id)
            if proj is None:                     # mapping 미설정 → 처리 제외
                if sess is not None and tracks:
                    sess.note_dropped(len(tracks))   # quality: 투영 제외
                return
            if sess is not None:
                sess.cams_seen.add(cam_id)
            min_conf = self._site.thresholds.min_conf
            for tr in tracks:
                if tr.conf < min_conf:           # 저신뢰 관측 — 오탐 연명 트랙 차단
                    continue                     # (BYTE 저신뢰 연관 유령 객체 방지)
                p = proj.project(tr.foot_uv)
                if p is None:                    # valid_roi 밖 — 제외
                    self._drop(f"{cam_id}:{tr.local_track_id}")
                    if sess is not None:
                        sess.note_dropped()
                    continue
                gid = f"{cam_id}:{tr.local_track_id}"
                st = self._objects.get(gid)
                if st is None:
                    st = self._objects[gid] = _ObjState(
                        cam_id=cam_id, local_id=int(tr.local_track_id), gid=gid,
                        first_ts=ts)
                st.conf = tr.conf
                st.hist.append((ts, p.x, p.y))
                while len(st.hist) > 1 and ts - st.hist[0][0] > self.window_sec:
                    st.hist.popleft()            # sliding window 유지
                st.last_ts = ts
                st.in_bounds = p.in_bounds
                for ec in self._exits.values():  # 방향성 crossing 관측
                    ec.observe(gid, (p.x, p.y))
                if sess is not None:             # EPFI 관측 누적
                    sess.observe_point(gid, ts, p.x, p.y)
            self._purge(self._latest_ts)
            if sess is not None:                 # 1초 샘플 (IDR·CBS·타임라인)
                sess.maybe_sample(self._latest_ts)

    def _drop(self, gid: str) -> None:
        self._objects.pop(gid, None)
        for ec in self._exits.values():
            ec.line.forget(gid)

    def _purge(self, now: float) -> None:
        """lost_timeout_sec 이상 미관측 객체 제거 (debounce 집계는 유지)."""
        stale = [gid for gid, st in self._objects.items()
                 if now - st.last_ts > self.lost_timeout_sec]
        for gid in stale:
            self._drop(gid)

    # ------------------------------------------ 평가 세션 (계약 v1.2 · P1)
    # 수식·예외: docs/requirements/삼성화재-피난훈련-정량평가-4대지표-요구사항.md
    # 계산 본체는 system/metrics/session.py — 여기는 수명주기 훅만 둔다.

    def reset(self) -> None:
        """런타임 상태 리셋 (계약 v1.1 예약분 이행) —
        객체 이력·통과선 카운트·왕복 debounce 초기화. 설정은 유지."""
        with self._lock:
            self._reset_locked()

    def _reset_locked(self) -> None:
        self._objects.clear()
        margin_px = (self.margin_m / self._m_per_px
                     if self._m_per_px else 0.0)
        self._exits = {
            ex.id: _ExitCounter(
                line=DirectionalLine(ex.line, ex.inside, margin_px=margin_px),
                cfg_key=(tuple(map(tuple, ex.line)), tuple(ex.inside)))
            for ex in self._site.exits}

    def start_session(self, origin_xy: tuple[float, float],
                      t_alarm: float | None = None) -> SessionLive:
        """평가 세션 시작 — 내부 카운터·debounce·객체이력 reset 후 누적 개시.
        site version을 calibration/config_version으로 기록.
        진행 중이면 RuntimeError (API 층이 409로 매핑)."""
        with self._lock:
            if self._session is not None:
                raise RuntimeError("평가 세션이 이미 진행 중")
            if t_alarm is None:
                t_alarm = time.time()
            self._reset_locked()
            self._session = EvaluationSession(self, origin_xy, float(t_alarm))
            return self._session.live(float(t_alarm))

    def _session_now(self) -> float:
        """세션 기준 '현재' — 관측된 최신 ts (없으면 alarm_ts, 결정성 §8)."""
        assert self._session is not None
        now = (self._latest_ts if self._latest_ts is not None
               else self._session.alarm_ts)
        return max(now, self._session.alarm_ts)

    def stop_session(self) -> EvaluationResult:
        """세션 종료 — 최종 EvaluationResult 산출·보존 후 세션 해제."""
        with self._lock:
            if self._session is None:
                raise RuntimeError("진행 중인 평가 세션 없음")
            result = self._session.finalize(self._session_now())
            self._last_result = result
            self._last_timeline = list(self._session.timeline)
            self._last_person_series = self._session.person_series()
            self._session = None
            return result

    def session_live(self) -> SessionLive | None:
        with self._lock:
            if self._session is None:
                return None
            return self._session.live(self._session_now())

    def session_result(self) -> EvaluationResult | None:
        """마지막으로 종료된 세션의 결과 (없으면 None)."""
        with self._lock:
            return self._last_result

    def session_person_series(self) -> dict:
        """객체별 d_i(t) 시계열 — 진행 중이면 현재까지, 아니면 마지막 세션 (v1.4)."""
        with self._lock:
            if self._session is not None:
                return self._session.person_series()
            return dict(self._last_person_series)

    def session_timeline(self) -> list[TimelinePoint]:
        """진행 중 세션의 타임라인, 없으면 마지막 세션의 타임라인."""
        with self._lock:
            if self._session is not None:
                return list(self._session.timeline)
            return list(self._last_timeline)

    # ------------------------------------------------------------ 지표 계산

    def _obj_kinematics(self, st: _ObjState):
        """(vx, vy, speed_mps, align) — sliding window 양끝점 기반.

        dt는 실제 경과 초(window 양끝 ts 차) — fps 불균일·드랍에 무관
        (webui/speed.py._speed 이식, 거리만 맵 px→m 환산).
        """
        t1, x1, y1 = st.hist[-1]
        vx = vy = 0.0
        speed: float | None = 0.0 if self._m_per_px is not None else None
        if len(st.hist) >= 2:
            t0, x0, y0 = st.hist[0]
            dt = t1 - t0
            dpx = math.hypot(x1 - x0, y1 - y0)
            if dpx > 1e-9:
                vx, vy = (x1 - x0) / dpx, (y1 - y0) / dpx
            if self._m_per_px is not None and dt > 0:
                dist_m = dpx * self._m_per_px
                speed = dist_m / dt if dist_m >= self.min_move_m else 0.0
        align: float | None = None
        if self._routes and (vx or vy):
            hit = min((nearest_on_polyline((x1, y1), r) for r in self._routes),
                      key=lambda h: h.dist_px)
            tx, ty = hit.tangent
            if tx or ty:
                align = vx * tx + vy * ty        # 최근접 구간 tangent와 cosine
        return vx, vy, speed, align

    def snapshot(self) -> MapState:
        """최신 맵 상태 스냅샷 (contracts.MapState) — API/SSE가 소비."""
        with self._lock:
            now = self._latest_ts if self._latest_ts is not None else time.time()
            self._purge(now)

            th = self._site.thresholds
            sess = self._session
            objects: list[MapObject] = []
            positions: list[tuple[float, float]] = []
            for st in self._objects.values():
                _, x, y = st.hist[-1]
                vx, vy, speed, align = self._obj_kinematics(st)
                # --- 객체별 부가 지표 (v1.5) — 전부 기계산 값의 노출/파생 ---
                dwell = max(0.0, st.last_ts - st.first_ts)
                zone_id = next((z.id for z, _a in self._zones
                                if point_in_polygon((x, y), z.polygon)), None)
                evac_ok = (speed is not None and align is not None
                           and speed >= th.v_th and align >= th.a_th) \
                    if (speed is not None or align is not None) else None
                epfi_live = dev_m = route_id = exited = None
                if sess is not None:
                    pa = sess.persons.get(st.gid)
                    if pa is not None:
                        dev_m = pa.last_d_m
                        route_id = pa.route_id
                        T = pa.last_ts - pa.first_ts
                        if T >= 2.0 and th.d_allow > 0 and pa.route_pts is not None:
                            epfi_live = max(0.0, 1.0 - pa.integral_dm
                                            / (T * th.d_allow)) * 100.0
                    exited = next((eid for eid, ec in self._exits.items()
                                   if st.gid in ec.counted_out
                                   or st.gid in ec.counted_in), None)
                objects.append(MapObject(
                    cam_id=st.cam_id, id=st.local_id, gid=st.gid,
                    x=x, y=y, vx=vx, vy=vy, speed_mps=speed, align=align,
                    in_bounds=st.in_bounds, conf=round(st.conf, 3),
                    dwell_sec=round(dwell, 1), zone_id=zone_id,
                    evac_ok=evac_ok, epfi_live=epfi_live, dev_m=dev_m,
                    route_id=route_id, exited=exited))
                positions.append((x, y))

            zones = []
            for z, area in self._zones:
                n = sum(1 for p in positions if point_in_polygon(p, z.polygon))
                density = (round(n / area, 3) if area and area > 0 else None)
                zones.append(ZoneState(id=z.id, count=n, density=density))

            bottlenecks = []
            for b, area in self._bottlenecks:
                n = sum(1 for p in positions if point_in_polygon(p, b.polygon))
                density = (round(n / area, 3) if area and area > 0 else None)
                over = density is not None and density > b.rho_crit
                bottlenecks.append(BottleneckState(
                    id=b.id, count=n, density=density, over=over))

            exits = [ExitState(id=eid, in_count=ec.in_count,
                               out_count=ec.out_count)
                     for eid, ec in self._exits.items()]

            return MapState(
                ts=now,
                site_version=self._site.version,
                objects=objects,
                zones=zones,
                bottlenecks=bottlenecks,
                exits=exits,
                cameras=[],   # 카메라 런타임 상태는 트랙 A/API 층이 병합
                session=(self._session.live(self._session_now())
                         if self._session is not None else None),
            )
