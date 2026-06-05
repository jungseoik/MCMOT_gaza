"""Standalone live tracking web UI (FastAPI + MJPEG).

This module is fully separate from the core pipeline. It only *reuses*
`src.inference_gpu.BoostTrackGPUInference` — it does not change any README
workflow. Launch it on demand with:

    python -m webui            # then open http://localhost:8000

Flow:
  1. Upload a video               -> POST /upload  (returns job_id)
  2. A worker thread runs TRT inference frame-by-frame, and for each frame
       (a) writes it to outputs/<job_id>.mp4   (for smooth loop replay)
       (b) JPEG-encodes it into a queue          (for the live MJPEG view)
  3. While processing: <img src="/stream/<id>"> shows frames as they finish.
  4. When done: the page swaps to <video loop> playing /result/<id>.mp4.
"""
import os
import sys
import time
import queue
import uuid
import asyncio
import threading
from pathlib import Path

import cv2
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import (
    StreamingResponse, HTMLResponse, FileResponse, JSONResponse,
)

# Make the project root importable so we can reuse the existing pipeline.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.inference_gpu import BoostTrackGPUInference  # noqa: E402

BASE = Path(__file__).resolve().parent
UPLOAD_DIR = BASE / "_data" / "uploads"
OUTPUT_DIR = BASE / "_data" / "outputs"
INDEX_HTML = BASE / "index.html"

app = FastAPI(title="BoostTrack Live UI")

# Stream tuning. Smaller JPEGs keep the MJPEG stream at full source fps
# (large frames become transport-bound). The saved mp4 keeps full resolution.
STREAM_MAX_WIDTH = 854   # downscale wider frames for the live/replay stream
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
        # All encoded JPEG frames, kept for loop replay (no re-decode needed).
        self.replay_frames: list[bytes] = []
        self.status = "queued"        # queued | processing | done | error
        self.processed = 0
        self.total = 0
        self.fps = 0.0
        self.error: str | None = None


def _push(job: Job, data):
    """Non-blocking push. If the live viewer lags, drop the oldest frame —
    the mp4 on disk still keeps every frame, so nothing is lost for replay."""
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


def _worker(job: Job):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = None
    try:
        # Serialize: only one inference runs at a time (shared tracker state).
        with _model_lock:
            job.status = "processing"
            for item in _model.stream(str(job.input_path)):
                if writer is None:
                    job.total = item["total"]
                    job.fps = item["fps"] or 25.0
                    writer = cv2.VideoWriter(
                        str(job.output_path), fourcc, job.fps,
                        (item["width"], item["height"]),
                    )
                writer.write(item["frame"])
                vis = item["frame"]
                if STREAM_MAX_WIDTH and vis.shape[1] > STREAM_MAX_WIDTH:
                    scale = STREAM_MAX_WIDTH / vis.shape[1]
                    vis = cv2.resize(vis, None, fx=scale, fy=scale,
                                     interpolation=cv2.INTER_AREA)
                ok, buf = cv2.imencode(
                    ".jpg", vis, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
                )
                if ok:
                    jpg = buf.tobytes()
                    job.replay_frames.append(jpg)   # keep all frames for replay
                    _push(job, jpg)                 # live view (drops if viewer lags)
                job.processed = item["index"]
        job.status = "done"
    except Exception as exc:  # surface failure to the UI
        job.error = str(exc)
        job.status = "error"
    finally:
        if writer is not None:
            writer.release()
        _push(job, None)   # sentinel: end of live stream


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
    job_id = uuid.uuid4().hex[:12]
    ext = os.path.splitext(file.filename or "")[1].lower() or ".mp4"
    dest = UPLOAD_DIR / f"{job_id}{ext}"
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    job = Job(job_id, dest)
    _jobs[job_id] = job
    threading.Thread(target=_worker, args=(job,), daemon=True).start()
    return {"job_id": job_id}


@app.get("/status/{job_id}")
def status(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return JSONResponse({
        "status": job.status,
        "processed": job.processed,
        "total": job.total,
        "fps": round(job.fps, 1),
        "error": job.error,
    })


@app.get("/stream/{job_id}")
async def stream(job_id: str):
    """One continuous MJPEG stream that transitions live -> loop replay.

    Phase 1 (while processing): emit frames as inference produces them.
    Phase 2 (once done): decode the saved mp4 server-side (cv2 reads mp4v fine)
    and re-emit it as JPEG in an endless loop at the source frame rate. The
    browser only ever decodes JPEGs in an <img>, so there is no codec issue.

    Async generator: blocking ops (queue.get / cap.read) run in a thread, and
    pacing uses asyncio.sleep so the event loop streams each frame promptly.
    """
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"

    async def gen():
        # Phase 1 — live frames while inference is running
        if job.status in ("queued", "processing"):
            while True:
                data = await asyncio.to_thread(job.queue.get)
                if data is None:
                    break
                yield boundary + data + b"\r\n"

        # Phase 2 — loop the pre-encoded frames forever, paced at source fps.
        # Frames were JPEG-encoded once during inference, so replay does zero
        # decode/encode work: pacing is governed purely by asyncio.sleep, which
        # holds true fps. Fixed schedule (next_t += delay) absorbs send jitter.
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
                    next_t = time.monotonic()   # fell behind -> resync

    return StreamingResponse(
        gen(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/result/{job_id}")
def result(job_id: str):
    job = _jobs.get(job_id)
    if job is None or not job.output_path.exists():
        raise HTTPException(404, "result not ready")
    return FileResponse(str(job.output_path), media_type="video/mp4")
