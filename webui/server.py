"""Standalone live tracking web UI (FastAPI + MJPEG) with a speed dashboard.

Separate from the core pipeline — reuses `src.inference_gpu` and `webui.speed`.

Flow:
  1. POST /upload         -> save video, return {job_id, width, height,
                             first_frame (base64 jpg)}  (no inference yet)
  2. Client picks an optional 4-point ROI + a calibration line on the first
     frame, then POST /start/{job_id} {roi, pixels_per_meter}.
  3. A worker runs TRT inference frame-by-frame; for each frame it estimates
     per-object speed, draws the overlay, writes the result mp4, JPEG-encodes
     for the live stream, and snapshots dashboard metrics onto the job.
  4. GET /stream/{job_id} shows frames live, then loops the result.
  5. GET /status/{job_id} feeds the dashboard (count / speeds / ...).
"""
import os
import sys
import time
import queue
import uuid
import base64
import asyncio
import threading
import subprocess
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, UploadFile, HTTPException, Body
from fastapi.responses import (
    StreamingResponse, HTMLResponse, FileResponse, JSONResponse,
)
from fastapi.staticfiles import StaticFiles

# Make the project root importable so we can reuse the existing pipeline.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.inference_gpu import BoostTrackGPUInference   # noqa: E402
from webui.speed import SpeedEstimator, annotate       # noqa: E402
from webui.counter import LineCounter                   # noqa: E402
from webui import depth_ground                          # noqa: E402
from webui.basic_viz import draw_basic                   # noqa: E402

BASE = Path(__file__).resolve().parent
UPLOAD_DIR = BASE / "_data" / "uploads"
OUTPUT_DIR = BASE / "_data" / "outputs"
INDEX_HTML = BASE / "index.html"

# Depth mode runs Depth-Anything-3 in its own conda env (separate deps).
DA3_PYTHON = os.environ.get(
    "DA3_PYTHON", os.path.expanduser("~/miniconda3/envs/da3/bin/python"))
DA3_SCRIPT = str(BASE / "da3_depth.py")

app = FastAPI(title="BoostTrack Live UI")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

# Stream tuning. Smaller JPEGs keep the MJPEG stream at full source fps
# (large frames become transport-bound). The saved mp4 keeps full resolution.
STREAM_MAX_WIDTH = 854
JPEG_QUALITY = 72

_model: BoostTrackGPUInference | None = None
# One shared tracker instance with mutable per-video state -> serialize jobs.
_model_lock = threading.Lock()
_jobs: dict[str, "Job"] = {}


class Job:
    def __init__(self, job_id: str, input_path: Path):
        self.id = job_id
        self.input_path = input_path
        self.output_path = OUTPUT_DIR / f"{job_id}.mp4"
        self.queue: "queue.Queue[bytes | None]" = queue.Queue(maxsize=128)
        self.replay_frames: list[bytes] = []   # all JPEGs, for loop replay
        self.replay_metrics: list[dict] = []   # per-frame metrics, for replay sync
        self.status = "uploaded"   # uploaded | queued | processing | done | error
        self.processed = 0
        self.total = 0
        self.fps = 0.0
        self.error: str | None = None
        self.live = False        # RTSP/live source (unbounded, stop-controlled)
        self.stop = False        # set by /stop to end a live job
        # speed config (set by /start)
        self.roi: list | None = None
        self.ppm: float | None = None          # pixels per meter (linear mode)
        self.homography: list | None = None    # 3x3, image foot -> ground meters
        self.world_area_m2: float | None = None
        # basic visualization (download-only mode): ID+box, H.264 result
        self.basic = False
        # in/out line counting
        self.counting = False
        self.count_line: list | None = None     # [[x,y],[x,y]]
        self.count_inside: list | None = None    # [x,y] on the "inside" half
        self.count_segment = True                # segment-only vs infinite line
        self.metrics: dict = {
            "unit": "px/s", "count": 0, "cumulative": 0, "avg": 0.0,
            "max": 0.0, "accel": 0.0, "moving": 0, "stationary": 0,
            "moving_ratio": 0.0, "density": 0.0, "density_unit": "-",
            "level": "—", "level_kr": "—", "avg_dwell": 0.0,
            "max_dwell": 0.0, "objects": [],
        }


def _push(job: Job, data):
    """Non-blocking push; drop oldest live frame if the viewer lags."""
    try:
        job.queue.put_nowait(data)
    except queue.Full:
        try:
            job.queue.get_nowait()
        except queue.Empty:
            pass
        try:
            job.queue.put_nowait(data)
        except queue.Full:
            pass


