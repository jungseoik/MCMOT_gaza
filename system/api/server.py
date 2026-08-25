"""멀티카메라 시스템 실서버 — CONTRACT §4 (mock_server.py와 동일 계약).

배선(INGEST_BACKEND 스위치 — 기본 ffmpeg, 안 건드리면 기존 동작과 동일):
  ffmpeg     IngestManager(ffmpeg-NVDEC) → FrameQueue → AnalyzerThread(TRT 공유)
             → MetricsEngine.on_tracks → MapState → REST/SSE → main 탭 canvas.
  deepstream DsIngestManager(GPU별 DS 워커 컨테이너 — zero-copy 디코드·배치
             추론·트래킹까지 컨테이너 안) → ZMQ 브리지 → MetricsEngine.on_tracks
             → 이후 동일. 근거·제약: docs/architecture/04-DeepStream-zero-copy-인제스트-전환.md

실행:
  conda run -n boosttrack uvicorn system.api.server:app --host 0.0.0.0 --port 8900

주의: 기존 webui PoC(webui/server.py)와 **같은 프로세스 동시 구동 금지**
(전역 GeneralSettings 충돌 — CONTRACT v1.1 §5). 별도 포트·별도 프로세스.
TRT 엔진 로드 실패 시(개발 장비 등) 추적 없이 API/UI만 동작한다.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import tempfile
import time
import subprocess
import shutil
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from system.config.schema import (
    DEFAULT_FLOOR_ID,
    CameraConfig,
    CameraMapping,
    ExitLine,
    Floor,
    MapSpec,
    Route,
    SiteConfig,
)
from system.config.store import SiteStore
from system.contracts import DrillResult, MapState
from system.ingest.frame_queue import FrameQueue
from system.ingest.manager import IngestManager
import model_zoo
from system.metrics.engine import MetricsEngine
from system.metrics.recorder import SessionRecorder
from system.vsource import controller as vsource
from system.vsource import scenario as vscenario

logger = logging.getLogger("system.api")

SITE_ID = os.environ.get("SITE_ID", "default")
SITE_ROOT = os.environ.get("SITE_ROOT", "data/sites")
GPU_DEVICES = [int(x) for x in os.environ.get("GPU_DEVICES", "0,1").split(",") if x != ""]
# 인제스트 백엔드 스위치 — "ffmpeg"(기본, 기존 경로 그대로) | "deepstream"(DS 워커)
INGEST_BACKEND = os.environ.get("INGEST_BACKEND", "ffmpeg").strip().lower()
if INGEST_BACKEND not in ("ffmpeg", "deepstream"):
    raise ValueError(f"INGEST_BACKEND는 ffmpeg|deepstream — 받은 값: {INGEST_BACKEND!r}")
# 세션 녹화 (계약 v1.10) — 경보 세션의 입력 트랙을 <session_id>.db로 기록.
# 기본 on. 0/false면 녹화 끔(기존과 동일 동작, 롤백). 리플레이/재계산의 원료.
SESSION_RECORD = os.environ.get("SESSION_RECORD", "1").strip().lower() not in ("0", "false", "no", "")
FRONT_DIR = Path(__file__).resolve().parents[2] / "webui" / "static" / "main"


class Runtime:
    """서버 수명 동안의 파이프라인 상태 (설정 변경 시 reload)."""

    def __init__(self) -> None:
        self.store = SiteStore(SITE_ROOT)
        if self.store.bootstrap_from_seed(SITE_ID):
            logger.info("seed에서 디폴트 사이트 세팅 복사됨: %s", SITE_ID)
        self.queue = FrameQueue(maxsize=64)
        if INGEST_BACKEND == "deepstream":
            # 지연 import — ffmpeg 모드에서는 pyzmq/docker 의존이 전혀 없어야 한다
            from system.ingest_ds.launcher import DsIngestManager, parse_gpu_devices
            # 주의: GPU_DEVICES 미지정 시 기본값이 갈린다 — ffmpeg "0,1" / DS "1"
            # (DS 워커는 GPU 전유 전제, GPU0은 타 프로젝트 상주 — launcher 기본 유지)
            self.ingest = DsIngestManager(self._dispatch_tracks,
                                          gpu_devices=parse_gpu_devices())
        else:
            self.ingest = IngestManager(self.queue, gpu_devices=GPU_DEVICES or None)
        # 층(floor)마다 엔진 1개 — 층=독립 좌표계 (v1.7). floor_id → MetricsEngine.
        self.engines: dict[str, MetricsEngine] = {}
        self._cam_floor: dict[str, str] = {}   # cam_id → floor_id (라우팅 캐시)
        self.analyzer = None  # AnalyzerThread | None — TRT 로드 실패 시 None
        self._lock = threading.Lock()

    def _dispatch_tracks(self, cam_id: str, ts: float, tracks) -> None:
        """트래킹 → 엔진 라우터. cam_id 소속 층 엔진으로 위임 (엔진은
        startup에서 생성되므로 지연 위임). DS 브리지·AnalyzerThread 공용."""
        eng = self.engines.get(self._cam_floor.get(cam_id, DEFAULT_FLOOR_ID))
        if eng is not None:
            eng.on_tracks(cam_id, ts, tracks)

    # --------------------------------------------------- 층(floor) 해석
    def resolve_floor(self, floor_id: str | None = None) -> str:
        """요청 floor_id를 실재 층 id로 해석 (없으면 default, default도 없으면
        첫 엔진). engines는 site.floors와 동기 유지된다."""
        fid = floor_id or DEFAULT_FLOOR_ID
        if fid in self.engines:
            return fid
        if DEFAULT_FLOOR_ID in self.engines:
            return DEFAULT_FLOOR_ID
        return next(iter(self.engines), DEFAULT_FLOOR_ID)

    def require_floor(self, floor_id: str | None = None) -> str:
        """**쓰기용** 층 해석 — 없는 층이면 404.

        resolve_floor()의 default 폴백은 조회에는 알맞지만 쓰기에는 위험하다.
        오타나 이미 지운 층 id로 맵·공간요소를 밀어 넣으면 조용히 default
        층을 덮어써서 그 층 설정이 통째로 날아간다(실측: floor=nosuchfloor
        로 CAD 적용 → 17F가 교체됨). 쓰기는 반드시 이쪽을 쓴다.
        """
        fid = floor_id or DEFAULT_FLOOR_ID
        if fid not in self.engines:
            raise HTTPException(404, f"층 없음: {fid}")
        return fid

    def engine_for(self, floor_id: str | None = None) -> MetricsEngine | None:
        return self.engines.get(self.resolve_floor(floor_id))

    def guard_session(self, fid: str) -> None:
        """세션 진행 중인 층의 기하(맵·경로·구역·병목)는 못 바꾸게 막는다.

        도중에 갈아엎으면 같은 세션 안에서 앞뒤 구간이 서로 다른 기하로
        계산돼 지표를 신뢰할 수 없고, reload_engine 으로 진행 중 세션이
        통째로 날아간다(실측: 적용 직후 GET /api/session → 404).
        """
        eng = self.engines.get(fid)
        if eng is not None and eng.session_live() is not None:
            raise HTTPException(
                409, f"'{fid}' 층에서 평가 세션이 진행 중입니다 — "
                     "먼저 세션을 종료한 뒤 도면을 적용하세요")

    # ------------------------------------------------------------ 설정 접근
    def site(self) -> SiteConfig:
        cfg = self.store.load_site(SITE_ID)
        if cfg is None:
            cfg = self.store.save_site(SiteConfig(site_id=SITE_ID), bump_version=False)
        return cfg

    def cameras(self) -> list[CameraConfig]:
        return self.store.list_cameras(SITE_ID)

    def reload_engine(self) -> None:
        """층 목록·카메라 매핑 변경을 엔진에 반영 — 층마다 엔진을 유지/생성/삭제.
        기존 엔진은 reload()로 통과선 카운트·진행 세션을 보존한다."""
        with self._lock:
            site, cams = self.site(), self.cameras()
            self._cam_floor = {c.cam_id: site.floor_id_of_camera(c) for c in cams}
            floor_ids = {fl.id for fl in site.floors}
            for fid in list(self.engines):          # 삭제된 층 엔진 정리
                if fid not in floor_ids:
                    del self.engines[fid]
            for fl in site.floors:
                view = site.as_floor_view(fl.id)
                floor_cams = [c for c in cams if self._cam_floor[c.cam_id] == fl.id]
                eng = self.engines.get(fl.id)
                if eng is None:
                    self.engines[fl.id] = MetricsEngine(view, floor_cams)
                else:
                    eng.reload(view, floor_cams)

    def reset_to_seed(self) -> bool:
        """사이트를 커밋된 디폴트(seed)로 복원 + 인제스트/엔진 재적용.
        카메라가 seed 기준으로 바뀌므로 ingest를 재기동한다(세션 녹화본은 보존)."""
        if not self.store.reset_from_seed(SITE_ID):
            return False
        try:
            self.ingest.stop()
        except Exception:
            logger.exception("reset: ingest stop 실패(무시하고 진행)")
        self.reload_engine()
        try:
            self.ingest.start(self.cameras())
        except Exception:
            logger.exception("reset: ingest start 실패")
        return True

    def participating_floors(self) -> list[str]:
        """드릴 참여 층 = 매핑(호모그래피)된 카메라가 ≥1개 있는 층 (추적이 실제로
        이뤄지는 층). 카메라 없는 층(예: 지상1층)은 제외. floors 순서 유지."""
        site, cams = self.site(), self.cameras()
        have = {site.floor_id_of_camera(c) for c in cams if c.mapping is not None}
        return [fl.id for fl in site.floors if fl.id in have]

    # ------------------------------------------------------------ 수명주기
    def startup(self) -> None:
        self.reload_engine()          # 층별 엔진 생성 + cam→floor 캐시 구성
        cams = self.cameras()
        self.ingest.start(cams)

        if INGEST_BACKEND == "deepstream":
            # 추론·트래킹은 DS 워커 컨테이너 안 — 호스트 AnalyzerThread 불필요
            logger.info("DeepStream 인제스트 기동 (%d cams) — 호스트 TRT 미로드", len(cams))
            return

        try:  # TRT 미가용 환경에서도 API/UI는 동작
            from system.tracking.analyzer import AnalyzerThread
            self.analyzer = AnalyzerThread(
                self.queue,
                on_tracks=self._dispatch_tracks,   # 층 라우터 경유
                camera_fps={c.cam_id: c.analyze_fps for c in cams},
            )
            self.analyzer.start()
            logger.info("AnalyzerThread 기동 (%d cams)", len(cams))
        except Exception:
            logger.exception("트래킹 비활성 — TRT/트래커 초기화 실패 (API/UI만 동작)")

    def shutdown(self) -> None:
        if self.analyzer is not None:
            self.analyzer.stop()
        self.ingest.stop()

    def next_cam_ids(self, count: int) -> list[str]:
        """연속된 미사용 cam_id를 count개 — 벌크 등록에서 store 재조회 없이 발번."""
        nums = [int(c.cam_id[3:]) for c in self.cameras()
                if c.cam_id.startswith("cam") and c.cam_id[3:].isdigit()]
        start = max(nums) + 1 if nums else 1
        return [f"cam{n:02d}" for n in range(start, start + count)]

    def next_cam_id(self) -> str:
        return self.next_cam_ids(1)[0]

    # ------------------------------------------------------------ 스냅샷
    def grab_frame(self, cfg: CameraConfig) -> np.ndarray | None:
        """실행 중 워커 프레임 우선, 없으면 1회성 캡처(test/미기동 카메라용).

        deepstream 모드에서는 get_snapshot()이 항상 None(픽셀이 컨테이너 밖으로
        안 나옴) → 아래 ffmpeg 단발 캡처(cv2 CAP_FFMPEG)로 폴백된다."""
        frame = self.ingest.get_snapshot(cfg.cam_id)
        if frame is not None:
            return frame
        cap = cv2.VideoCapture(cfg.rtsp, cv2.CAP_FFMPEG)
        try:
            ok, frame = cap.read()
            return frame if ok else None
        finally:
            cap.release()


rt = Runtime()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    rt.startup()
    yield
    rt.shutdown()


app = FastAPI(title="MACS-EVAC 멀티카메라 시스템", lifespan=_lifespan)


def _cam_or_404(cam_id: str) -> CameraConfig:
    cfg = rt.store.load_camera(SITE_ID, cam_id)
    if cfg is None:
        raise HTTPException(404, f"카메라 없음: {cam_id}")
    return cfg


# ================================================================ 사이트
@app.get("/api/site")
def get_site():
    cfg = rt.store.load_site(SITE_ID)
    if cfg is None:
        raise HTTPException(404, "사이트 미설정")
    return cfg


@app.post("/api/site/reset-seed")
def site_reset_seed():
    """현재 사이트를 커밋된 디폴트(seed)로 초기화 — 실험 설정 원복.
    설정(층·맵·카메라·매핑·공간요소·임계값)만 복원, 세션 녹화본은 보존."""
    if not rt.reset_to_seed():
        raise HTTPException(404, "seed 없음 — 복원 불가")
    return rt.site()


@app.put("/api/site")
async def put_site(request: Request):
    body = await request.json()
    body["site_id"] = SITE_ID  # site_id 고정
    prev = rt.store.load_site(SITE_ID)
    if prev is not None and "map" not in body:
        body["map"] = prev.map.model_dump() if prev.map else None
    # floors 방어: body에 floors가 없으면 기존 층 구성을 보존한다. 없으면
    # 검증기가 top-level만 default 층 1개로 재승격해 **다층 사이트가 붕괴**한다
    # (map 보존과 동일한 비대칭 방지 — 부분 PUT·외부 스크립트 안전).
    if prev is not None and "floors" not in body and prev.floors:
        body["floors"] = [fl.model_dump() for fl in prev.floors]
    cfg = SiteConfig.model_validate(body)
    cfg = rt.store.save_site(cfg)
    rt.reload_engine()
    return cfg


@app.post("/api/site/map")
async def post_site_map(image: UploadFile = File(...), meta: str | None = Form(None),
                        floor: str = DEFAULT_FLOOR_ID):
    data = await image.read()
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise HTTPException(422, "이미지 디코드 실패")
    fid = rt.require_floor(floor)
    rt.guard_session(fid)
    path = rt.store.map_path(SITE_ID, fid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    m_per_px = None
    src_name = unit_name = None
    if meta:  # cad-convert 메타 JSON — m_per_px 자동 (계약 A-6)
        try:
            mj = json.loads(meta)
            m_per_px = float(mj.get("m_per_px"))
        except (ValueError, TypeError, json.JSONDecodeError):
            raise HTTPException(422, "meta JSON에서 m_per_px를 읽지 못함")
        # 출처(표시 전용) — 없으면 None. 있으면 UI가 "무엇에서 나온 축척인지"
        # 보여줘 불필요한 수동 2점 재측정을 막는다.
        src_name = mj.get("source") or None
        unit_name = mj.get("unit") or None

    cfg = rt.site()
    fl = cfg.get_floor(fid)
    # 재업로드 시 기존 2점 축척 유지 — 단, **CAD 실측 축척(m_per_px)이 함께 오면
    # 버린다**. 옛 2점은 이전 맵의 px 좌표에서 찍은 값이라 새 맵과 무관하고
    # (구역·병목을 비우는 것과 같은 이유), 남겨두면 UI에 엉뚱한 위치로 표시되며
    # 나중에 m_per_px가 지워질 때 조용히 틀린 축척으로 폴백한다.
    prev_scale = None if m_per_px is not None else (fl.map.scale if fl.map else None)
    spec = MapSpec(image=path.name, w=arr.shape[1], h=arr.shape[0],
                   scale=prev_scale, m_per_px=m_per_px,
                   source=src_name, unit=unit_name)
    # 맵 픽셀 크기가 달라지면 그 층 카메라 매핑(map_pts)은 옛 맵 px 기준이라
    # 무효다. 남겨두면 사람이 엉뚱한 위치로 투영되는데 화면상 "매핑됨"으로
    # 보여 알아채기 어렵다 — 구역·병목을 비우는 것과 같은 이유로 해제한다.
    # 크기가 같으면(같은 도면 재적용 등) 그대로 둔다.
    prev = fl.map
    cleared: list[str] = []
    if prev is not None and (prev.w, prev.h) != (spec.w, spec.h):
        for cam in rt.cameras():
            if cfg.floor_id_of_camera(cam) == fid and cam.mapping is not None:
                cam.mapping = None
                rt.store.save_camera(SITE_ID, cam)
                cleared.append(cam.cam_id)

    fl.map = spec
    if fid == DEFAULT_FLOOR_ID:
        cfg.map = spec                               # top-level 동기화(재승격 대비)
    rt.store.save_site(cfg)
    rt.reload_engine()
    return {**spec.model_dump(), "mappings_cleared": cleared}


@app.get("/api/site/map")
def get_site_map(floor: str = DEFAULT_FLOOR_ID):
    p = rt.store.map_path(SITE_ID, rt.resolve_floor(floor))
    if not p.is_file():
        raise HTTPException(404, "맵 이미지 없음")
    return FileResponse(p, media_type="image/png")


AUTO_ROUTE_PREFIX = "auto-evac-"   # CAD 편집기가 자동 산출한 피난경로 id 접두 (D-2 ①)


@app.put("/api/site/routes")
async def put_site_routes(request: Request, floor: str = DEFAULT_FLOOR_ID):
    """한 층(floor)의 피난경로(routes)만 교체 — 다층 붕괴 위험 없는 부분 반영.

    CAD 도면 편집기(:8910)의 최단경로(worst-N) 결과를 EPFI 기준경로로
    자동 반영하는 전용 경로 (요구사항 D-2 ① CAD 자동 산출 분기).

    body:
      routes  : [{id, name, points:[[px,px]...]}] — 맵 원본 px polyline.
      replace : "auto"(기본) → id가 'auto-evac-'로 시작하는 자동경로만
                교체하고 사용자가 손으로 그린 경로는 보존.
                "all" → 그 층 routes 전체를 교체.
    PUT /api/site 로 floors 통째 전송하지 않으므로 다른 층은 절대 건드리지
    않는다. reload_engine 으로 즉시 반영.
    """
    body = await request.json()
    try:
        new_routes = [Route.model_validate(r) for r in body.get("routes", [])]
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    mode = str(body.get("replace", "auto"))
    if mode not in ("auto", "all"):
        raise HTTPException(422, "replace는 'auto'|'all'")

    cfg = rt.site()
    fid = rt.require_floor(floor)
    rt.guard_session(fid)
    fl = cfg.get_floor(fid)
    kept = ([] if mode == "all"
            else [r for r in fl.routes if not r.id.startswith(AUTO_ROUTE_PREFIX)])
    fl.routes = kept + new_routes
    if fid == DEFAULT_FLOOR_ID:
        cfg.routes = fl.routes          # top-level 동기화(재승격 대비)
    rt.store.save_site(cfg)
    rt.reload_engine()
    return {"floor": fid, "routes": len(fl.routes),
            "auto": sum(1 for r in fl.routes if r.id.startswith(AUTO_ROUTE_PREFIX)),
            "manual": sum(1 for r in fl.routes if not r.id.startswith(AUTO_ROUTE_PREFIX))}


@app.put("/api/site/floor-elements")
async def put_site_floor_elements(request: Request, floor: str = DEFAULT_FLOOR_ID):
    """CAD 도면 적용 시 한 층(floor)의 공간요소를 새 CAD 기준으로 재세팅 —
    다층 붕괴 위험 없는 부분 반영 (요구사항 D-2, v1.9).

    새 CAD 도면으로 맵이 바뀌면 옛 공간요소 좌표(옛 맵 px 기준)가 새 맵과
    맞지 않으므로: 편집기가 아는 것(피난경로·출입구)만 CAD 기준으로 자동
    세팅하고, 모르는 것(구역·병목)은 옛것을 남기지 않고 비워 새 맵 위에
    다시 그리게 한다.

    body (모두 선택 — 키가 있을 때만 반영):
      routes            : [{id, name, points:[[px,px]...]}] — 맵 원본 px polyline.
      replace           : "auto"(기본)/"all" — routes 교체 규칙(put_site_routes와 동일).
                          자동경로 id 접두 'auto-evac-'만 교체하거나 전체 교체.
      exits             : [{id, name, line:[[px,px],[px,px]], inside:[px,px],
                          design_capacity?}] — 그 층 exits **전체 교체**(CAD 기준 재세팅).
      clear_zones       : true → 그 층 zones를 빈 리스트로.
      clear_bottlenecks : true → 그 층 bottlenecks를 빈 리스트로.
    PUT /api/site 로 floors 통째 전송하지 않으므로 다른 층은 절대 건드리지
    않는다. reload_engine 으로 즉시 반영.
    """
    body = await request.json()
    mode = str(body.get("replace", "auto"))
    if mode not in ("auto", "all"):
        raise HTTPException(422, "replace는 'auto'|'all'")
    try:
        new_routes = ([Route.model_validate(r) for r in body["routes"]]
                      if "routes" in body else None)
        new_exits = ([ExitLine.model_validate(e) for e in body["exits"]]
                     if "exits" in body else None)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    cfg = rt.site()
    fid = rt.require_floor(floor)
    rt.guard_session(fid)
    fl = cfg.get_floor(fid)

    if new_routes is not None:
        kept = ([] if mode == "all"
                else [r for r in fl.routes if not r.id.startswith(AUTO_ROUTE_PREFIX)])
        fl.routes = kept + new_routes
    if new_exits is not None:
        fl.exits = new_exits                 # CAD 기준 전체 교체
    if bool(body.get("clear_zones", False)):
        fl.zones = []
    if bool(body.get("clear_bottlenecks", False)):
        fl.bottlenecks = []

    if fid == DEFAULT_FLOOR_ID:              # top-level 동기화(재승격 대비)
        cfg.routes, cfg.exits = fl.routes, fl.exits
        cfg.zones, cfg.bottlenecks = fl.zones, fl.bottlenecks
    rt.store.save_site(cfg)
    rt.reload_engine()
    return {"floor": fid, "routes": len(fl.routes), "exits": len(fl.exits),
            "zones": len(fl.zones), "bottlenecks": len(fl.bottlenecks),
            "auto_routes": sum(1 for r in fl.routes if r.id.startswith(AUTO_ROUTE_PREFIX))}


# ================================================================ 카메라
@app.get("/api/cameras")
def list_cameras():
    states = {s.cam_id: s for s in rt.ingest.states()}
    out = []
    for c in rt.cameras():
        s = states.get(c.cam_id)
        out.append({**c.model_dump(),
                    "state": s.model_dump() if s else {"cam_id": c.cam_id, "status": "disabled"}})
    return out


@app.post("/api/cameras")
async def add_camera(request: Request):
    body = await request.json()
    try:
        cfg = CameraConfig(cam_id=rt.next_cam_id(), name=body.get("name", ""),
                           rtsp=body["rtsp"], analyze_fps=body.get("analyze_fps", 5.0),
                           floor_id=body.get("floor_id"),   # None이면 default 층
                           min_conf=body.get("min_conf"))   # None이면 사이트값 상속
    except (ValidationError, KeyError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    rt.store.save_camera(SITE_ID, cfg)
    rt.ingest.add_camera(cfg)
    if rt.analyzer is not None:
        rt.analyzer.set_camera_fps(cfg.cam_id, cfg.analyze_fps)
    rt.reload_engine()
    return cfg


@app.post("/api/cameras/bulk")
async def add_cameras_bulk(request: Request):
    """카메라 여러 대를 한 번에 등록 — DS 워커 재시작을 슬롯당 1회로 묶는다.

    body: {"cameras": [{rtsp, name?, analyze_fps?, floor_id?, min_conf?,
                        enabled?}, ...]}   (최상위가 바로 리스트여도 됨)

    한 대씩 POST /api/cameras 하면 DS 백엔드에서 매번 워커가 재시작되고 같은
    슬롯의 기존 채널이 전부 ~8s 끊긴다(실측). 40채널이면 ~9.5분 + 40회 단절.
    이 엔드포인트는 전건 검증 후 한 번에 반영해 그 비용을 1회로 만든다.

    **전건 검증 후 등록** — 하나라도 유효하지 않으면 아무것도 등록하지 않는다.
    """
    body = await request.json()
    items = body.get("cameras") if isinstance(body, dict) else body
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=422,
                            detail="cameras: 비어 있지 않은 리스트가 필요")

    cfgs: list[CameraConfig] = []
    for i, (cam_id, it) in enumerate(zip(rt.next_cam_ids(len(items)), items)):
        if not isinstance(it, dict):
            raise HTTPException(status_code=422, detail=f"cameras[{i}]: 객체가 아님")
        try:
            cfgs.append(CameraConfig(
                cam_id=cam_id, name=it.get("name", ""), rtsp=it["rtsp"],
                enabled=it.get("enabled", True),
                analyze_fps=it.get("analyze_fps", 5.0),
                floor_id=it.get("floor_id"),      # None이면 default 층
                min_conf=it.get("min_conf")))     # None이면 사이트값 상속
        except (ValidationError, KeyError) as e:
            raise HTTPException(status_code=422, detail=f"cameras[{i}]: {e}")

    for cfg in cfgs:                    # 검증을 다 통과한 뒤에만 영속화
        rt.store.save_camera(SITE_ID, cfg)
    rt.ingest.add_cameras(cfgs)         # 슬롯당 워커 재시작 1회
    if rt.analyzer is not None:
        for cfg in cfgs:
            rt.analyzer.set_camera_fps(cfg.cam_id, cfg.analyze_fps)
    rt.reload_engine()
    return cfgs


# ⚠️ /{cam_id} 보다 먼저 등록해야 한다 — FastAPI는 선언 순서로 매칭하므로
#    뒤에 두면 cam_id="bulk" 로 잡혀 404가 난다.
@app.put("/api/cameras/bulk")
async def update_cameras_bulk(request: Request):
    """여러 카메라 설정을 한 번에 변경 — DS 워커 재시작을 슬롯당 1회로 묶는다.

    body: {"cameras": [{"cam_id": "cam05", <바꿀 필드들>}, ...]}
          (최상위가 바로 리스트여도 됨)

    대표 용례는 **매핑을 끝낸 카메라들의 일괄 활성화**. 하나씩 PUT 하면
    비활성→활성이 신규 추가와 같은 경로를 타 매번 워커가 재시작되고, 같은
    슬롯의 기존 채널이 전부 ~8s 끊긴다. 40대면 ~9.5분 + 40회 단절.

    **전건 검증 후 반영** — 하나라도 유효하지 않으면 아무것도 바뀌지 않는다.
    """
    body = await request.json()
    items = body.get("cameras") if isinstance(body, dict) else body
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=422,
                            detail="cameras: 비어 있지 않은 리스트가 필요")

    cfgs: list[CameraConfig] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not it.get("cam_id"):
            raise HTTPException(status_code=422,
                                detail=f"cameras[{i}]: cam_id가 필요")
        old = rt.store.load_camera(SITE_ID, it["cam_id"])
        if old is None:
            raise HTTPException(status_code=404,
                                detail=f"cameras[{i}]: 카메라 없음 {it['cam_id']}")
        patch = {k: v for k, v in it.items() if k != "cam_id"}
        try:
            cfgs.append(CameraConfig.model_validate({**old.model_dump(), **patch}))
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=f"cameras[{i}]: {e}")

    for cfg in cfgs:                    # 검증을 다 통과한 뒤에만 영속화
        rt.store.save_camera(SITE_ID, cfg)
    rt.ingest.update_cameras(cfgs)      # 슬롯당 워커 재시작 1회
    if rt.analyzer is not None:
        for cfg in cfgs:
            rt.analyzer.set_camera_fps(cfg.cam_id, cfg.analyze_fps)
    rt.reload_engine()
    return cfgs


@app.put("/api/cameras/{cam_id}")
async def update_camera(cam_id: str, request: Request):
    old = _cam_or_404(cam_id)
    patch = await request.json()
    patch.pop("cam_id", None)
    try:
        cfg = CameraConfig.model_validate({**old.model_dump(), **patch})
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    rt.store.save_camera(SITE_ID, cfg)
    rt.ingest.update_camera(cfg)
    if rt.analyzer is not None:
        rt.analyzer.set_camera_fps(cfg.cam_id, cfg.analyze_fps)
    rt.reload_engine()
    return cfg


@app.delete("/api/cameras/{cam_id}")
def delete_camera(cam_id: str):
    _cam_or_404(cam_id)
    rt.ingest.remove_camera(cam_id)
    if rt.analyzer is not None:
        rt.analyzer.remove_camera(cam_id)
    rt.store.delete_camera(SITE_ID, cam_id)
    rt.reload_engine()
    return {"ok": True}


@app.post("/api/cameras/probe")
async def probe_rtsp(request: Request):
    """**등록 전** 임의 RTSP 주소 연결 검사 — body {"rtsp": "..."}.

    벌크 등록 화면에서 수십 개 주소를 미리 검증하는 용도. 등록된 카메라를
    요구하는 `/api/cameras/{id}/test`와 달리 저장을 남기지 않는다.
    스냅샷은 돌려주지 않는다(수십 장을 한 번에 받을 이유가 없음) — 연결 여부와
    해상도만.
    """
    body = await request.json()
    rtsp = (body.get("rtsp") or "").strip()
    if not rtsp:
        raise HTTPException(status_code=422, detail="rtsp가 필요합니다")
    probe = CameraConfig(cam_id="__probe__", rtsp=rtsp)
    frame = rt.grab_frame(probe)      # 미등록 cam_id → ffmpeg 1회성 캡처로 폴백
    if frame is None:
        return {"ok": False, "width": 0, "height": 0}
    return {"ok": True, "width": frame.shape[1], "height": frame.shape[0]}


@app.post("/api/cameras/{cam_id}/test")
def test_camera(cam_id: str):
    cfg = _cam_or_404(cam_id)
    frame = rt.grab_frame(cfg)
    if frame is None:
        return {"ok": False, "width": 0, "height": 0, "snapshot_b64": None}
    ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    # snapshot_b64는 data URL 형식 (CONTRACT v1.1 §4 — 프론트 <img>.src 직행)
    b64 = ("data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode()) if ok else None
    return {"ok": True, "width": frame.shape[1], "height": frame.shape[0], "snapshot_b64": b64}


@app.get("/api/cameras/{cam_id}/snapshot")
def camera_snapshot(cam_id: str):
    cfg = _cam_or_404(cam_id)
    frame = rt.grab_frame(cfg)
    if frame is None:
        raise HTTPException(404, "프레임 수신 실패")
    ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise HTTPException(500, "JPEG 인코딩 실패")
    return Response(jpg.tobytes(), media_type="image/jpeg")


@app.put("/api/cameras/{cam_id}/mapping")
async def set_mapping(cam_id: str, request: Request):
    cfg = _cam_or_404(cam_id)
    body = await request.json()
    cctv_pts, map_pts = body["cctv_pts"], body["map_pts"]
    if len(cctv_pts) < 4 or len(cctv_pts) != len(map_pts):
        raise HTTPException(422, "대응점은 4쌍 이상, 개수 일치 필요")
    src = np.array(cctv_pts, np.float64)
    dst = np.array(map_pts, np.float64)
    H, _ = cv2.findHomography(src, dst)
    if H is None:
        raise HTTPException(422, "호모그래피 산출 실패 — 대응점이 퇴화 배치")
    # 대응점별 재투영 오차(맵 px) — FR-01 기준점 오차 기록, UI가 품질 표시에 사용
    proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
    errs = [float(np.hypot(*(p - d))) for p, d in zip(proj, dst)]
    cfg.mapping = CameraMapping(cctv_pts=cctv_pts, map_pts=map_pts,
                                H=[float(v) for v in H.reshape(-1)],
                                reproj_err_px=[round(e, 2) for e in errs])
    # valid_roi는 요청 값으로 전체 교체 — null/3점 미만 = ROI 제거 (계약 v1.3)
    roi = body.get("valid_roi")
    cfg.valid_roi = roi if (roi and len(roi) >= 3) else None
    if "floor_id" in body:            # 층 매핑 (v1.7) — map_pts는 해당 층 맵 px
        cfg.floor_id = body["floor_id"]
    rt.store.save_camera(SITE_ID, cfg)
    rt.reload_engine()
    return cfg


# ================================================================ 층(floor) — 다중 도면 (v1.7)
def _floor_summary(cfg: SiteConfig, fl: Floor) -> dict:
    n_cams = sum(1 for c in rt.cameras() if cfg.floor_id_of_camera(c) == fl.id)
    return {"id": fl.id, "name": fl.name,
            "has_map": fl.map is not None,
            "map": fl.map.model_dump() if fl.map else None,
            "camera_count": n_cams}


@app.get("/api/floors")
def list_floors():
    """층 목록(요약) — 운영뷰 층 전환 탭용."""
    cfg = rt.site()
    return [_floor_summary(cfg, fl) for fl in cfg.floors]


@app.post("/api/floors")
async def add_floor(request: Request):
    """층 추가 — body {id?, name?}. id 생략 시 서버 발급(floor2..).
    id 중복은 409."""
    body = await request.json()
    cfg = rt.site()
    ids = {fl.id for fl in cfg.floors}
    fid = (body.get("id") or "").strip()
    if fid and fid in ids:
        raise HTTPException(409, f"이미 존재하는 층 id: {fid}")
    if not fid:
        n = 2
        while f"floor{n}" in ids:
            n += 1
        fid = f"floor{n}"
    cfg.floors.append(Floor(id=fid, name=body.get("name", "")))
    rt.store.save_site(cfg)
    rt.reload_engine()
    return _floor_summary(rt.site(), cfg.get_floor(fid))


@app.delete("/api/floors/{floor_id}")
def delete_floor(floor_id: str):
    """층 삭제 — default 삭제 불가, 최소 1개 층 보장. 소속 카메라는
    default 층으로 재배정(floor_id=None)."""
    if floor_id == DEFAULT_FLOOR_ID:
        raise HTTPException(422, "default 층은 삭제할 수 없습니다")
    cfg = rt.site()
    ids = {fl.id for fl in cfg.floors}
    if floor_id not in ids:
        raise HTTPException(404, f"층 없음: {floor_id}")
    if len(cfg.floors) <= 1:
        raise HTTPException(422, "최소 1개 층이 필요합니다")
    cfg.floors = [fl for fl in cfg.floors if fl.id != floor_id]
    rt.store.save_site(cfg)
    # 해당 층 소속 카메라 → default 재배정 (고아 방지)
    for cam in rt.cameras():
        if cam.floor_id == floor_id:
            cam.floor_id = None
            rt.store.save_camera(SITE_ID, cam)
    rt.reload_engine()
    return {"ok": True}


# ================================================================ 평가 세션 (계약 v1.2)
@app.post("/api/session/start")
async def session_start(request: Request, floor: str = DEFAULT_FLOOR_ID):
    fid = rt.resolve_floor(floor)
    eng = rt.engine_for(fid)
    if eng is None:
        raise HTTPException(503, "엔진 미기동")
    body = await request.json()
    if eng.session_live() is not None:
        raise HTTPException(409, "세션 진행 중 — 먼저 종료하세요")
    # origins 우선, 없으면 단일 origin (하위 호환)
    origins = body.get("origins")
    origin_xy = tuple(body["origin"]) if "origin" in body else None
    live = eng.start_session(
        origin_xy=origin_xy,
        t_alarm=body.get("t_alarm"),
        alarm_origins=[tuple(o) for o in origins] if origins else None,
    )
    if SESSION_RECORD:                     # 세션 녹화 부착 (계약 v1.10)
        try:
            _attach_recorder(eng, fid, live)
        except Exception:
            logger.exception("세션 녹화기 부착 실패 — 녹화 없이 진행")
    return live


def _sessions_dir(floor_id: str = DEFAULT_FLOOR_ID) -> Path:
    """세션 저장 디렉토리 — default 층은 기존 sessions/ 유지(하위호환),
    그 외 층은 sessions/<floor_id>/."""
    base = rt.store.site_dir(SITE_ID) / "sessions"
    d = base if floor_id == DEFAULT_FLOOR_ID else base / floor_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_db_path(session_id: str, floor_id: str = DEFAULT_FLOOR_ID) -> Path:
    """세션 녹화 db 경로 — 집계 <session_id>.json 옆에 <session_id>.db (v1.10)."""
    return _sessions_dir(floor_id) / f"{session_id}.db"


def _attach_recorder(eng, floor_id: str, live) -> None:
    """세션 시작 시 녹화기 부착 — 그 층의 공간요소·카메라 스냅샷을 meta로 저장.
    스냅샷이 있어야 리플레이가 세션 당시 도면 기준으로 결정적 재생된다."""
    site = rt.site()
    cams = rt.cameras()
    floor_cams = [c for c in cams if site.floor_id_of_camera(c) == floor_id]
    meta = {
        "session_id": live.session_id,
        "floor_id": floor_id,
        "site_id": SITE_ID,
        "alarm_ts": live.alarm_ts,
        "alarm_origins": [list(o) for o in (live.alarm_origins or [])],
        "site_version": site.version,
        "site_view": site.as_floor_view(floor_id).model_dump(),
        "cameras": [c.model_dump() for c in floor_cams],
    }
    rec = SessionRecorder(_session_db_path(live.session_id, floor_id), meta)
    eng.attach_recorder(rec)


def _save_session(result, timeline, person_series=None,
                  floor_id: str = DEFAULT_FLOOR_ID) -> None:
    """세션 결과+타임라인+객체별 d_i(t) 시계열 영속화 (FR-09·v1.4)."""
    payload = {"result": result.model_dump(),
               "timeline": [t.model_dump() for t in timeline],
               "person_series": person_series or {}}
    p = _sessions_dir(floor_id) / f"{result.session_id}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.rename(p)


def _load_saved(session_id: str, floor_id: str = DEFAULT_FLOOR_ID) -> dict | None:
    p = _sessions_dir(floor_id) / f"{session_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _latest_saved(floor_id: str = DEFAULT_FLOOR_ID) -> dict | None:
    # sessions/ 최상위만 스캔(하위 층 디렉토리 제외) — glob("*.json")로 충분
    files = sorted(_sessions_dir(floor_id).glob("*.json"),
                   key=lambda p: p.stat().st_mtime)
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


@app.post("/api/session/stop")
def session_stop(floor: str = DEFAULT_FLOOR_ID):
    fid = rt.resolve_floor(floor)
    eng = rt.engine_for(fid)
    if eng is None or eng.session_live() is None:
        raise HTTPException(404, "진행 중 세션 없음")
    result = eng.stop_session()
    rec = eng.detach_recorder()            # 녹화 마감 (계약 v1.10)
    if rec is not None:
        try:
            rec.close()
        except Exception:
            logger.exception("세션 녹화 종료 실패")
    _save_session(result, eng.session_timeline(),
                  eng.session_person_series(), floor_id=fid)
    return result


# ================================================================ 건물 드릴 (전 층 세션 — ADR 06)
def _floor_result(session_id: str, floor_id: str):
    """그 층의 EvaluationResult(dict) — 진행중이면 live, 아니면 저장본."""
    eng = rt.engine_for(floor_id)
    res = eng.session_result() if eng else None
    if res is not None and res.session_id == session_id:
        return res.model_dump()
    saved = _load_saved(session_id, floor_id)
    return saved["result"] if saved else None


def _r(v, n: int = 4):
    """CSV용 반올림 — None은 빈 칸."""
    return "" if v is None else round(v, n)


def _aggregate_floors(session_id: str, floors: list[tuple[str, dict]]) -> dict:
    """참여 층 결과 [(floor_id, result_dict)] → 건물 롤업 (ADR 06 §3): EPFI 전원평균·
    CBS 합·SEI 통합분포·IDR 구역별 나열 + 추가요약(총 통과·최대혼잡층·층별 개시시각).
    저장본/라이브/리플레이 결과 어디서든 공통으로 쓰는 순수 집계 코어."""
    epfis = [pm["epfi"] for _f, r in floors for pm in r.get("person_metrics", [])
             if pm.get("epfi") is not None]
    epfi_avg = sum(epfis) / len(epfis) if epfis else None
    cbs_total = sum(r.get("cbs_total", 0.0) for _f, r in floors)
    pairs = [(em.get("actual_count", 0), em["design_capacity"])
             for _f, r in floors for em in r.get("exit_metrics", [])
             if em.get("design_capacity")]
    sum_e = sum(e for e, _c in pairs); sum_c = sum(c for _e, c in pairs)
    sei = None
    if pairs and sum_e > 0 and sum_c > 0:
        sei = (1.0 - 0.5 * sum(abs(e / sum_e - c / sum_c) for e, c in pairs)) * 100.0
    total_passed = sum(em.get("actual_count", 0)
                       for _f, r in floors for em in r.get("exit_metrics", []))
    max_cbs_floor = (max(floors, key=lambda fr: fr[1].get("cbs_total", 0.0))[0]
                     if floors else None)
    floor_start = {}
    for f, r in floors:
        starts = [z["evacuation_start_at"] for z in r.get("zone_metrics", [])
                  if z.get("evacuation_start_at") is not None]
        floor_start[f] = min(starts) if starts else None
    alarm_ts = next((r.get("alarm_ts") for _f, r in floors), None)
    return {
        "session_id": session_id,
        "alarm_ts": alarm_ts,
        "floors": [f for f, _r in floors],
        "building": {"epfi_avg": epfi_avg, "cbs_total": round(cbs_total, 4), "sei": sei,
                     "idr_by_floor": {f: r.get("zone_metrics", []) for f, r in floors}},
        "summary": {"total_passed": total_passed, "max_cbs_floor": max_cbs_floor,
                    "floor_start_ts": floor_start},
        "per_floor": [{"floor_id": f, "result": r} for f, r in floors],
    }


def _drill_rollup(session_id: str) -> dict:
    """저장본/라이브에서 session_id로 참여 전 층 결과를 모아 건물 롤업."""
    floors = [(f, r) for f in rt.participating_floors()
              if (r := _floor_result(session_id, f)) is not None]
    return _aggregate_floors(session_id, floors)


@app.post("/api/drill/start")
async def drill_start(request: Request):
    """건물 드릴 시작 — 참여(카메라 매핑) 전 층에 경보 원점이 지정돼야 시작.
    body {floor_origins:{floor_id:[[x,y],…]}, t_alarm?}. 공유 t_alarm으로 전 층 동시 개시."""
    import time as _t
    body = await request.json()
    floor_origins = body.get("floor_origins") or {}
    part = rt.participating_floors()
    if not part:
        raise HTTPException(409, "카메라 매핑된 층이 없습니다 — 드릴 불가")
    missing = [f for f in part if not floor_origins.get(f)]
    if missing:
        raise HTTPException(409, {"msg": "경보 발생원 미지정 층 — 각 층에 경보 위치를 지정하세요",
                                  "missing_floors": missing})
    busy = [f for f in part if (rt.engine_for(f) and rt.engine_for(f).session_live() is not None)]
    if busy:
        raise HTTPException(409, {"msg": "이미 세션 진행 중인 층 — 먼저 종료하세요", "busy_floors": busy})
    t_alarm = float(body["t_alarm"]) if body.get("t_alarm") is not None else _t.time()
    floors = []
    for f in part:
        eng = rt.engine_for(f)
        live = eng.start_session(t_alarm=t_alarm,
                                 alarm_origins=[tuple(o) for o in floor_origins[f]])
        if SESSION_RECORD:
            try:
                _attach_recorder(eng, f, live)
            except Exception:
                logger.exception("드릴 녹화 부착 실패: %s", f)
        floors.append({"floor_id": f, "session": live.model_dump()})
    return {"session_id": floors[0]["session"]["session_id"], "alarm_ts": t_alarm,
            "floors": floors}


@app.post("/api/drill/stop", response_model=DrillResult)
def drill_stop():
    """건물 드릴 종료 — 참여 전 층 세션 일괄 finalize·저장 후 롤업 반환."""
    sid = None
    for f in rt.participating_floors():
        eng = rt.engine_for(f)
        if eng is None or eng.session_live() is None:
            continue
        result = eng.stop_session()
        rec = eng.detach_recorder()
        if rec is not None:
            try:
                rec.close()
            except Exception:
                logger.exception("드릴 녹화 종료 실패: %s", f)
        _save_session(result, eng.session_timeline(), eng.session_person_series(), floor_id=f)
        sid = result.session_id
    if sid is None:
        raise HTTPException(404, "진행 중인 드릴 없음")
    return _drill_rollup(sid)


@app.get("/api/drill/{session_id}/result", response_model=DrillResult)
def drill_result(session_id: str):
    roll = _drill_rollup(session_id)
    if not roll["per_floor"]:
        raise HTTPException(404, f"드릴 결과 없음: {session_id}")
    return roll


def _drill_session_ids() -> list[str]:
    """참여 층 전부에 공통 존재하는 session_id (= 건물 드릴), 최신순."""
    ids = None
    for f in rt.participating_floors():
        fids = {p.stem for p in _sessions_dir(f).glob("*.json")}
        ids = fids if ids is None else (ids & fids)
    return sorted(ids or [], reverse=True)


@app.get("/api/drills")
def drills_list():
    """드릴 이력 — 참여 층 전부에 공통 존재하는 session_id 기준(건물 롤업 요약)."""
    out = []
    for sid in _drill_session_ids():
        roll = _drill_rollup(sid)
        b = roll["building"]
        # 참여 각 층에 .db가 있으면 드릴 재계산 가능
        has_record = all(_session_db_path(sid, f).is_file() for f in roll["floors"]) \
            if roll["floors"] else False
        out.append({"session_id": sid, "alarm_ts": roll.get("alarm_ts"),
                    "floors": roll["floors"], "epfi_avg": b["epfi_avg"],
                    "cbs_total": b["cbs_total"], "sei": b["sei"],
                    "total_passed": roll["summary"]["total_passed"],
                    "has_record": has_record})
    return out


@app.get("/api/drill/{session_id}/export")
def drill_export(session_id: str, format: str = "json"):
    """건물 드릴 롤업 + 층별 상세를 JSON/CSV로 내보내기 (ADR 06 §4)."""
    roll = _drill_rollup(session_id)
    if not roll["per_floor"]:
        raise HTTPException(404, f"드릴 결과 없음: {session_id}")
    if format == "json":
        return DrillResult.model_validate(roll)
    # CSV — 건물 요약 1행 + 층별 상세 N행
    import csv as _csv
    import io as _io
    b, s = roll["building"], roll["summary"]
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["scope", "floor_id", "sei", "epfi_avg", "cbs_total",
                "passed", "zones_started", "zones_total"])
    zst = sum(1 for zs in b["idr_by_floor"].values() for z in zs if z.get("status") == "started")
    ztot = sum(len(zs) for zs in b["idr_by_floor"].values())
    w.writerow(["building", "", _r(b["sei"]), _r(b["epfi_avg"]), _r(b["cbs_total"]),
                s["total_passed"], zst, ztot])
    for pf in roll["per_floor"]:
        r = pf["result"]
        passed = sum(em.get("actual_count", 0) for em in r.get("exit_metrics", []))
        zm = r.get("zone_metrics", [])
        started = sum(1 for z in zm if z.get("status") == "started")
        w.writerow(["floor", pf["floor_id"], _r(r.get("sei")), _r(r.get("epfi_avg")),
                    _r(r.get("cbs_total")), passed, started, len(zm)])
    return Response(content=buf.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="drill_{session_id}.csv"'})


@app.post("/api/drill/{session_id}/replay")
async def drill_replay(session_id: str, request: Request):
    """건물 드릴 재계산 — 참여 각 층의 녹화 db를 같은 오버라이드로 리플레이하고
    재산출된 층별 결과로 건물 롤업을 다시 만든다 (ADR 06 Phase 3, 계약 v1.11).

    body(모두 선택): {thresholds, rho_crit, bottlenecks:{id:{...}},
      exits:{id:{...}}, fps(재생 격자, 기본 5)}. 반환: 재산출 DrillResult +
      frames_by_floor(층별 2D 재생 프레임) + site_by_floor(층별 배경 공간요소)."""
    import anyio
    from system.metrics.replay import run_replay
    try:
        body = await request.json()
    except Exception:
        body = {}
    overrides = {k: body[k] for k in ("thresholds", "rho_crit", "bottlenecks", "exits")
                 if k in body and body[k] is not None}
    fps = float(body.get("fps", 5.0))

    part = rt.participating_floors()
    dbs = [(f, _session_db_path(session_id, f)) for f in part]
    dbs = [(f, db) for f, db in dbs if db.is_file()]
    if not dbs:
        raise HTTPException(404, f"드릴 녹화 없음: {session_id} — 녹화 이후 드릴만 재계산 가능")

    floors: list[tuple[str, dict]] = []
    frames_by_floor: dict[str, list] = {}
    site_by_floor: dict[str, dict] = {}
    for f, db in dbs:
        result, _timeline, frames, meta = await anyio.to_thread.run_sync(
            run_replay, db, overrides, fps)
        floors.append((f, result.model_dump()))
        frames_by_floor[f] = frames
        site_by_floor[f] = meta.get("site_view")
    roll = _aggregate_floors(session_id, floors)
    return {"drill": DrillResult.model_validate(roll).model_dump(),
            "frames_by_floor": frames_by_floor, "site_by_floor": site_by_floor}


@app.get("/api/session")
def session_get(floor: str = DEFAULT_FLOOR_ID):
    eng = rt.engine_for(floor)
    live = eng.session_live() if eng else None
    if live is None:
        raise HTTPException(404, "진행 중 세션 없음")
    return live


@app.get("/api/session/result")
def session_result(floor: str = DEFAULT_FLOOR_ID):
    fid = rt.resolve_floor(floor)
    eng = rt.engine_for(fid)
    res = eng.session_result() if eng else None
    if res is not None:
        return res
    saved = _latest_saved(fid)                  # 재시작 후에도 마지막 결과 유지
    if saved is not None:
        return saved["result"]
    raise HTTPException(404, "산출된 세션 결과 없음")


@app.get("/api/session/timeline")
def session_timeline(floor: str = DEFAULT_FLOOR_ID):
    fid = rt.resolve_floor(floor)
    eng = rt.engine_for(fid)
    tl = eng.session_timeline() if eng else []
    if tl:
        return tl
    saved = _latest_saved(fid)
    return saved["timeline"] if saved else []


@app.get("/api/session/person_series")
def session_person_series(floor: str = DEFAULT_FLOOR_ID):
    """객체별 d_i(t) 시계열 — 진행 중이면 현재까지, 종료 후엔 마지막/저장본 (v1.4)."""
    fid = rt.resolve_floor(floor)
    eng = rt.engine_for(fid)
    ps = eng.session_person_series() if eng else {}
    if ps:
        return ps
    saved = _latest_saved(fid)
    return saved.get("person_series", {}) if saved else {}


@app.get("/api/sessions")
def sessions_list(floor: str = DEFAULT_FLOOR_ID):
    """세션 이력 목록 (요약) — 저장 파일 기반, 최신순 (계약 v1.3)."""
    out = []
    for p in sorted(_sessions_dir(rt.resolve_floor(floor)).glob("*.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))["result"]
        except (json.JSONDecodeError, KeyError):
            continue
        rec = {k: r.get(k) for k in
               ("session_id", "alarm_ts", "ended_at", "sei", "epfi_avg", "cbs_total")}
        sid = rec.get("session_id")
        rec["has_record"] = bool(sid) and _session_db_path(
            sid, rt.resolve_floor(floor)).is_file()
        out.append(rec)
    return out


@app.get("/api/sessions/{session_id}")
def sessions_get(session_id: str, floor: str = DEFAULT_FLOOR_ID):
    saved = _load_saved(session_id, rt.resolve_floor(floor))
    if saved is None:
        raise HTTPException(404, f"세션 없음: {session_id}")
    saved["has_record"] = _session_db_path(
        session_id, rt.resolve_floor(floor)).is_file()
    return saved


@app.post("/api/session/{session_id}/replay")
async def session_replay(session_id: str, request: Request,
                         floor: str = DEFAULT_FLOOR_ID):
    """저장 세션 리플레이·지표 재계산 (계약 v1.10).

    녹화 db를 세션 당시 도면 스냅샷 위에서 헤드리스 재생 → 2D 재생 프레임 +
    (thresholds 오버라이드 시) 재산출 4대 지표. 도면·호모그래피는 그대로 —
    임계값만 바꿔 '역방향 재파라미터화'. 원본 저장물은 절대 변경하지 않는다.

    body(모두 선택): {thresholds:{v_th,a_th,r_th,dt_hold,d_allow,min_conf,q_design},
      rho_crit(전역 병목 임계), bottlenecks:{id:{rho_crit,weight}},
      exits:{id:{width_m,q_design,design_capacity}} — 폭·기준을 주면 C_j를
      다시 파생하고, design_capacity를 직접 주면 그 값이 최종(v1.12),
      fps(재생 샘플 격자, 기본 5)}
    """
    fid = rt.resolve_floor(floor)
    db = _session_db_path(session_id, fid)
    if not db.is_file():
        raise HTTPException(404, f"녹화 없음: {session_id} — 녹화 이후 세션만 재생 가능")
    try:
        body = await request.json()
    except Exception:
        body = {}
    overrides = {k: body[k] for k in ("thresholds", "rho_crit", "bottlenecks", "exits")
                 if k in body and body[k] is not None}
    fps = float(body.get("fps", 5.0))

    import anyio  # replay는 CPU 바운드 → 워커 스레드로 오프로드 (이벤트루프 보호)
    from system.metrics.replay import run_replay
    result, timeline, frames, meta = await anyio.to_thread.run_sync(
        run_replay, db, overrides, fps)
    return {
        "result": result.model_dump(),
        "timeline": [t.model_dump() for t in timeline],
        "frames": frames,
        "site": meta.get("site_view"),        # 세션 당시 공간요소(배경 렌더용)
        "meta": {k: meta.get(k) for k in
                 ("session_id", "floor_id", "alarm_ts", "alarm_origins",
                  "site_version", "call_count", "track_row_count")},
    }


@app.get("/api/session/export")
def session_export(format: str = "json", floor: str = DEFAULT_FLOOR_ID):
    from system.contracts import EvaluationResult
    fid = rt.resolve_floor(floor)
    eng = rt.engine_for(fid)
    res = eng.session_result() if eng else None
    if res is None:                              # 재시작 후엔 저장본으로
        saved = _latest_saved(fid)
        if saved is None:
            raise HTTPException(404, "산출된 세션 결과 없음")
        res = EvaluationResult.model_validate(saved["result"])
    if format == "json":
        return Response(res.model_dump_json(indent=2),
                        media_type="application/json",
                        headers={"Content-Disposition":
                                 f"attachment; filename={res.session_id}.json"})
    if format == "csv":  # 지표별 평탄화 CSV (FR-09)
        import io, csv as _csv
        buf = io.StringIO()
        w = _csv.writer(buf)
        w.writerow(["kind", "id", "k", "v"])
        for zm in res.zone_metrics:
            for k, v in zm.model_dump().items():
                w.writerow(["zone", zm.zone_id, k, v])
        for pm in res.person_metrics:
            for k, v in pm.model_dump().items():
                w.writerow(["person", pm.global_track_id, k, v])
        for bm in res.bottleneck_metrics:
            for k, v in bm.model_dump().items():
                w.writerow(["bottleneck", bm.bottleneck_id, k, v])
        for bg in res.bottleneck_groups:            # CBS 그룹 집계 (v1.12)
            for k, v in bg.model_dump().items():
                w.writerow(["bottleneck_group", bg.group, k, v])
        for em in res.exit_metrics:
            for k, v in em.model_dump().items():
                w.writerow(["exit", em.exit_id, k, v])
        w.writerow(["summary", res.session_id, "sei", res.sei])
        w.writerow(["summary", res.session_id, "epfi_avg", res.epfi_avg])
        w.writerow(["summary", res.session_id, "cbs_total", res.cbs_total])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition":
                                 f"attachment; filename={res.session_id}.csv"})
    raise HTTPException(422, "format은 json|csv")


# ============================================== 훈련영상 동기 송출 (ADR 08)
# 리허설·시연용 별도 도구. 본체(추론·추적·지표)는 건드리지 않는다 — 켜지 않으면
# 아무 일도 일어나지 않고, 켜는 동안만 RTSP 경로의 송출 주체가 바뀐다.


@app.get("/api/vsource/scenarios")
def vsource_scenarios():
    """시나리오 목록 + 검증 결과.

    영상(존재·길이·fps·코덱)뿐 아니라 **경로를 보는 카메라**(등록·활성·매핑)까지
    확인한다 — 송출만 해서는 운영 뷰에 아무것도 안 뜨기 때문이다.
    """
    return {"scenarios": [s.to_dict() for s in vscenario.load_all(cameras=rt.cameras())],
            "media_dir": str(vscenario.MEDIA_DIR),
            "scenario_dir": str(vscenario.SCENARIO_DIR)}


@app.post("/api/vsource/start")
async def vsource_start(request: Request):
    """동시 송출 시작 — body {scenario_id, loop?}.

    전 채널이 공통 T0에 함께 t=0부터 시작한다(실측 편차 1.7ms). 같은 RTSP 경로를
    점유 중인 pm2 송출은 자동으로 내린다(경로는 배타적).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    sid = str(body.get("scenario_id") or "").strip()
    if not sid:
        raise HTTPException(422, "scenario_id 필요")
    try:
        return vsource.start(sid, loop=bool(body.get("loop", True)))
    except FileNotFoundError:
        raise HTTPException(404, f"시나리오 없음: {sid}")
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/vsource/stop")
async def vsource_stop(request: Request):
    """송출 정지 — body {restore_pm2?} (미지정 시 환경변수 기본값)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    rp = body.get("restore_pm2")
    return vsource.stop(restore_pm2=None if rp is None else bool(rp))


@app.get("/api/vsource/status")
def vsource_status():
    """송출 상태 — t0·사이클 위치·다음 사이클까지 남은 시간·채널별 송출 여부."""
    return vsource.status()


# ================================================================ 맵 상태
def _map_state(floor_id: str = DEFAULT_FLOOR_ID) -> MapState:
    """해당 층 엔진 스냅샷 + 그 층 소속 카메라 상태만 병합 (v1.7)."""
    fid = rt.resolve_floor(floor_id)
    eng = rt.engines.get(fid)
    ms = eng.snapshot() if eng is not None else MapState(ts=0.0)
    ms.cameras = [s for s in rt.ingest.states()
                  if rt._cam_floor.get(s.cam_id, DEFAULT_FLOOR_ID) == fid]
    return ms


@app.get("/api/map/state")
def map_state(floor: str = DEFAULT_FLOOR_ID):
    return _map_state(floor)


@app.get("/api/map/stream")
async def map_stream(floor: str = DEFAULT_FLOOR_ID):
    async def gen():
        while True:
            payload = _map_state(floor).model_dump_json()
            yield f"event: state\ndata: {payload}\n\n"
            await asyncio.sleep(1.0)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.get("/api/debug/tracks")
def debug_tracks(floor: str = DEFAULT_FLOOR_ID):
    """트래커 foot_uv 진단 — 좌표가 카메라 원본 해상도 범위 안인지 확인."""
    eng = rt.engine_for(floor)
    if eng is None:
        return {"error": "엔진 없음"}
    import numpy as np, cv2
    cams = {c.cam_id: c for c in rt.cameras()}
    workers = getattr(rt.ingest, "_workers", {})
    foot_dbg = eng._debug_foot   # gid -> {foot_u/v, map_x/y}
    result = []
    for gid, d in foot_dbg.items():
        cam_id = gid.rsplit(":", 1)[0]
        worker = workers.get(cam_id)
        cam_w = getattr(worker, "width", "?") if worker else "?"
        cam_h = getattr(worker, "height", "?") if worker else "?"
        cam_cfg = cams.get(cam_id)
        # cctv_pts 커버리지
        cov = None
        if cam_cfg and cam_cfg.mapping:
            cctv = cam_cfg.mapping.cctv_pts
            u_min = min(p[0] for p in cctv); u_max = max(p[0] for p in cctv)
            v_min = min(p[1] for p in cctv); v_max = max(p[1] for p in cctv)
            u, v = d["foot_u"], d["foot_v"]
            in_cov = (u_min <= u <= u_max and v_min <= v <= v_max)
            cov = f"cctv_pts u[{u_min:.0f}~{u_max:.0f}] v[{v_min:.0f}~{v_max:.0f}] → {'IN' if in_cov else '⚠OUT'}"
        result.append({
            "gid": gid, "cam": f"{cam_w}×{cam_h}",
            "foot_u": d["foot_u"], "foot_v": d["foot_v"],
            "map_x": d["map_x"], "map_y": d["map_y"],
            "coverage": cov,
        })
    result.sort(key=lambda r: r["gid"])
    return {"objects": result}


@app.get("/api/status")
def status():
    try:
        if rt.analyzer is not None:
            pipeline = rt.analyzer.stats()
        elif INGEST_BACKEND == "deepstream":
            # 추론 통계는 워커 컨테이너 로그(STATS) 소관 — 여기는 브리지 수신 통계
            bridge = getattr(rt.ingest, "bridge", None)
            pipeline = {"tracking": "deepstream",
                        **(bridge.stats() if bridge is not None else {})}
        else:
            pipeline = {"tracking": "disabled"}
        return {
            "backend": INGEST_BACKEND,
            "pipeline": pipeline,
            "queue": {"size": rt.queue.qsize(), "drops": rt.queue.dropped},
            "cameras": [s.model_dump() for s in rt.ingest.states()],
        }
    except Exception as e:  # 원인 노출 (임시 디버그 겸 방어)
        logger.exception("/api/status 실패")
        raise HTTPException(500, f"status 수집 실패: {type(e).__name__}: {e}")


# ================================================================ RTSP 미리보기
# 등록 전 임의 주소를 **추론까지 돌려** 눈으로 확인하는 기능. probe 는 연결
# 여부만 보고 스냅샷은 정지화면이라, "탐지가 실제로 되나"를 못 본다.
#
# 추론은 API 프로세스에 올리지 않는다(운영 API 는 77MB — torch/TRT 를 얹으면
# GB 단위로 늘고 기동이 느려진다). 필요할 때만 워커 subprocess 를 띄우고
# 결과 파일을 중계한다. 동시 1개, 저장 없음, 정지하면 흔적이 남지 않는다.

class _Preview:
    """미리보기 워커 수명주기 — 동시 1개."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.dir: Path | None = None
        self.rtsp = ""
        self.started = 0.0

    @staticmethod
    def mask(url: str) -> str:
        """계정·비밀번호 가림 — 화면·로그에 남기지 않는다."""
        if "://" in url and "@" in url.split("://", 1)[1].split("/", 1)[0]:
            scheme, rest = url.split("://", 1)
            return f"{scheme}://***:***@{rest.split('@', 1)[1]}"
        return url

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, rtsp: str, fps: float, max_sec: float) -> None:
        self.stop()
        self.dir = Path(tempfile.mkdtemp(prefix="macs_preview_"))
        self.rtsp = rtsp
        self.started = time.time()
        # 워커 출력은 버리지 않고 파일로 남긴다 — 버리면 실패 원인을 알 수 없다.
        log = open(self.dir / "worker.log", "wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "system.api.preview_worker",
             "--rtsp", rtsp, "--out", str(self.dir),
             "--fps", str(fps), "--max-sec", str(max_sec)],
            cwd=str(Path(__file__).resolve().parents[2]),
            stdout=log, stderr=subprocess.STDOUT)

    def stop(self) -> None:
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=6)
            except Exception:
                self.proc.kill()
        self.proc = None
        if self.dir is not None:
            shutil.rmtree(self.dir, ignore_errors=True)   # 흔적 없이
            self.dir = None

    def status(self) -> dict:
        base = {"running": False, "rtsp": self.mask(self.rtsp), "stage": "대기",
                "w": 0, "h": 0, "src_fps": 0.0, "fps": 0.0, "det": 0.0,
                "tracks": 0, "frames": 0, "error": "", "first_latency": None,
                "profile": ""}
        if self.dir is None:
            return base
        f = self.dir / "status.json"
        from_file = None
        if f.is_file():
            try:
                from_file = json.loads(f.read_text())
                base.update(from_file)
            except Exception:
                from_file = None
        base["rtsp"] = self.mask(self.rtsp)
        # 워커가 뜨는 데 시간이 걸린다(엔진 로드). 그 사이 status.json 이 아직
        # 없으면 "아직 시작 중"이지 "끝난 것"이 아니다. base 에 이미
        # running:False 가 있어 .get(...,True) 의 기본값이 먹지 않으므로
        # 파일 유무를 명시적으로 갈라야 한다.
        base["running"] = self.alive() and (
            bool(from_file.get("running")) if from_file is not None else True)
        if self.alive() and from_file is None:
            base["stage"] = "시작 중"
        if not self.alive() and not base.get("error"):
            # 워커가 상태를 못 남기고 죽은 경우 — 로그 꼬리를 사유로 올린다
            lg = self.dir / "worker.log"
            if lg.is_file():
                tail = lg.read_text(errors="replace").strip().splitlines()[-3:]
                if tail:
                    base["error"] = " / ".join(t[:120] for t in tail)
        if not self.alive() and not base.get("error"):
            base["stage"] = "정지"
        return base

    def frame(self) -> bytes | None:
        if self.dir is None:
            return None
        f = self.dir / "frame.jpg"
        try:
            return f.read_bytes() if f.is_file() else None
        except Exception:
            return None


