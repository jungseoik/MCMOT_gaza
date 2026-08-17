"""M6 개발용 mock 서버 — CONTRACT §4 REST API 전체를 동일 계약으로 구현.

실서버(메인 세션 소유)가 나중에 같은 경로·페이로드로 대체한다.
- 설정 영속화: SiteStore → data/sites/mock/ (실제 JSON — 새로고침 복원 UX 확인용)
- 카메라 test/snapshot: 더미 이미지(회색 캔버스 + 카메라명)
- /api/map/stream: SSE 1초 — 가짜 객체 20~40개가 등록된 Route를 따라 이동
  (경로 없으면 랜덤 워크), zones/bottlenecks/exits 상태 규칙 갱신

실행:
    conda run -n boosttrack uvicorn system.api.mock_server:app --port 8901

프론트(webui/static/main/)도 같은 포트에서 서빙한다 → http://localhost:8901/
"""
from __future__ import annotations

import asyncio
import base64
import csv
import heapq
import io
import json
import math
import random
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from system.config.schema import (
    CameraConfig,
    CameraMapping,
    MapScale,
    MapSpec,
    Point,
    SiteConfig,
)
from system.config.store import SiteStore
from system.contracts import (
    BottleneckMetric,
    BottleneckState,
    CameraState,
    EvaluationResult,
    ExitMetric,
    ExitState,
    MapObject,
    MapState,
    PersonMetric,
    SessionLive,
    TimelinePoint,
    ZoneMetric,
    ZoneState,
)

ROOT = Path(__file__).resolve().parents[2]
SITE_ID = "mock"
store = SiteStore(ROOT / "data" / "sites")

app = FastAPI(title="MACS-EVAC mock server (M6)")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ================================================================ 더미 이미지

_FONT_PATH = ROOT / "webui" / "static" / "fonts" / "Pretendard-Bold.ttf"