def _encode(frame):
    if STREAM_MAX_WIDTH and frame.shape[1] > STREAM_MAX_WIDTH:
        scale = STREAM_MAX_WIDTH / frame.shape[1]
        frame = cv2.resize(frame, None, fx=scale, fy=scale,
                           interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY),
                                           JPEG_QUALITY])
    return buf.tobytes() if ok else None


def _make_analyzer(job, item):
    """Sentinel for basic mode, LineCounter for counting, else SpeedEstimator."""
    if job.basic:
        return "basic"                 # no analyzer; _process draws ID boxes
    if job.counting:
        return LineCounter(job.count_line, job.count_inside,
                           segment_only=job.count_segment)
    return SpeedEstimator(job.fps or 25.0, pixels_per_meter=job.ppm,
                          homography=job.homography, roi=job.roi,
                          world_area_m2=job.world_area_m2,
                          frame_size=(item["width"], item["height"]))


def _process(analyzer, job, item, t_sec):
    """Run one frame through the analyzer -> (annotated_frame, metrics)."""
    if job.basic:                      # download-only: ID + box, no metrics
        n = int(item["targets"].shape[0]) if getattr(item["targets"], "ndim", 0) == 2 else 0
        return draw_basic(item["frame"], item["targets"]), {"kind": "basic", "count": n}
    if job.counting:
        analyzer.update(item["targets"])
        return analyzer.draw(item["frame"], item["targets"]), analyzer.metrics()
    present = analyzer.update(t_sec, item["targets"])
    return (annotate(item["frame"], item["targets"], present, analyzer),
            analyzer.metrics(present))


def _transcode_h264(path):
    """Re-encode the result mp4 (OpenCV writes mp4v) to H.264 in place via ffmpeg
    so the download plays everywhere (browsers, default players)."""
    src = str(path)
    tmp = src + ".tmp.mp4"
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", "-an", tmp],
            capture_output=True, text=True, timeout=900)
    except FileNotFoundError:
        return False
    if r.returncode == 0 and os.path.exists(tmp):
        os.replace(tmp, src)
        return True
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    return False


def _stop_other_live(keep_id=None):
    """Signal every other running LIVE job to stop, so it releases the model
    lock. Live jobs never end on their own; without this a new job would queue
    forever behind an orphaned live job (e.g. a viewer that just closed the tab)."""
    for jid, j in list(_jobs.items()):
        if jid != keep_id and j.live and j.status in ("queued", "processing"):
            j.stop = True


def _worker(job: Job):
    if job.live:
        return _worker_live(job)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = None
    analyzer = None
    try:
        with _model_lock:                 # one inference at a time
            job.status = "processing"
            for item in _model.stream(str(job.input_path), draw=False):
                if analyzer is None:
                    job.total = item["total"]
                    job.fps = item["fps"] or 25.0
                    analyzer = _make_analyzer(job, item)
                    writer = cv2.VideoWriter(
                        str(job.output_path), fourcc, job.fps,
                        (item["width"], item["height"]))

                t_sec = item["index"] / (job.fps or 25.0)   # video-timeline seconds
                frame, m = _process(analyzer, job, item, t_sec)

                writer.write(frame)
                jpg = _encode(frame)
                if jpg is not None:
                    job.replay_frames.append(jpg)
                    _push(job, jpg)
                job.metrics = m
                job.replay_metrics.append(m)   # keep per-frame for replay sync
                job.processed = item["index"]
        if job.basic and writer is not None:   # download mode: finalize as H.264
            writer.release()
            writer = None
            job.status = "encoding"
            _transcode_h264(job.output_path)
        job.status = "done"
    except Exception as exc:
        job.error = str(exc)
        job.status = "error"
    finally:
        if writer is not None:
            writer.release()
        _push(job, None)


def _worker_live(job: Job):
    """Live (RTSP) job: process the newest frame at the model's real-time rate,
    drop the backlog, run until /stop or disconnect. No recording, no replay.
    Speed timing uses the wall clock (frames are skipped non-uniformly)."""
    analyzer = None
    try:
        with _model_lock:
            job.status = "processing"
            for item in _model.stream(str(job.input_path), draw=False,
                                      live=True, should_stop=lambda: job.stop):
                if analyzer is None:
                    job.fps = item["fps"] or 0.0
                    analyzer = _make_analyzer(job, item)
                frame, m = _process(analyzer, job, item, time.monotonic())  # real time
                jpg = _encode(frame)
                if jpg is not None:
                    _push(job, jpg)
                job.metrics = m
                job.processed = item["index"]
        job.status = "stopped"
    except Exception as exc:
        job.error = str(exc)
        job.status = "error"
    finally:
        _push(job, None)


