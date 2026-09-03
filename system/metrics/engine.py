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

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger("system.metrics.engine")

from system.config.schema import CameraConfig, SiteConfig
from system.contracts import (
    BottleneckState,
    EvaluationResult,
    ExitState,
    JourneySegment,
    MapObject,
    MapState,
    PersonJourney,
    SessionLive,
    TimelinePoint,
    TrackedObject,
    ZoneState,
)
from system.identity import GlobalIdService
from system.identity import get_settings as _gid_settings
from system.metrics.session import EvaluationSession
from system.spatial import (
    CameraProjector,
    DirectionalLine,
    ZoneGate,
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

    # 키 2단 (v1.13): line_key = 카메라 로컬 키 — 선분 부호 기억은 트랙 수명과
    # 일치해야 하므로 항상 f"{cam}:{local}". count_key = debounce·집계 키 —
    # 글로벌 ID 모드에선 gN(같은 사람 재통과 억제), 아니면 line_key 와 동일.
    # 반환: 이번 관측으로 **새로 집계된** 방향("in"/"out") — 여정의 출구 이벤트용.

    def observe_zone(self, line_key: str, count_key: str, pt, bbox) -> str | None:
        """화면 영역 게이트용 — bbox 를 함께 넘긴다. 집계·debounce 는 동일."""
        ev = self.line.observe(line_key, pt, bbox)
        if ev == "out" and count_key not in self.counted_out:
            self.counted_out.add(count_key)
            self.out_count += 1
            return "out"
        return None

    def observe(self, line_key: str, count_key: str,
                pt: tuple[float, float]) -> str | None:
        ev = self.line.observe(line_key, pt)
        if ev == "in" and count_key not in self.counted_in:
            self.counted_in.add(count_key)
            self.in_count += 1
            return "in"
        if ev == "out" and count_key not in self.counted_out:
            self.counted_out.add(count_key)
            self.out_count += 1
            return "out"
        return None


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
        self._cam_exits: dict[str, list[str]] = {}   # cam_id -> [exit_id] (화면 통과선)
        # 평가 세션 (계약 v1.2) — 진행 중 세션·마지막 결과·타임라인
        self._session: EvaluationSession | None = None
        self._recorder = None   # SessionRecorder | None (계약 v1.10 — 세션 녹화)
        self._last_result: EvaluationResult | None = None
        self._last_timeline: list[TimelinePoint] = []
        self._last_person_series: dict = {}
        self._debug_foot: dict = {}  # gid -> {foot_uv, map_xy} — debug용 임시
        # 글로벌 ID (v1.13) — 토글 on 일 때만 서비스가 만들어지고, off 면 전부 None/빈 값
        self._gid: GlobalIdService | None = None
        self._gid_used = False                 # 이번 세션이 글로벌 id 로 측정 중인가
        self._replay_gid = False               # 리플레이 gid_hint 를 본 적 있는가
        self._journeys: dict[str, list[dict]] = {}     # gid -> 카메라 구간 장부
        self._exit_events: dict[str, tuple[str, float]] = {}  # gid -> (exit_id, ts) 최초 out
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
            # 카메라별 min_conf 오버라이드 (raw) — None이면 사이트값 상속.
            # 매 프레임 dict lookup, fallback은 on_tracks에서 site.thresholds로.
            # reload마다 갱신되므로 site.thresholds.min_conf 변경도 즉시 반영.
            self._cam_min_conf = {cam.cam_id: cam.min_conf for cam in cameras}

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
            self._cam_exits = {}          # cam_id -> [exit_id] (화면 통과선)
            for ex in site.exits:
                # 화면 통과선이 설정된 출입구는 **화면 px 기하**로 카운터를 만든다.
                # 카운터는 출입구당 하나뿐이라 맵/화면이 동시에 세는 일은 없다.
                in_cam = ex.counts_in_camera()
                if ex.camera_zone_mode():          # 화면 **영역** (선보다 우선)
                    key = (tuple(map(tuple, ex.cam_zone)), ex.cam_zone_dwell, "zone")
                    line = ZoneGate(ex.cam_zone, dwell=ex.cam_zone_dwell)
                else:
                    geo_line = ex.cam_line if in_cam else ex.line
                    geo_inside = ex.cam_inside if in_cam else ex.inside
                    key = (tuple(map(tuple, geo_line)), tuple(geo_inside), in_cam)
                    # 화면 px 는 맵 px 보다 스케일이 작을 수 있어 데드밴드를 줄인다
                    line = DirectionalLine(
                        geo_line, geo_inside,
                        margin_px=(margin_px * 0.5 if in_cam else margin_px))
                st = _ExitCounter(line=line, cfg_key=key)
                if in_cam:
                    self._cam_exits.setdefault(ex.count_cam, []).append(ex.id)
                prev = old.get(ex.id)
                if prev is not None:
                    st.in_count, st.out_count = prev.in_count, prev.out_count
                    st.counted_in = prev.counted_in
                    st.counted_out = prev.counted_out
                    if prev.cfg_key == key:      # 선 기하 동일 → 부호상태 유지
                        st.line = prev.line
                self._exits[ex.id] = st

    def _cam_exit_ids(self) -> set:
        """화면 통과선으로 카운트하는 출입구 id 집합 (맵 관측에서 제외)."""
        return {eid for ids in self._cam_exits.values() for eid in ids}

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
                # 녹화는 raw 계약 유지 — 미매핑 카메라 프레임도 기록 (gid 없음)
                self._record(cam_id, ts, tracks, None)
                if sess is not None and tracks:
                    sess.note_dropped(len(tracks))   # quality: 투영 제외
                return
            if sess is not None:
                sess.cams_seen.add(cam_id)
            # 카메라별 오버라이드 우선, 없으면(None) 사이트 임계값 상속.
            # 필터는 호스트 엔진에서만 적용 — DS 워커는 conf 계산만 하고
            # launcher는 min_conf를 워커로 보내지 않는다 (경로 무관).
            cam_override = self._cam_min_conf.get(cam_id)
            min_conf = (cam_override if cam_override is not None
                        else self._site.thresholds.min_conf)
            # ---- 글로벌 ID (v1.13) — 토글 on(서비스) 또는 리플레이 힌트가 있을 때만
            # 동작. off 면 svc=None 이고 gid_eff 는 언제나 로컬 합성키(현행과 동일).
            gset = _gid_settings()
            svc = self._gid_service(gset) if gset["enabled"] else None
            rec_gids: list[str | None] = []      # 녹화용 확정 gid — tracks 와 1:1 정렬
            for tr in tracks:
                okey = f"{cam_id}:{tr.local_track_id}"  # 카메라 로컬 키 — 운동학·선분 기억
                gid_eff = okey                          # debounce·표시·인원 지표 키
                if tr.gid_hint:                  # 리플레이 — 녹화된 확정 id (결정성)
                    gid_eff = tr.gid_hint
                    self._replay_gid = True
                elif svc is not None:
                    gid_eff = svc.lookup(cam_id, tr.local_track_id) or okey
                if tr.conf < min_conf:           # 저신뢰 관측 — 오탐 연명 트랙 차단
                    rec_gids.append(None)        # (BYTE 저신뢰 연관 유령 객체 방지)
                    continue
                # 화면 통과선 — **투영 전에** 관측한다. 문 앞은 대응점 헐 밖이라
                # 아래 ROI 게이트에서 버려지는데, 카운트는 거기서도 살아야 한다.
                for _eid in self._cam_exits.get(cam_id, ()):
                    _ec = self._exits.get(_eid)
                    if _ec is None:
                        continue
                    if isinstance(_ec.line, ZoneGate):
                        # 문 앞은 발끝이 잘려 튄다 — bbox 도 함께 넘겨 겹침으로 판정
                        ev = _ec.observe_zone(okey, gid_eff, tr.foot_uv, tr.bbox_xyxy)
                    else:
                        ev = _ec.observe(okey, gid_eff, tr.foot_uv)
                    if ev == "out" and sess is not None:
                        self._exit_events.setdefault(gid_eff, (_eid, float(ts)))
                p = proj.project(tr.foot_uv)
                self._debug_foot[okey] = {
                    "foot_u": round(tr.foot_uv[0], 1), "foot_v": round(tr.foot_uv[1], 1),
                    "map_x": round(p.x, 1) if p else None, "map_y": round(p.y, 1) if p else None,
                }
                if p is None:                    # valid_roi 밖 — 표출·밀도·EPFI 에서 제외
                    # 단, **출입구 통과 판정**만은 헐 밖 관측도 쓴다(exit_extrap_m).
                    # 문은 대개 헐 경계 밖에 있어 일반 규칙대로면 영영 안 세진다.
                    # 외삽 오차를 감안해 출입구 선 근처 관측만, 맵 안(in_bounds)만.
                    self._observe_exits_extrap(cam_id, okey, gid_eff, ts, proj, tr.foot_uv)
                    self._drop(okey, forget_lines=not self._extrap_on())
                    if svc is not None:          # 헐 밖에서도 '지금 활성' 유지 (동시활성 기각용)
                        svc.touch(cam_id, tr.local_track_id, ts)
                    if sess is not None:
                        sess.note_dropped()
                    rec_gids.append(gid_eff if gid_eff != okey else None)
                    continue
                if svc is not None:              # 헐 안 관측만 특징으로 글로벌 id 확정/갱신
                    g = svc.resolve(cam_id, tr.local_track_id, tr.emb, ts)
                    if g is not None:
                        gid_eff = g
                st = self._objects.get(okey)     # 운동학 상태는 항상 카메라 로컬 키 —
                if st is None:                   # 이력이 카메라를 넘어 섞이지 않는다(v_th 보호)
                    st = self._objects[okey] = _ObjState(
                        cam_id=cam_id, local_id=int(tr.local_track_id), gid=gid_eff,
                        first_ts=ts)
                st.gid = gid_eff                 # 바인딩 승격(okey→gN) 반영 — 표시·debounce 키
                st.conf = tr.conf
                st.hist.append((ts, p.x, p.y))
                while len(st.hist) > 1 and ts - st.hist[0][0] > self.window_sec:
                    st.hist.popleft()            # sliding window 유지
                st.last_ts = ts
                st.in_bounds = p.in_bounds
                if sess is not None and gid_eff != okey:
                    self._gid_used = True        # 이번 세션은 글로벌 id 로 측정 중
                    self._journey_note(gid_eff, cam_id, ts, p.x, p.y)
                cam_owned = self._cam_exit_ids()
                for eid, ec in self._exits.items():   # 방향성 crossing 관측
                    if eid in cam_owned:              # 화면 통과선이 이미 셌다
                        continue
                    ev = ec.observe(okey, gid_eff, (p.x, p.y))
                    if ev == "out" and sess is not None:
                        self._exit_events.setdefault(gid_eff, (eid, float(ts)))
                if sess is not None:             # EPFI 관측 누적 (글로벌이면 사람 단위 병합)
                    sess.observe_point(gid_eff, ts, p.x, p.y)
                rec_gids.append(gid_eff if gid_eff != okey else None)
            # 녹화 — raw 계약 유지하되 이 프레임에서 **확정된** gid 를 함께 남긴다
            # (리플레이가 갤러리 없이 같은 id 를 재현, v1.13). 루프 뒤에 기록하는 이유:
            # 바인딩이 같은 프레임 안에서 일어나므로 기록 시점의 gid 가 실제 사용값이다.
            self._record(cam_id, ts, tracks, rec_gids)
            self._purge(self._latest_ts)
            if sess is not None:                 # 1초 샘플 (IDR·CBS·타임라인)
                sess.maybe_sample(self._latest_ts)

    # ------------------------------------------ 글로벌 ID (v1.13) 내부
    def _gid_service(self, gset: dict) -> GlobalIdService:
        """토글 on 일 때만 호출 — 서비스 지연 생성 + 파라미터 핫리로드."""
        s = self._gid
        if s is None:
            s = self._gid = GlobalIdService(
                ttl_sec=float(gset["ttl_sec"]), cos_th=float(gset["cos_th"]),
                update_every=int(gset["update_every"]),
                min_new_obs=int(gset.get("min_new_obs", 3)))
        else:
            s.ttl_sec = float(gset["ttl_sec"])
            s.cos_th = float(gset["cos_th"])
            s.update_every = int(gset["update_every"])
            s.min_new_obs = int(gset.get("min_new_obs", 3))
        return s

    def _record(self, cam_id: str, ts: float, tracks, gids) -> None:
        """세션 녹화 (계약 v1.10) — min_conf 필터 이전 raw + 확정 gid(v1.13)."""
        if self._recorder is None or not tracks:
            return
        try:
            self._recorder.record(cam_id, ts, tracks, gids=gids)
        except Exception:                       # 녹화 실패가 라이브를 죽이지 않게
            logger.exception("세션 녹화 실패 — 녹화 중단, 라이브 계속")
            self._recorder = None

    def _journey_note(self, gid: str, cam_id: str, ts: float, x: float, y: float) -> None:
        """여정 장부 — 같은 카메라 연속 관측은 구간 연장, 아니면 새 구간(갭은 조립 때 브리지)."""
        segs = self._journeys.setdefault(gid, [])
        s = segs[-1] if segs else None
        if s is not None and s["cam"] == cam_id and ts - s["t1"] <= self.lost_timeout_sec:
            s["dist_px"] += math.hypot(x - s["x1"], y - s["y1"])
            s["t1"], s["x1"], s["y1"] = ts, x, y
            s["n"] += 1
        else:
            segs.append({"cam": cam_id, "t0": ts, "t1": ts, "x0": x, "y0": y,
                         "x1": x, "y1": y, "dist_px": 0.0, "n": 1})

    def _build_journeys(self) -> list[PersonJourney]:
        """세션 종료 시 여정 조립 — 구간 거리 합 + 구간 사이 직선 브리지.

        갭 평균속도가 유효한 이유: 전 카메라 시간 동기 + 동일 맵 좌표계(사용자 확정
        설계). id 유실로 여정이 조각나면 coverage 로 드러난다 — 숨기지 않는다."""
        out: list[PersonJourney] = []
        mpp = self._m_per_px
        for gid, segs in self._journeys.items():
            if not segs:
                continue
            first, last = segs[0], segs[-1]
            dist_px = sum(s["dist_px"] for s in segs)
            for a, b in zip(segs, segs[1:]):
                dist_px += math.hypot(b["x0"] - a["x1"], b["y0"] - a["y1"])
            dur = max(0.0, last["t1"] - first["t0"])
            obs = sum(s["t1"] - s["t0"] for s in segs)
            ex = self._exit_events.get(gid)
            zone = next((z.id for z, _a in self._zones
                         if point_in_polygon((first["x0"], first["y0"]), z.polygon)), None)
            dist_m = dist_px * mpp if mpp else None
            out.append(PersonJourney(
                gid=gid, start_ts=first["t0"], end_ts=last["t1"],
                start_xy=(round(first["x0"], 1), round(first["y0"], 1)),
                end_xy=(round(last["x1"], 1), round(last["y1"], 1)),
                start_zone=zone,
                exit_id=(ex[0] if ex else None),
                exit_ts=(ex[1] if ex else None),
                total_dist_m=(round(dist_m, 1) if dist_m is not None else None),
                duration_sec=round(dur, 1),
                avg_speed_mps=(round(dist_m / dur, 2)
                               if dist_m is not None and dur > 0 else None),
                coverage_ratio=round(min(1.0, obs / dur), 3) if dur > 0 else 1.0,
                segments=[JourneySegment(
                    cam_id=s["cam"], t0=round(s["t0"], 2), t1=round(s["t1"], 2),
                    dist_m=(round(s["dist_px"] * mpp, 1) if mpp else None),
                    n_points=s["n"]) for s in segs]))
        out.sort(key=lambda j: j.start_ts)
        return out

    def _extrap_on(self) -> bool:
        return bool(self._m_per_px) and float(getattr(self._site.thresholds, "exit_extrap_m", 0.0) or 0.0) > 0

    def _observe_exits_extrap(self, cam_id: str, line_key: str, count_key: str,
                              ts: float, proj, foot_uv) -> None:
        """헐 밖 관측을 출입구 맵 통과선 판정에 쓴다 — **헐 경계에서 exit_extrap_m 안**일 때만.

        외삽 오차는 헐에서 멀어질수록 커진다(렌즈 왜곡·대응점 잡음 증폭). 그래서 기준은
        "출입구 선에 가깝다"가 아니라 "매핑된 바닥(헐)에서 가깝다"다 — 문이 헐 바로 밖
        1~2m 에 있는 전형적 경우만 허용하고, 멀리서 찍힌 사람이 엉뚱한 선을 넘는 건 막는다.
        """
        if not self._extrap_on():
            return
        p = proj.project_raw(foot_uv)
        if not p.in_bounds:
            return
        r_px = float(self._site.thresholds.exit_extrap_m) / self._m_per_px
        cache = getattr(self, "_roi_map_poly", None)
        if cache is None:
            cache = self._roi_map_poly = {}
        poly = cache.get(cam_id)
        if poly is None or cache.get(("_proj", cam_id)) is not proj:
            poly = cache[cam_id] = proj.roi_map_polygon()
            cache[("_proj", cam_id)] = proj      # 투영기가 재구성되면 다각형도 다시
        if _dist_to_polygon(p.x, p.y, poly) > r_px:
            return
        cam_owned = self._cam_exit_ids()
        for eid, ec in self._exits.items():
            if eid in cam_owned or not hasattr(ec.line, "A"):
                continue                          # 화면 게이트는 이미 투영 전에 관측했다
            ev = ec.observe(line_key, count_key, (p.x, p.y))
            if ev == "out" and self._session is not None:
                self._exit_events.setdefault(count_key, (eid, float(ts)))

    def _drop(self, gid: str, forget_lines: bool = True) -> None:
        """맵 투영에서 빠진 객체 정리.

        화면 통과선(cam_line)은 **일부러 헐 밖에서 쓰는 것**이라 여기서 부호
        기억을 지우면 안 된다. 지우면 문 앞으로 나가는 매 프레임마다 상태가
        초기화돼 crossing 이 영영 성립하지 않는다(실측으로 확인).
        맵 통과선은 forget_lines=True 일 때만 정리 — 헐 밖 외삽 판정(exit_extrap_m)을
        쓰는 동안은 "헐 안 → 헐 밖" 한 걸음 사이에 부호가 지워지면 crossing 이 안 잡히므로
        유지하고, 객체가 소실(lost_timeout)될 때 지운다.
        """
        self._objects.pop(gid, None)
        if not forget_lines:
            return
        cam_owned = self._cam_exit_ids()
        for eid, ec in self._exits.items():
            if eid in cam_owned:
                continue
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
        # 카운터 재생성 — reload() 와 같은 규칙을 써야 한다. 화면 통과선 설정을
        # 여기서 빠뜨리면 세션 시작·리셋 때 조용히 맵 카운트로 되돌아간다.
        self._exits = {}
        self._cam_exits = {}
        for ex in self._site.exits:
            in_cam = ex.counts_in_camera()
            if ex.camera_zone_mode():
                gate = ZoneGate(ex.cam_zone, dwell=ex.cam_zone_dwell)
                key = (tuple(map(tuple, ex.cam_zone)), ex.cam_zone_dwell, "zone")
            else:
                geo_line = ex.cam_line if in_cam else ex.line
                geo_inside = ex.cam_inside if in_cam else ex.inside
                gate = DirectionalLine(
                    geo_line, geo_inside,
                    margin_px=(margin_px * 0.5 if in_cam else margin_px))
                key = (tuple(map(tuple, geo_line)), tuple(geo_inside), in_cam)
            self._exits[ex.id] = _ExitCounter(line=gate, cfg_key=key)
            if in_cam:
                self._cam_exits.setdefault(ex.count_cam, []).append(ex.id)

    def start_session(self,
                      origin_xy: tuple[float, float] | None = None,
                      t_alarm: float | None = None,
                      alarm_origins: list[tuple[float, float]] | None = None,
                      ) -> SessionLive:
        """평가 세션 시작 — 내부 카운터·debounce·객체이력 reset 후 누적 개시.
        site version을 calibration/config_version으로 기록.
        진행 중이면 RuntimeError (API 층이 409로 매핑).

        alarm_origins 우선. 없으면 site.alarm_origins 사용.
        둘 다 없으면 origin_xy를 단일 origin으로 사용 (하위 호환).
        """
        with self._lock:
            if self._session is not None:
                raise RuntimeError("평가 세션이 이미 진행 중")
            if t_alarm is None:
                t_alarm = time.time()
            # 경보 발생원 결정: 인자 > site 설정 > origin_xy 폴백
            origins: list[tuple[float, float]]
            if alarm_origins:
                origins = alarm_origins
            elif self._site.alarm_origins:
                origins = [(ao.xy[0], ao.xy[1]) for ao in self._site.alarm_origins]
            elif origin_xy is not None:
                origins = [origin_xy]
            else:
                origins = [(0.0, 0.0)]
            # 경보가 울린 순간 사람들이 어디 있었나 — IDR 거리 D 의 기준(§4.1).
            # _reset_locked() 가 객체 이력을 지우므로 그 전에 붙잡아 둔다.
            at_alarm = [(st.hist[-1][1], st.hist[-1][2])
                        for st in self._objects.values() if st.hist]
            self._reset_locked()
            # 글로벌 ID (v1.13) — 세션마다 id 공간을 g1부터 새로 (리포트 가독성·독립성)
            self._gid_used = False
            self._replay_gid = False
            self._journeys = {}
            self._exit_events = {}
            if self._gid is not None:
                self._gid.reset()
            self._session = EvaluationSession(self, origins, float(t_alarm),
                                              occupants_px=at_alarm)
            return self._session.live(float(t_alarm))

    def attach_recorder(self, recorder) -> None:
        """세션 녹화기 부착 (계약 v1.10) — on_tracks가 raw 입력을 기록한다."""
        with self._lock:
            self._recorder = recorder

    def detach_recorder(self):
        """녹화기 분리 후 반환 (API가 close 담당). 없으면 None."""
        with self._lock:
            rec, self._recorder = self._recorder, None
            return rec

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
            if self._gid_used or self._replay_gid:   # 글로벌 ID 측정분 반영 (v1.13)
                result = result.model_copy(update={
                    "global_id": True, "journeys": self._build_journeys()})
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

            # 세션 중이면 병목별 CBS 진행값도 실어준다 (v1.12 — 그룹/선택 합산)
            cbs_live = (self._session.cbs_by_bottleneck()
                        if self._session is not None else {})
            bottlenecks = []
            for b, area in self._bottlenecks:
                n = sum(1 for p in positions if point_in_polygon(p, b.polygon))
                density = (round(n / area, 3) if area and area > 0 else None)
                over = density is not None and density > b.rho_crit
                cbs = cbs_live.get(b.id)
                bottlenecks.append(BottleneckState(
                    id=b.id, count=n, density=density, over=over,
                    cbs=(round(cbs, 3) if cbs is not None else None)))

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


def _dist_to_segment(x: float, y: float, A, B) -> float:
    """점 (x,y) 와 선분 AB(맵 px) 사이 거리."""
    ax, ay = float(A[0]), float(A[1])
    bx, by = float(B[0]), float(B[1])
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L2))
    px, py = ax + t * dx, ay + t * dy
    return float(((x - px) ** 2 + (y - py) ** 2) ** 0.5)


def _dist_to_polygon(x: float, y: float, poly) -> float:
    """점과 다각형(맵 px) 사이 거리 — 안이면 0."""
    if len(poly) >= 3 and point_in_polygon((x, y), poly):
        return 0.0
    n = len(poly)
    return min(_dist_to_segment(x, y, poly[i], poly[(i + 1) % n]) for i in range(n)) if n >= 2 else float("inf")