def _dummy_frame(cam: CameraConfig, w: int = 640, h: int = 360) -> np.ndarray:
    """회색 캔버스 + 카메라명 텍스트 (BGR). 카메라별로 톤을 살짝 달리한다."""
    idx = int("".join(c for c in cam.cam_id if c.isdigit()) or 0)
    base = 70 + (idx * 23) % 60
    img = Image.new("RGB", (w, h), (base, base, base + 8))
    d = ImageDraw.Draw(img)
    # 격자 (프레임 느낌)
    for gx in range(0, w, 80):
        d.line([(gx, 0), (gx, h)], fill=(base + 14, base + 14, base + 20))
    for gy in range(0, h, 80):
        d.line([(0, gy), (w, gy)], fill=(base + 14, base + 14, base + 20))
    try:
        f_big = ImageFont.truetype(str(_FONT_PATH), 34)
        f_sm = ImageFont.truetype(str(_FONT_PATH), 18)
    except OSError:
        f_big = f_sm = ImageFont.load_default()
    label = f"{cam.name or cam.cam_id}"
    d.text((w / 2, h / 2 - 14), label, font=f_big, fill=(240, 240, 240), anchor="mm")
    d.text((w / 2, h / 2 + 22), f"{cam.cam_id} · MOCK {w}x{h}",
           font=f_sm, fill=(180, 200, 210), anchor="mm")
    d.text((10, h - 26), time.strftime("%Y-%m-%d %H:%M:%S"),
           font=f_sm, fill=(150, 160, 165))
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def _jpeg(frame: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise HTTPException(500, "jpeg encode 실패")
    return buf.tobytes()


# ================================================================ 지오메트리


def _point_in_poly(x: float, y: float, poly: list[Point]) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _poly_area_px(poly: list[Point]) -> float:
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _side(p: Point, a: Point, b: Point) -> float:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _seg_intersect(p1: Point, p2: Point, a: Point, b: Point) -> bool:
    d1, d2 = _side(p1, a, b), _side(p2, a, b)
    d3, d4 = _side(a, p1, p2), _side(b, p1, p2)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def _centroid(poly: list[Point]) -> tuple[float, float]:
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    return cx, cy


# ==================================================== 평가 세션 시뮬레이터 (v1.2)


class SessionSim:
    """4대 지표 세션 mock — 세션 중 그럴듯한 지표 시계열을 산출한다.

    - SEI : 출구 방향성 통과 분포 vs 설계 분포 수식(+노이즈, 55~98 클램프).
            통과 0명이거나 초기 4초는 None(=insufficient_data).
    - CBS : 병목 밀도(실측; 축척 없으면 임계 주변을 진동하는 합성 밀도)로
            임계초과분을 1초 적분 — 단조 누적 증가.
    - EPFI: 70~100 random walk.
    - IDR : 경보위치→구역 (공간그래프 있으면 그래프, 없으면 직선) 거리 기반
            시간차 스케줄로 not_started → started 전환.

    모든 호출은 Simulator.lock 안에서 이뤄진다 (엔드포인트도 sim.lock 사용).
    """

    WALK_MPS = 1.2  # 구역 개시 지연 가정 보행속도

    def __init__(self):
        self.live: SessionLive | None = None
        self.result: EvaluationResult | None = None
        self.timeline: list[TimelinePoint] = []
        self._zone_sched: dict[str, dict] = {}   # zone_id → {dist, delay, started_at}
        self._bn: dict[str, dict] = {}           # bn_id → {peak, over_sec, cbs, phase}
        self._epfi = 85.0
        self._sei_noise = 0.0
        self._site_version = 0

    # ------------------------------------------------------------ 거리
    @staticmethod
    def _m_per_px(site: SiteConfig | None) -> float:
        if site and site.map:
            try:
                v = site.map.resolve_m_per_px()
                if v:
                    return v
            except ValueError:
                pass
        return 1.0 / 12.0                        # 축척 없으면 12px≈1m (sim과 동일)

    @staticmethod
    def _graph_dist_px(site: SiteConfig | None, a_xy, b_xy) -> float:
        """공간그래프 최단거리(px). 그래프 없거나 미연결이면 직선 폴백."""
        g = site.graph if site else None
        if not g or not g.nodes or not g.edges:
            return math.dist(a_xy, b_xy)
        pos = {n.id: n.xy for n in g.nodes}
        nearest = lambda p: min(pos, key=lambda nid: math.dist(pos[nid], p))  # noqa: E731
        sa, sb = nearest(a_xy), nearest(b_xy)
        adj: dict[str, list[tuple[str, float]]] = {}
        for u, v in g.edges:
            if u in pos and v in pos:
                w = math.dist(pos[u], pos[v])
                adj.setdefault(u, []).append((v, w))
                adj.setdefault(v, []).append((u, w))
        dist = {sa: 0.0}
        pq: list[tuple[float, str]] = [(0.0, sa)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, 1e18):
                continue
            if u == sb:
                break
            for v, w in adj.get(u, []):
                nd = d + w
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        gd = dist.get(sb)
        if gd is None:                            # 그래프 미연결
            return math.dist(a_xy, b_xy)
        return math.dist(a_xy, pos[sa]) + gd + math.dist(pos[sb], b_xy)

    # ------------------------------------------------------------ 시작/종료
    def start(self, origin, t_alarm: float, site: SiteConfig | None,
              sim_obj: "Simulator") -> SessionLive:
        m_per_px = self._m_per_px(site)
        self.result = None
        self.timeline = []
        self._epfi = random.uniform(80.0, 90.0)
        self._sei_noise = 0.0
        self._site_version = site.version if site else 0
        sim_obj.exit_counts.clear()               # 계약: start 시 카운터·debounce reset
        self._zone_sched = {}
        for z in (site.zones if site else []):
            c = _centroid(z.polygon)
            d_m = self._graph_dist_px(site, origin, c) * m_per_px
            delay = max(1.5, d_m / self.WALK_MPS + random.gauss(3.0, 1.5))
            self._zone_sched[z.id] = {"dist": round(d_m, 1), "delay": delay,
                                      "started_at": None}
        self._bn = {b.id: {"peak": 0.0, "over_sec": 0.0, "cbs": 0.0,
                           "phase": random.uniform(0.0, 6.28)}
                    for b in (site.bottlenecks if site else [])}
        self.live = SessionLive(
            session_id="sess-" + time.strftime("%Y%m%d-%H%M%S"),
            alarm_ts=t_alarm,
            alarm_origin=(float(origin[0]), float(origin[1])),
            zones_total=len(self._zone_sched))
        return self.live

    def _sei_calc(self, site: SiteConfig | None, state: MapState,
                  elapsed: float) -> float | None:
        exits = site.exits if site else []
        if not exits or elapsed < 4.0:
            return None
        actual = {e.id: 0 for e in exits}
        for es in state.exits:
            if es.id in actual:
                actual[es.id] = es.out_count
        tot = sum(actual.values())
        if tot <= 0:
            return None                           # insufficient_data
        caps = {e.id: e.design_capacity for e in exits}
        csum = sum(c or 0 for c in caps.values())
        if csum > 0:
            design = {k: (c or 0) / csum for k, c in caps.items()}
        else:                                     # 설계 미입력 → 균등 가정
            design = {e.id: 1.0 / len(exits) for e in exits}
        diff = sum(abs(actual[k] / tot - design[k]) for k in actual)
        raw = (1.0 - 0.5 * diff) * 100.0
        self._sei_noise = max(-6.0, min(6.0, self._sei_noise + random.gauss(0, 1.0)))
        return round(min(98.0, max(55.0, raw + self._sei_noise)), 1)

    def tick(self, now: float, site: SiteConfig | None,
             state: MapState) -> SessionLive | None:
        """1초 틱 (sim.lock 내부) — live 갱신 + TimelinePoint 누적."""
        if not self.live:
            return None
        lv = self.live
        elapsed = max(0.0, now - lv.alarm_ts)
        lv.elapsed_sec = round(elapsed, 1)
        # ---- IDR: 시간차 개시
        started = 0
        for sc in self._zone_sched.values():
            if sc["started_at"] is None and elapsed >= sc["delay"]:
                sc["started_at"] = lv.alarm_ts + sc["delay"]
            if sc["started_at"] is not None:
                started += 1
        lv.zones_started = started
        # ---- CBS: 임계초과 적분 (누적 증가)
        st_dens = {b.id: b.density for b in state.bottlenecks}
        rho = {b.id: (b.rho_crit, b.weight)
               for b in (site.bottlenecks if site else [])}
        dens_now: dict[str, float] = {}
        cbs_total = 0.0
        for bid, acc in self._bn.items():
            rc, w = rho.get(bid, (2.0, 1.0))
            d = st_dens.get(bid)
            if d is None:                         # 축척 없음 → 합성 밀도(임계 주변 진동)
                d = rc * (0.65 + 0.55 * abs(math.sin(elapsed / 13.0 + acc["phase"])))
            d = round(float(d), 2)
            dens_now[bid] = d
            acc["peak"] = max(acc["peak"], d)
            if d > rc:
                acc["over_sec"] += 1.0
                acc["cbs"] += (d - rc) * w        # dt = 1s
            cbs_total += acc["cbs"]
        lv.cbs_total = round(cbs_total, 2)
        # ---- EPFI: 70~100 random walk
        self._epfi = min(100.0, max(70.0, self._epfi + random.gauss(0, 1.2)))
        lv.epfi_avg = round(self._epfi, 1) if elapsed >= 2.0 else None
        # ---- SEI
        lv.sei = self._sei_calc(site, state, elapsed)
        # ---- 타임라인 1초 누적
        self.timeline.append(TimelinePoint(
            ts=now, sei=lv.sei, cbs_total=lv.cbs_total, epfi_avg=lv.epfi_avg,
            zones_started=started,
            exit_counts={e.id: e.out_count for e in state.exits},
            bottleneck_density=dens_now))
        return lv

    def stop(self, site: SiteConfig | None, sim_obj: "Simulator",
             now: float) -> EvaluationResult:
        lv = self.live
        assert lv is not None
        d_allow = site.thresholds.d_allow if site else 2.0
        elapsed = max(1.0, now - lv.alarm_ts)
        # ---- zone_metrics (IDR)
        zone_metrics = []
        for zid, sc in self._zone_sched.items():
            if sc["started_at"] is not None:
                delay = sc["started_at"] - lv.alarm_ts
                zone_metrics.append(ZoneMetric(
                    zone_id=zid, evacuation_start_at=sc["started_at"],
                    response_delay_sec=round(delay, 1),
                    graph_distance=sc["dist"],
                    idr=round(sc["dist"] / max(delay, 1e-6), 3),
                    participant_ratio=round(random.uniform(0.55, 0.95), 2),
                    status="started"))
            else:
                zone_metrics.append(ZoneMetric(
                    zone_id=zid, graph_distance=sc["dist"],
                    participant_ratio=round(random.uniform(0.0, 0.3), 2),
                    status="not_started"))
        # ---- person_metrics (EPFI)
        person_metrics = []
        for o in sim_obj.objs:
            e = min(100.0, max(0.0, random.gauss(self._epfi, 9.0)))
            mean_dev = round(max(0.05,
                                 d_allow * (1 - e / 100.0) * random.uniform(0.8, 1.2)), 2)
            person_metrics.append(PersonMetric(
                global_track_id=f"{o.cam_id}:{o.oid}",
                assigned_route_id=o.route_id,
                duration_sec=round(elapsed * random.uniform(0.6, 1.0), 1),
                mean_deviation_m=mean_dev,
                max_deviation_m=round(mean_dev * random.uniform(1.4, 2.6), 2),
                epfi=round(e, 1)))
        epfi_avg = (round(sum(p.epfi for p in person_metrics) / len(person_metrics), 1)
                    if person_metrics else None)
        # ---- bottleneck_metrics (CBS)
        bn_metrics = []
        for bid, acc in self._bn.items():
            cbs = round(acc["cbs"], 2)
            risk = "low" if cbs < 5 else ("mid" if cbs < 20 else "high")
            bn_metrics.append(BottleneckMetric(
                bottleneck_id=bid, peak_density=round(acc["peak"], 2),
                over_threshold_sec=acc["over_sec"], cbs=cbs, risk_level=risk))
        # ---- exit_metrics (SEI)
        exits = site.exits if site else []
        actual = {e.id: sim_obj.exit_counts.get(e.id, [0, 0])[1] for e in exits}
        tot = sum(actual.values())
        csum = sum(e.design_capacity or 0 for e in exits)
        exit_metrics = [ExitMetric(
            exit_id=e.id, actual_count=actual[e.id],
            design_capacity=e.design_capacity,
            actual_share=round(actual[e.id] / tot, 3) if tot > 0 else None,
            design_share=(round((e.design_capacity or 0) / csum, 3)
                          if csum > 0 else None)) for e in exits]
        self.result = EvaluationResult(
            session_id=lv.session_id,
            calibration_version=self._site_version,
            config_version=self._site_version,
            alarm_ts=lv.alarm_ts, alarm_origin=lv.alarm_origin, ended_at=now,
            zone_metrics=zone_metrics, person_metrics=person_metrics,
            bottleneck_metrics=bn_metrics, exit_metrics=exit_metrics,
            sei=lv.sei, epfi_avg=epfi_avg, cbs_total=lv.cbs_total,
            quality={"camera_coverage": 1.0, "unmapped_point_ratio": 0.02,
                     "global_id_confidence": 0.9, "missing_interval_sec": 0.0,
                     "warnings": ["mock 시뮬레이션 데이터 — 실측 아님"]},
            generated_at=now)
        self.live = None
        return self.result


# ================================================================ 시뮬레이터


class _SimObj:
    __slots__ = ("oid", "cam_id", "route_id", "dist", "speed", "x", "y",
                 "px", "py", "vx", "vy")

    def __init__(self, oid: int):
        self.oid = oid
        self.cam_id = "cam01"
        self.route_id: str | None = None
        self.dist = 0.0          # 경로 시작점부터 누적 px
        self.speed = max(0.4, random.gauss(1.2, 0.35))  # m/s
        self.x = self.y = 0.0
        self.px = self.py = 0.0  # 직전 위치 (통과선 판정)
        self.vx, self.vy = 1.0, 0.0


class Simulator:
    """가짜 객체 시뮬레이션 + MapState 스냅샷. 1초 틱."""

    def __init__(self):
        self.lock = threading.Lock()
        self.n = random.randint(20, 40)
        self.objs = [_SimObj(i + 1) for i in range(self.n)]
        self.exit_counts: dict[str, list[int]] = {}     # id -> [in, out]
        self.drops: dict[str, int] = {}
        self._spawned = False
        self.state = MapState(ts=time.time())

    # ------------------------------------------------------------ 배치
    def _bounds(self, site: SiteConfig | None) -> tuple[float, float]:
        if site and site.map:
            return float(site.map.w), float(site.map.h)
        return 1000.0, 600.0

    def _spawn(self, site: SiteConfig | None, cams: list[CameraConfig]):
        w, h = self._bounds(site)
        routes = site.routes if site else []
        cam_ids = [c.cam_id for c in cams if c.enabled] or ["cam01"]
        for i, o in enumerate(self.objs):
            o.cam_id = cam_ids[i % len(cam_ids)]
            if routes:
                r = routes[i % len(routes)]
                o.route_id = r.id
                total = self._route_len(r.points)
                o.dist = random.uniform(0, total)
            else:
                o.route_id = None
                o.x, o.y = random.uniform(0, w), random.uniform(0, h)
                a = random.uniform(0, 2 * math.pi)
                o.vx, o.vy = math.cos(a), math.sin(a)
        self._spawned = True

    @staticmethod
    def _route_len(pts: list[Point]) -> float:
        return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))

    @staticmethod
    def _route_pos(pts: list[Point], dist: float) -> tuple[float, float, float, float]:
        """경로 누적거리 dist의 (x, y, 단위방향 dx, dy)."""
        acc = 0.0
        for i in range(len(pts) - 1):
            seg = math.dist(pts[i], pts[i + 1])
            if seg <= 1e-9:
                continue
            if acc + seg >= dist:
                t = (dist - acc) / seg
                x = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t
                y = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t
                dx = (pts[i + 1][0] - pts[i][0]) / seg
                dy = (pts[i + 1][1] - pts[i][1]) / seg
                return x, y, dx, dy
            acc += seg
        dx = pts[-1][0] - pts[-2][0]
        dy = pts[-1][1] - pts[-2][1]
        n = math.hypot(dx, dy) or 1.0
        return pts[-1][0], pts[-1][1], dx / n, dy / n

    # ------------------------------------------------------------ 틱
    def tick(self):
        site = store.load_site(SITE_ID)
        cams = store.list_cameras(SITE_ID)
        with self.lock:
            self._tick_locked(site, cams)

    def _tick_locked(self, site: SiteConfig | None, cams: list[CameraConfig]):
        now = time.time()
        w, h = self._bounds(site)
        routes = {r.id: r for r in (site.routes if site else [])}
        m_per_px = None
        if site and site.map:
            try:
                m_per_px = site.map.resolve_m_per_px()
            except ValueError:
                m_per_px = None
        px_per_m = (1.0 / m_per_px) if m_per_px else 12.0  # 축척 없으면 12px≈1m 가정

        if not self._spawned or (routes and any(o.route_id not in routes and
                                                o.route_id is not None
                                                for o in self.objs)):
            self._spawn(site, cams)
        # 경로가 새로 생겼으면 재배치
        if routes and all(o.route_id is None for o in self.objs):
            self._spawn(site, cams)

        cam_ids = [c.cam_id for c in cams if c.enabled] or ["cam01"]
        objects: list[MapObject] = []
        for i, o in enumerate(self.objs):
            o.cam_id = cam_ids[i % len(cam_ids)]
            o.px, o.py = o.x, o.y
            if o.route_id and o.route_id in routes:
                pts = routes[o.route_id].points
                total = self._route_len(pts)
                o.dist += o.speed * px_per_m * 1.0          # dt=1s
                if o.dist >= total:                          # 끝 → 처음부터
                    o.dist = 0.0
                    o.x, o.y, o.vx, o.vy = self._route_pos(pts, 0.0)
                    o.px, o.py = o.x, o.y                   # 순간이동은 통과선 미판정
                else:
                    o.x, o.y, o.vx, o.vy = self._route_pos(pts, o.dist)
                # 경로 수직 방향 흔들림
                o.x += o.vy * random.uniform(-4, 4)
                o.y += -o.vx * random.uniform(-4, 4)
                align = max(-1.0, min(1.0, random.gauss(0.93, 0.05)))
            else:
                a = math.atan2(o.vy, o.vx) + random.uniform(-0.5, 0.5)
                o.vx, o.vy = math.cos(a), math.sin(a)
                o.x += o.vx * o.speed * px_per_m
                o.y += o.vy * o.speed * px_per_m
                if o.x < 0 or o.x > w:
                    o.vx = -o.vx
                    o.x = min(max(o.x, 0), w)
                if o.y < 0 or o.y > h:
                    o.vy = -o.vy
                    o.y = min(max(o.y, 0), h)
                align = None
            objects.append(MapObject(
                cam_id=o.cam_id, id=o.oid, gid=f"{o.cam_id}:{o.oid}",
                x=round(o.x, 1), y=round(o.y, 1),
                vx=round(o.vx, 3), vy=round(o.vy, 3),
                speed_mps=round(o.speed, 2), align=align,
            ))

        # ---- zones / bottlenecks (점-폴리곤 포함 + 밀도)
        zones = []
        for z in (site.zones if site else []):
            cnt = sum(1 for o in self.objs if _point_in_poly(o.x, o.y, z.polygon))
            dens = None
            if m_per_px:
                area = _poly_area_px(z.polygon) * m_per_px ** 2
                dens = round(cnt / area, 2) if area > 0 else None
            zones.append(ZoneState(id=z.id, count=cnt, density=dens))

        bottlenecks = []
        for b in (site.bottlenecks if site else []):
            cnt = sum(1 for o in self.objs if _point_in_poly(o.x, o.y, b.polygon))
            dens = None
            over = False
            if m_per_px:
                area = _poly_area_px(b.polygon) * m_per_px ** 2
                if area > 0:
                    dens = round(cnt / area, 2)
                    over = dens > b.rho_crit
            else:                       # 축척 없으면 인원수 기반 폴백
                over = cnt > 5
            bottlenecks.append(BottleneckState(id=b.id, count=cnt,
                                               density=dens, over=over))

        # ---- exits (방향성 통과선: prev→cur 세그먼트 교차)
        exits = []
        for e in (site.exits if site else []):
            cnts = self.exit_counts.setdefault(e.id, [0, 0])
            a, bb = e.line
            inside_sign = _side(e.inside, a, bb)
            for o in self.objs:
                if (o.px, o.py) == (o.x, o.y):
                    continue
                if _seg_intersect((o.px, o.py), (o.x, o.y), a, bb):
                    to_sign = _side((o.x, o.y), a, bb)
                    if inside_sign != 0 and to_sign * inside_sign > 0:
                        cnts[0] += 1        # 안쪽으로 → IN
                    else:
                        cnts[1] += 1        # 바깥으로 → OUT
            exits.append(ExitState(id=e.id, in_count=cnts[0], out_count=cnts[1]))

        # ---- cameras
        cam_states = []
        for c in cams:
            if c.enabled:
                self.drops[c.cam_id] = self.drops.get(c.cam_id, 0) + \
                    (1 if random.random() < 0.05 else 0)
                cam_states.append(CameraState(
                    cam_id=c.cam_id, status="running",
                    fps_in=round(c.analyze_fps + random.uniform(-0.3, 0.3), 1),
                    last_frame_ts=now, drops=self.drops.get(c.cam_id, 0)))
            else:
                cam_states.append(CameraState(cam_id=c.cam_id, status="disabled"))

        self.state = MapState(
            ts=now, site_version=site.version if site else 0,
            objects=objects, zones=zones, bottlenecks=bottlenecks,
            exits=exits, cameras=cam_states,
        )
        # 평가 세션 진행 중이면 지표 갱신 + MapState.session 탑재 (v1.2)
        self.state.session = session.tick(now, site, self.state)

    def snapshot(self) -> MapState:
        with self.lock:
            return self.state

    def camera_state(self, cam: CameraConfig) -> CameraState:
        st = self.snapshot()
        for cs in st.cameras:
            if cs.cam_id == cam.cam_id:
                return cs
        return CameraState(cam_id=cam.cam_id,
                           status="running" if cam.enabled else "disabled",
                           fps_in=cam.analyze_fps if cam.enabled else 0.0)