@app.on_event("startup")
def _startup():
    global _model
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("[webui] Loading TRT engines (one-time) ...")
    _model = BoostTrackGPUInference()
    print("[webui] Model ready. Open http://localhost:8000")


@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML.read_text(encoding="utf-8")


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    _stop_other_live()                 # adding a new source ends any old live job
    job_id = uuid.uuid4().hex[:12]
    ext = os.path.splitext(file.filename or "")[1].lower() or ".mp4"
    dest = UPLOAD_DIR / f"{job_id}{ext}"
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)

    # Grab the first frame so the client can set ROI / calibration on it.
    cap = cv2.VideoCapture(str(dest))
    ok, frame = cap.read()
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if not ok:
        raise HTTPException(400, "cannot read video")
    fok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    first_b64 = base64.b64encode(buf.tobytes()).decode() if fok else ""

    _jobs[job_id] = Job(job_id, dest)
    return {"job_id": job_id, "width": w, "height": h,
            "first_frame": "data:image/jpeg;base64," + first_b64}


@app.post("/rtsp")
def rtsp(body: dict = Body(default={})):
    """Open a live RTSP/stream source. Grabs one frame for ROI/calibration; the
    job runs live (no recording) until /stop."""
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "rtsp url required")
    _stop_other_live()                 # adding a new source ends any old live job
    cap = cv2.VideoCapture(url)
    ok, frame = cap.read()
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if not ok:
        raise HTTPException(400, f"cannot open stream: {url}")
    job_id = uuid.uuid4().hex[:12]
    job = Job(job_id, url)
    job.live = True
    _jobs[job_id] = job
    fok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    first_b64 = base64.b64encode(buf.tobytes()).decode() if fok else ""
    return {"job_id": job_id, "width": w, "height": h, "live": True,
            "first_frame": "data:image/jpeg;base64," + first_b64}


