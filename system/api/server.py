"""멀티카메라 시스템 실서버 — CONTRACT §4 (mock_server.py와 동일 계약).

배선: IngestManager(ffmpeg-NVDEC) → FrameQueue → AnalyzerThread(TRT 공유)
      → MetricsEngine.on_tracks → MapState → REST/SSE → main 탭 canvas.

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

from system.config.schema import CameraConfig, CameraMapping, MapSpec, SiteConfig
from system.config.store import SiteStore
from system.contracts import MapState
from system.ingest.frame_queue import FrameQueue
from system.ingest.manager import IngestManager
from system.metrics.engine import MetricsEngine

logger = logging.getLogger("system.api")

SITE_ID = os.environ.get("SITE_ID", "default")
SITE_ROOT = os.environ.get("SITE_ROOT", "data/sites")
GPU_DEVICES = [int(x) for x in os.environ.get("GPU_DEVICES", "0,1").split(",") if x != ""]
FRONT_DIR = Path(__file__).resolve().parents[2] / "webui" / "static" / "main"


class Runtime:
    """서버 수명 동안의 파이프라인 상태 (설정 변경 시 reload)."""

    def __init__(self) -> None:
        self.store = SiteStore(SITE_ROOT)
        self.queue = FrameQueue(maxsize=64)
        self.ingest = IngestManager(self.queue, gpu_devices=GPU_DEVICES or None)
        self.engine: MetricsEngine | None = None
        self.analyzer = None  # AnalyzerThread | None — TRT 로드 실패 시 None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 설정 접근
    def site(self) -> SiteConfig:
        cfg = self.store.load_site(SITE_ID)
        if cfg is None:
            cfg = self.store.save_site(SiteConfig(site_id=SITE_ID), bump_version=False)
        return cfg

    def cameras(self) -> list[CameraConfig]:
        return self.store.list_cameras(SITE_ID)

    def reload_engine(self) -> None:
        with self._lock:
            if self.engine is not None:
                self.engine.reload(self.site(), self.cameras())

    # ------------------------------------------------------------ 수명주기
    def startup(self) -> None:
        site, cams = self.site(), self.cameras()
        self.engine = MetricsEngine(site, cams)
        self.ingest.start(cams)

        try:  # TRT 미가용 환경에서도 API/UI는 동작
            from system.tracking.analyzer import AnalyzerThread
            self.analyzer = AnalyzerThread(
                self.queue,
                on_tracks=self.engine.on_tracks,
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

    def next_cam_id(self) -> str:
        nums = [int(c.cam_id[3:]) for c in self.cameras()
                if c.cam_id.startswith("cam") and c.cam_id[3:].isdigit()]
        return f"cam{(max(nums) + 1 if nums else 1):02d}"

    # ------------------------------------------------------------ 스냅샷
    def grab_frame(self, cfg: CameraConfig) -> np.ndarray | None:
        """실행 중 워커 프레임 우선, 없으면 1회성 캡처(test/미기동 카메라용)."""
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


@app.put("/api/site")
async def put_site(request: Request):
    body = await request.json()
    body["site_id"] = SITE_ID  # site_id 고정
    prev = rt.store.load_site(SITE_ID)
    if prev is not None and "map" not in body:
        body["map"] = prev.map.model_dump() if prev.map else None
    cfg = SiteConfig.model_validate(body)
    cfg = rt.store.save_site(cfg)
    rt.reload_engine()
    return cfg


@app.post("/api/site/map")
async def post_site_map(image: UploadFile = File(...), meta: str | None = Form(None)):
    data = await image.read()
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise HTTPException(422, "이미지 디코드 실패")
    site_dir = rt.store.site_dir(SITE_ID)
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "map.png").write_bytes(data)

    m_per_px = None
    if meta:  # cad-convert 메타 JSON — m_per_px 자동 (계약 A-6)
        try:
            m_per_px = float(json.loads(meta).get("m_per_px"))
        except (ValueError, TypeError, json.JSONDecodeError):
            raise HTTPException(422, "meta JSON에서 m_per_px를 읽지 못함")

    cfg = rt.site()
    prev_scale = cfg.map.scale if cfg.map else None  # 재업로드 시 기존 축척 유지
    cfg.map = MapSpec(image="map.png", w=arr.shape[1], h=arr.shape[0],
                      scale=prev_scale, m_per_px=m_per_px)
    rt.store.save_site(cfg)
    rt.reload_engine()
    return cfg.map


@app.get("/api/site/map")
def get_site_map():
    p = rt.store.site_dir(SITE_ID) / "map.png"
    if not p.is_file():
        raise HTTPException(404, "맵 이미지 없음")
    return FileResponse(p, media_type="image/png")


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
    cfg = CameraConfig(cam_id=rt.next_cam_id(), name=body.get("name", ""),
                       rtsp=body["rtsp"], analyze_fps=body.get("analyze_fps", 5.0))
    rt.store.save_camera(SITE_ID, cfg)
    rt.ingest.add_camera(cfg)
    if rt.analyzer is not None:
        rt.analyzer.set_camera_fps(cfg.cam_id, cfg.analyze_fps)
    rt.reload_engine()
    return cfg


@app.put("/api/cameras/{cam_id}")
async def update_camera(cam_id: str, request: Request):
    old = _cam_or_404(cam_id)
    patch = await request.json()
    patch.pop("cam_id", None)
    cfg = CameraConfig.model_validate({**old.model_dump(), **patch})
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
    H, _ = cv2.findHomography(np.array(cctv_pts, np.float64), np.array(map_pts, np.float64))
    if H is None:
        raise HTTPException(422, "호모그래피 산출 실패 — 대응점이 퇴화 배치")
    cfg.mapping = CameraMapping(cctv_pts=cctv_pts, map_pts=map_pts,
                                H=[float(v) for v in H.reshape(-1)])
    # valid_roi는 요청 값으로 전체 교체 — null/3점 미만 = ROI 제거 (계약 v1.3)
    roi = body.get("valid_roi")
    cfg.valid_roi = roi if (roi and len(roi) >= 3) else None
    rt.store.save_camera(SITE_ID, cfg)
    rt.reload_engine()
    return cfg


# ================================================================ 평가 세션 (계약 v1.2)
@app.post("/api/session/start")
async def session_start(request: Request):
    if rt.engine is None:
        raise HTTPException(503, "엔진 미기동")
    body = await request.json()
    if rt.engine.session_live() is not None:
        raise HTTPException(409, "세션 진행 중 — 먼저 종료하세요")
    origin = tuple(body["origin"])
    return rt.engine.start_session(origin, t_alarm=body.get("t_alarm"))


def _sessions_dir() -> Path:
    d = rt.store.site_dir(SITE_ID) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_session(result, timeline) -> None:
    """세션 결과+타임라인 영속화 (FR-09) — data/sites/<site>/sessions/<id>.json."""
    payload = {"result": result.model_dump(),
               "timeline": [t.model_dump() for t in timeline]}
    p = _sessions_dir() / f"{result.session_id}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.rename(p)


def _load_saved(session_id: str) -> dict | None:
    p = _sessions_dir() / f"{session_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _latest_saved() -> dict | None:
    files = sorted(_sessions_dir().glob("*.json"),
                   key=lambda p: p.stat().st_mtime)
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


@app.post("/api/session/stop")
def session_stop():
    if rt.engine is None or rt.engine.session_live() is None:
        raise HTTPException(404, "진행 중 세션 없음")
    result = rt.engine.stop_session()
    _save_session(result, rt.engine.session_timeline())
    return result


@app.get("/api/session")
def session_get():
    live = rt.engine.session_live() if rt.engine else None
    if live is None:
        raise HTTPException(404, "진행 중 세션 없음")
    return live


@app.get("/api/session/result")
def session_result():
    res = rt.engine.session_result() if rt.engine else None
    if res is not None:
        return res
    saved = _latest_saved()                     # 재시작 후에도 마지막 결과 유지
    if saved is not None:
        return saved["result"]
    raise HTTPException(404, "산출된 세션 결과 없음")


@app.get("/api/session/timeline")
def session_timeline():
    tl = rt.engine.session_timeline() if rt.engine else []
    if tl:
        return tl
    saved = _latest_saved()
    return saved["timeline"] if saved else []


@app.get("/api/sessions")
def sessions_list():
    """세션 이력 목록 (요약) — 저장 파일 기반, 최신순 (계약 v1.3)."""
    out = []
    for p in sorted(_sessions_dir().glob("*.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))["result"]
        except (json.JSONDecodeError, KeyError):
            continue
        out.append({k: r.get(k) for k in
                    ("session_id", "alarm_ts", "ended_at", "sei", "epfi_avg", "cbs_total")})
    return out


@app.get("/api/sessions/{session_id}")
def sessions_get(session_id: str):
    saved = _load_saved(session_id)
    if saved is None:
        raise HTTPException(404, f"세션 없음: {session_id}")
    return saved


@app.get("/api/session/export")
def session_export(format: str = "json"):
    from system.contracts import EvaluationResult
    res = rt.engine.session_result() if rt.engine else None
    if res is None:                              # 재시작 후엔 저장본으로
        saved = _latest_saved()
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


# ================================================================ 맵 상태
def _map_state() -> MapState:
    ms = rt.engine.snapshot() if rt.engine is not None else MapState(ts=0.0)
    ms.cameras = rt.ingest.states()
    return ms


@app.get("/api/map/state")
def map_state():
    return _map_state()


@app.get("/api/map/stream")
async def map_stream():
    async def gen():
        while True:
            payload = _map_state().model_dump_json()
            yield f"event: state\ndata: {payload}\n\n"
            await asyncio.sleep(1.0)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.get("/api/status")
def status():
    try:
        return {
            "pipeline": rt.analyzer.stats() if rt.analyzer is not None else {"tracking": "disabled"},
            "queue": {"size": rt.queue.qsize(), "drops": rt.queue.dropped},
            "cameras": [s.model_dump() for s in rt.ingest.states()],
        }
    except Exception as e:  # 원인 노출 (임시 디버그 겸 방어)
        logger.exception("/api/status 실패")
        raise HTTPException(500, f"status 수집 실패: {type(e).__name__}: {e}")


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