session = SessionSim()
sim = Simulator()
_started = time.time()


async def _tick_loop():
    while True:
        try:
            await asyncio.to_thread(sim.tick)
        except Exception as ex:                      # mock — 죽지 않게만
            print("[mock] tick error:", ex)
        await asyncio.sleep(1.0)


@app.on_event("startup")
async def _startup():
    asyncio.get_event_loop().create_task(_tick_loop())


# ================================================================ /api/site


@app.get("/api/site")
def get_site():
    site = store.load_site(SITE_ID)
    if site is None:
        raise HTTPException(404, "사이트 설정이 없습니다")
    return site


@app.put("/api/site")
def put_site(cfg: SiteConfig):
    cfg.site_id = SITE_ID
    prev = store.load_site(SITE_ID)
    if prev and prev.map and cfg.map and cfg.map.image != prev.map.image:
        raise HTTPException(400, "map.image는 /api/site/map 업로드로만 변경")
    return store.save_site(cfg)


@app.post("/api/site/map")
async def post_site_map(image: UploadFile = File(...), meta: str | None = Form(None)):
    raw = await image.read()
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:
        raise HTTPException(400, "이미지를 열 수 없습니다 (png/jpg)")
    site_dir = store.site_dir(SITE_ID)
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "map.png").write_bytes(raw)

    m_per_px = None
    if meta:
        try:
            mj = json.loads(meta)
            m_per_px = float(mj.get("m_per_px")) if mj.get("m_per_px") else None
        except (ValueError, TypeError, json.JSONDecodeError):
            raise HTTPException(400, "meta JSON 파싱 실패 (m_per_px)")

    site = store.load_site(SITE_ID) or SiteConfig(site_id=SITE_ID, version=0)
    prev_scale = site.map.scale if site.map else None
    # MapSpec은 scale 또는 m_per_px 필수 — 업로드 직후(축척 미지정)엔
    # placeholder m_per_px=1.0 을 넣고, UI의 축척 2점 저장(PUT /api/site)이 덮는다.
    spec = MapSpec(image="map.png", w=im.width, h=im.height,
                   scale=prev_scale if m_per_px is None else None,
                   m_per_px=m_per_px if m_per_px is not None
                   else (None if prev_scale else 1.0))
    site.map = spec
    store.save_site(site)
    return spec