@app.post("/stop/{job_id}")
def stop(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    job.stop = True
    return {"ok": True}


@app.post("/start/{job_id}")
def start(job_id: str, body: dict = Body(default={})):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status not in ("uploaded",):
        raise HTTPException(409, f"job already {job.status}")

    # basic visualization (download-only): ID+box, no ROI/calibration/metrics
    if body.get("basic"):
        job.basic = True
        _stop_other_live(keep_id=job_id)
        job.status = "queued"
        threading.Thread(target=_worker, args=(job,), daemon=True).start()
        return {"job_id": job_id, "basic": True}

    # in/out line counting (independent of speed calibration)
    cnt = body.get("count")
    if cnt and cnt.get("line") and cnt.get("inside"):
        job.counting = True
        job.count_line = cnt["line"]
        job.count_inside = cnt["inside"]
        job.count_segment = bool(cnt.get("segment", True))
        _stop_other_live(keep_id=job_id)
        job.status = "queued"
        threading.Thread(target=_worker, args=(job,), daemon=True).start()
        return {"job_id": job_id, "counting": True}

    roi = body.get("roi")                       # [[x,y]*4] in original px, or None
    if roi and len(roi) == 4:
        job.roi = roi
    mode = body.get("mode", "none")             # none | line | homography | depth

    if mode == "line":
        ppm = body.get("pixels_per_meter")
        if ppm and float(ppm) > 0:
            job.ppm = float(ppm)
    elif mode == "homography" and job.roi:
        rw, rh = float(body.get("real_w", 0)), float(body.get("real_h", 0))
        if rw > 0 and rh > 0:
            import numpy as np
            img = np.array(job.roi, dtype=np.float32)            # 4 image corners
            world = np.array([[0, 0], [rw, 0], [rw, rh], [0, rh]],
                             dtype=np.float32)                    # meters
            H = cv2.getPerspectiveTransform(img, world)
            job.homography = H.tolist()
            job.world_area_m2 = rw * rh
    # mode == "depth": homography was already built by /prepare_depth and stored
    # on the job; nothing to do here. If prepare wasn't run, falls back to px/s.

    _stop_other_live(keep_id=job_id)   # free the model lock from any old live job
    job.status = "queued"
    threading.Thread(target=_worker, args=(job,), daemon=True).start()
    return {"job_id": job_id, "roi": job.roi, "pixels_per_meter": job.ppm}


@app.get("/status/{job_id}")
def status(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return JSONResponse({
        "status": job.status,
        "live": job.live,
        "processed": job.processed,
        "total": job.total,
        "fps": round(job.fps, 1),
        "error": job.error,
        "metrics": job.metrics,
    })


def _first_frame_boxes(input_path):
    """Person boxes on the first frame (for depth focal/ground estimation)."""
    with _model_lock:
        for item in _model.stream(input_path, draw=False):
            t = item["targets"]
            boxes = [[float(b[0]), float(b[1]), float(b[2]), float(b[3])]
                     for b in t] if getattr(t, "ndim", 0) == 2 else []
            return boxes, (item["width"], item["height"])
    return [], (0, 0)


@app.post("/prepare_depth/{job_id}")
def prepare_depth(job_id: str, body: dict = Body(default={})):
    """Run Depth-Anything-3 (separate env) on the first frame, fit the ground
    plane, and build the image->ground homography. Returns a depth preview the
    UI shows for confirmation before measurement actually starts."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    roi = body.get("roi")
    if roi and len(roi) == 4:
        job.roi = roi

    if not os.path.exists(DA3_PYTHON):
        raise HTTPException(500, f"da3 env python not found ({DA3_PYTHON}); "
                                 "set DA3_PYTHON env var")
    cap = cv2.VideoCapture(str(job.input_path))
    ok, frame = cap.read(); cap.release()
    if not ok:
        raise HTTPException(400, "cannot read frame")
    framep = UPLOAD_DIR / f"{job_id}_f0.png"
    depthp = OUTPUT_DIR / f"{job_id}_depth.npy"
    visp = OUTPUT_DIR / f"{job_id}_depth.png"
    cv2.imwrite(str(framep), frame)

    r = subprocess.run([DA3_PYTHON, DA3_SCRIPT, "--image", str(framep),
                        "--out-depth", str(depthp), "--out-vis", str(visp)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not depthp.exists():
        raise HTTPException(500, "depth extraction failed: " + (r.stderr or "")[-400:])

    depth = np.load(str(depthp))
    boxes, (w, h) = _first_frame_boxes(str(job.input_path))
    est = depth_ground.estimate(depth, boxes, (w, h))
    if est is None:
        return {"ok": False, "vis": f"/depthvis/{job_id}",
                "reason": "ground-plane estimate failed (need standing people / floor)"}
    job.homography = est["homography"]
    if job.roi:
        job.world_area_m2 = depth_ground.polygon_area_m2(
            np.array(est["homography"], dtype=np.float32), job.roi)
    return {"ok": True, "vis": f"/depthvis/{job_id}",
            "focal": round(est["focal"], 1),
            "inlier": round(est["plane_inlier"], 2),
            "people": len(boxes)}


@app.get("/depthvis/{job_id}")
def depthvis(job_id: str):
    visp = OUTPUT_DIR / f"{job_id}_depth.png"
    if not visp.exists():
        raise HTTPException(404, "no depth preview")
    return FileResponse(str(visp), media_type="image/png")


@app.get("/metrics_all/{job_id}")
def metrics_all(job_id: str):
    """Full per-frame metrics, so the client can replay the dashboard in sync
    with the looping result video (no DB — ephemeral, in memory)."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return {"fps": job.fps or 25.0, "total": len(job.replay_metrics),
            "frames": job.replay_metrics}


@app.get("/stream/{job_id}")
async def stream(job_id: str):
    """One continuous MJPEG stream: live during processing, then loop replay.

    The browser only ever decodes JPEGs in an <img>, so there is no video-codec
    dependency. Blocking ops run in a thread; pacing uses asyncio.sleep.
    """
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"

    async def gen():
        # Phase 1 — live frames while inference is running
        if job.status in ("uploaded", "queued", "processing"):
            while True:
                data = await asyncio.to_thread(job.queue.get)
                if data is None:
                    break
                yield boundary + data + b"\r\n"

        # Phase 2 — loop the pre-encoded frames forever, paced at source fps
        frames = job.replay_frames
        if job.status == "done" and frames:
            delay = 1.0 / (job.fps or 25.0)
            next_t = time.monotonic()
            i = 0
            while True:
                yield boundary + frames[i] + b"\r\n"
                i = (i + 1) % len(frames)
                next_t += delay
                sleep_for = next_t - time.monotonic()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
                else:
                    next_t = time.monotonic()

    return StreamingResponse(
        gen(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/result/{job_id}")
def result(job_id: str, download: bool = False):
    job = _jobs.get(job_id)
    path = job.output_path if job is not None else OUTPUT_DIR / f"{job_id}.mp4"
    if not path.exists():
        raise HTTPException(404, "result not ready")
    fname = f"tracked_{job_id}.mp4" if download else None   # attachment download
    return FileResponse(str(path), media_type="video/mp4", filename=fname)