_preview = _Preview()


def _sweep_preview_tmp() -> None:
    """서버 기동 시 남은 미리보기 임시 디렉터리 청소.

    미리보기 도중 API 가 재시작되면 stop() 이 못 돌아 /tmp/macs_preview_* 가
    영구히 남는다(실측 확인). 기동 때 한 번 쓸어 준다 — 이 시점엔 살아 있는
    세션이 있을 수 없다.
    """
    for d in Path(tempfile.gettempdir()).glob("macs_preview_*"):
        shutil.rmtree(d, ignore_errors=True)


_sweep_preview_tmp()


@app.post("/api/preview/start")
async def preview_start(payload: dict = None):
    """임의 RTSP 를 추론까지 돌려 미리보기 — 등록·저장 없음."""
    body = payload or {}
    rtsp = (body.get("rtsp") or "").strip()
    if not rtsp.lower().startswith(("rtsp://", "rtsps://", "http://", "https://")):
        raise HTTPException(422, "rtsp:// 로 시작하는 주소를 넣으세요")
    _preview.start(rtsp, float(body.get("fps") or 5.0),
                   float(body.get("max_sec") or 600.0))
    return {"ok": True, "rtsp": _Preview.mask(rtsp)}


@app.post("/api/preview/stop")
def preview_stop():
    _preview.stop()
    return {"ok": True}