@app.get("/api/site/map")
def get_site_map():
    """맵 원본 이미지 서빙 — 프론트 배경 렌더용 (계약 §4에 없어 mock에서 추가)."""
    p = store.site_dir(SITE_ID) / "map.png"
    if not p.is_file():
        raise HTTPException(404, "맵 이미지가 없습니다")
    return FileResponse(p, media_type="image/png")


# ================================================================ /api/cameras


class CameraCreate(BaseModel):
    name: str = ""
    rtsp: str
    analyze_fps: float = Field(default=5.0, gt=0, le=30)
    floor_id: str | None = None       # None이면 default 층


def _next_cam_id() -> str:
    used = {c.cam_id for c in store.list_cameras(SITE_ID)}
    i = 1
    while f"cam{i:02d}" in used:
        i += 1
    return f"cam{i:02d}"


def _get_cam(cam_id: str) -> CameraConfig:
    cam = store.load_camera(SITE_ID, cam_id)
    if cam is None:
        raise HTTPException(404, f"카메라 없음: {cam_id}")
    return cam


@app.get("/api/cameras")
def list_cameras():
    out = []
    for c in store.list_cameras(SITE_ID):
        out.append({**c.model_dump(), "state": sim.camera_state(c).model_dump()})
    return out


@app.post("/api/cameras")
def create_camera(body: CameraCreate):
    cam = CameraConfig(cam_id=_next_cam_id(), name=body.name, rtsp=body.rtsp,
                       analyze_fps=body.analyze_fps, floor_id=body.floor_id)
    return store.save_camera(SITE_ID, cam)


class CamerasBulkCreate(BaseModel):
    cameras: list[CameraCreate]


@app.post("/api/cameras/bulk")
def create_cameras_bulk(body: CamerasBulkCreate):
    """실서버와 동일 계약 — 여러 대 일괄 등록(mock은 워커가 없어 즉시 반영)."""
    if not body.cameras:
        raise HTTPException(422, "cameras: 비어 있지 않은 리스트가 필요")
    return [store.save_camera(SITE_ID, CameraConfig(
        cam_id=_next_cam_id(), name=b.name, rtsp=b.rtsp,
        analyze_fps=b.analyze_fps, floor_id=b.floor_id)) for b in body.cameras]


# ⚠️ /{cam_id} 보다 먼저 — 뒤에 두면 cam_id="bulk"로 잡힌다 (실서버와 동일)
@app.put("/api/cameras/bulk")
def update_cameras_bulk(body: dict):
    """실서버와 동일 계약 — 여러 대 설정 일괄 변경(전건 검증 후 반영)."""
    items = body.get("cameras") if isinstance(body, dict) else body
    if not isinstance(items, list) or not items:
        raise HTTPException(422, "cameras: 비어 있지 않은 리스트가 필요")
    cams = []
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not it.get("cam_id"):
            raise HTTPException(422, f"cameras[{i}]: cam_id가 필요")
        data = _get_cam(it["cam_id"]).model_dump()
        data.update({k: v for k, v in it.items() if k != "cam_id"})
        try:
            cams.append(CameraConfig.model_validate(data))
        except Exception as ex:
            raise HTTPException(422, f"cameras[{i}]: {ex}")
    return [store.save_camera(SITE_ID, c) for c in cams]