@app.get("/api/preview/status")
def preview_status():
    return _preview.status()


@app.get("/api/preview/frame")
def preview_frame():
    f = _preview.frame()
    if f is None:
        raise HTTPException(404, "프레임 없음")
    return Response(f, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


# ============================================================ 추론 프로파일
# 검출기·ReID·트래커 조합(model_zoo.py)을 UI에서 갈아끼운다. 실제 반영은 추론
# 계층 재기동 시점이라, 선택 저장 → 인제스트/분석 스레드 재기동까지 한 번에 한다.

def _infer_ds() -> bool:
    return INGEST_BACKEND == "deepstream"


def _restart_inference() -> str:
    """추론 계층만 재기동 — 사이트 설정·세션 녹화본은 건드리지 않는다."""
    if _infer_ds():
        rt.ingest.stop()
        rt.ingest.start(rt.cameras())
        return "DeepStream 워커 재기동"
    if rt.analyzer is not None:
        rt.analyzer.stop()
        rt.analyzer = None
    from system.tracking.analyzer import AnalyzerThread
    rt.analyzer = AnalyzerThread(
        rt.queue, on_tracks=rt._dispatch_tracks,
        camera_fps={c.cam_id: c.analyze_fps for c in rt.cameras()})
    rt.analyzer.start()
    return "분석 스레드 재기동"


@app.get("/api/infer/profiles")
def infer_profiles():
    """선택 가능한 추론 프로파일 목록 + 현재 백엔드."""
    return {"backend": INGEST_BACKEND,
            "current": model_zoo.current_id(),
            "profiles": model_zoo.describe(ds=_infer_ds())}


@app.post("/api/infer/profile")
async def set_infer_profile(request: Request):
    """프로파일 전환 — 저장 후 추론 계층 재기동.

    세션 중 전환은 막는다: 같은 세션 안에서 앞뒤 구간이 서로 다른 검출기로
    계산되면 4대 지표를 신뢰할 수 없다(맵·구역 변경을 막는 것과 같은 이유).
    """
    body = await request.json()
    pid = str(body.get("profile") or "").strip()
    live = [fid for fid, eng in rt.engines.items() if eng.session_live() is not None]
    if live:
        raise HTTPException(409, f"평가 세션 진행 중({', '.join(live)}) — "
                                 "세션을 종료한 뒤 추론 모델을 바꾸세요")
    try:
        prof = model_zoo.resolve(pid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    missing = model_zoo.missing_engines(prof, ds=_infer_ds())
    if missing:
        raise HTTPException(400, "TRT 엔진이 없습니다 — tools/build_trt_engine.py 로 "
                                 f"먼저 빌드하세요: {', '.join(missing)}")
    model_zoo.select(prof.id)
    try:
        how = _restart_inference()
    except Exception as e:
        logger.exception("추론 계층 재기동 실패")
        raise HTTPException(500, f"프로파일은 저장됐으나 재기동 실패 — {e}")
    logger.info("추론 프로파일 전환: %s (%s)", prof.id, how)
    return {"ok": True, "profile": prof.id, "label": prof.label, "restarted": how}


# ================================================================ 프론트
@app.get("/")
def index():
    return FileResponse(FRONT_DIR / "index.html")


@app.middleware("http")
async def _static_no_cache(request: Request, call_next):
    """정적 파일 캐시 재검증 강제 — JS/CSS 갱신이 브라우저 캐시에 씹히는 문제 방지.
    (no-cache = 매 요청 재검증. Last-Modified 기반 304라 비용은 미미)"""
    resp = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# webui/static 전체 마운트 — main/ 하위 + 기존 디자인 토큰(colors_and_type.css) 공유
app.mount("/static", StaticFiles(directory=str(FRONT_DIR.parent)), name="static")