@app.put("/api/cameras/{cam_id}")
def update_camera(cam_id: str, patch: dict):
    cam = _get_cam(cam_id)
    data = cam.model_dump()
    patch.pop("cam_id", None)                       # id 변경 금지
    data.update(patch)
    try:
        cam2 = CameraConfig.model_validate(data)
    except Exception as ex:
        raise HTTPException(422, f"CameraConfig 검증 실패: {ex}")
    return store.save_camera(SITE_ID, cam2)


@app.delete("/api/cameras/{cam_id}")
def delete_camera(cam_id: str):
    if not store.delete_camera(SITE_ID, cam_id):
        raise HTTPException(404, f"카메라 없음: {cam_id}")
    return {"ok": True}


class RtspProbe(BaseModel):
    rtsp: str


@app.post("/api/cameras/probe")
def probe_rtsp(body: RtspProbe):
    """실서버와 동일 계약 — 등록 전 RTSP 연결 검사.
    mock은 실제로 붙지 않고, 주소에 'fail'이 들어간 것만 실패로 흉내낸다."""
    if not body.rtsp.strip():
        raise HTTPException(422, "rtsp가 필요합니다")
    if "fail" in body.rtsp:
        return {"ok": False, "width": 0, "height": 0}
    return {"ok": True, "width": 1920, "height": 1080}


@app.post("/api/cameras/{cam_id}/test")
def test_camera(cam_id: str):
    cam = _get_cam(cam_id)
    frame = _dummy_frame(cam)
    h, w = frame.shape[:2]
    b64 = base64.b64encode(_jpeg(frame)).decode()
    return {"ok": True, "width": w, "height": h,
            "snapshot_b64": "data:image/jpeg;base64," + b64}


@app.get("/api/cameras/{cam_id}/snapshot")
def camera_snapshot(cam_id: str):
    cam = _get_cam(cam_id)
    return Response(content=_jpeg(_dummy_frame(cam)), media_type="image/jpeg")


class MappingBody(BaseModel):
    cctv_pts: list[Point] = Field(min_length=4)
    map_pts: list[Point] = Field(min_length=4)
    valid_roi: list[Point] | None = None


@app.put("/api/cameras/{cam_id}/mapping")
def put_mapping(cam_id: str, body: MappingBody):
    cam = _get_cam(cam_id)
    if len(body.cctv_pts) != len(body.map_pts):
        raise HTTPException(422, "cctv_pts와 map_pts 개수 불일치")
    src = np.array(body.cctv_pts, dtype=np.float64)
    dst = np.array(body.map_pts, dtype=np.float64)
    H, _mask = cv2.findHomography(src, dst, method=0)
    if H is None:
        raise HTTPException(422, "호모그래피 산출 실패 — 대응점을 확인하세요")
    cam.mapping = CameraMapping(cctv_pts=body.cctv_pts, map_pts=body.map_pts,
                                H=[float(v) for v in H.reshape(-1)])
    # valid_roi는 요청 값으로 전체 교체 — null/3점 미만 = ROI 제거 (계약 v1.3, 실서버 동일)
    cam.valid_roi = body.valid_roi if (body.valid_roi and len(body.valid_roi) >= 3) else None
    return store.save_camera(SITE_ID, cam)


# ================================================================ /api/session


class SessionStart(BaseModel):
    origin: tuple[float, float]                  # 경보 위치 (맵 px)
    t_alarm: float | None = None                 # 생략 시 now


@app.post("/api/session/start")
def session_start(body: SessionStart):
    site = store.load_site(SITE_ID)
    with sim.lock:
        if session.live is not None:
            raise HTTPException(409, "세션이 이미 진행 중입니다")
        return session.start(body.origin, body.t_alarm or time.time(), site, sim)


@app.post("/api/session/stop")
def session_stop():
    site = store.load_site(SITE_ID)
    with sim.lock:
        if session.live is None:
            raise HTTPException(404, "진행 중인 세션이 없습니다")
        return session.stop(site, sim, time.time())


@app.get("/api/session")
def session_live():
    with sim.lock:
        if session.live is None:
            raise HTTPException(404, "진행 중인 세션이 없습니다")
        return session.live


@app.get("/api/session/result")
def session_result():
    with sim.lock:
        if session.result is None:
            raise HTTPException(404, "세션 결과가 없습니다")
        return session.result


@app.get("/api/session/timeline")
def session_timeline():
    with sim.lock:
        return list(session.timeline)


@app.get("/api/session/export")
def session_export(format: str = "json"):
    with sim.lock:
        res = session.result
    if res is None:
        raise HTTPException(404, "세션 결과가 없습니다 — 먼저 세션을 종료하세요")
    fname = f"evaluation_{res.session_id}"
    if format == "json":
        return Response(
            res.model_dump_json(indent=2), media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{fname}.json"'})
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["# summary"])
        w.writerow(["session_id", "alarm_ts", "alarm_origin_x", "alarm_origin_y",
                    "ended_at", "sei", "epfi_avg", "cbs_total",
                    "calibration_version", "config_version"])
        w.writerow([res.session_id, res.alarm_ts, res.alarm_origin[0],
                    res.alarm_origin[1], res.ended_at,
                    res.sei if res.sei is not None else "insufficient_data",
                    res.epfi_avg, res.cbs_total,
                    res.calibration_version, res.config_version])
        w.writerow(["# zone_metrics"])
        w.writerow(["zone_id", "evacuation_start_at", "response_delay_sec",
                    "graph_distance", "idr", "participant_ratio", "status"])
        for z in res.zone_metrics:
            w.writerow([z.zone_id, z.evacuation_start_at, z.response_delay_sec,
                        z.graph_distance, z.idr, z.participant_ratio, z.status])
        w.writerow(["# person_metrics"])
        w.writerow(["global_track_id", "assigned_route_id", "duration_sec",
                    "mean_deviation_m", "max_deviation_m", "epfi"])
        for p in res.person_metrics:
            w.writerow([p.global_track_id, p.assigned_route_id, p.duration_sec,
                        p.mean_deviation_m, p.max_deviation_m, p.epfi])
        w.writerow(["# bottleneck_metrics"])
        w.writerow(["bottleneck_id", "peak_density", "over_threshold_sec",
                    "cbs", "risk_level"])
        for b in res.bottleneck_metrics:
            w.writerow([b.bottleneck_id, b.peak_density, b.over_threshold_sec,
                        b.cbs, b.risk_level])
        w.writerow(["# exit_metrics"])
        w.writerow(["exit_id", "actual_count", "design_capacity",
                    "actual_share", "design_share"])
        for e in res.exit_metrics:
            w.writerow([e.exit_id, e.actual_count, e.design_capacity,
                        e.actual_share, e.design_share])
        return Response(
            buf.getvalue(), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'})
    raise HTTPException(422, "format은 json 또는 csv")


# ================================================================ map state


@app.get("/api/map/state")
def map_state():
    return sim.snapshot()


@app.get("/api/map/stream")
async def map_stream():
    async def gen():
        while True:
            payload = sim.snapshot().model_dump_json()
            yield f"event: state\ndata: {payload}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/status")
def status():
    st = sim.snapshot()
    return {
        "pipeline": {"mock": True, "uptime_s": round(time.time() - _started, 1),
                     "objects": len(st.objects), "tick_hz": 1.0},
        "cameras": [c.model_dump() for c in st.cameras],
    }


# ================================================================ 프론트 서빙

_MAIN_DIR = ROOT / "webui" / "static" / "main"


@app.get("/")
def index():
    p = _MAIN_DIR / "index.html"
    if not p.is_file():
        raise HTTPException(404, "webui/static/main/index.html 없음")
    return FileResponse(p)


app.mount("/static", StaticFiles(directory=ROOT / "webui" / "static"), name="static")
