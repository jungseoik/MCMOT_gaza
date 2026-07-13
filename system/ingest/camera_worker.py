"""카메라 1대 = ffmpeg 서브프로세스 1개 (M0 확정: ffmpeg + NVDEC).

명령: ffmpeg -hwaccel cuda [-hwaccel_device N] -rtsp_transport tcp -i <rtsp>
      -vf fps=<analyze_fps> -f rawvideo -pix_fmt bgr24 pipe:1
디코드는 원 fps로 NVDEC이 수행하고 출력만 analyze_fps로 다운샘플해
고정 크기 rawvideo 프레임을 stdout 파이프로 받는다. 사전 ffprobe로
해상도를 확인해 프레임 크기를 고정한다.

재접속 워치독은 Edge 레포 pipeline/reconnect.py의 3신호 + 지수 백오프
패턴을 프로세스 감시로 이식한 것:
  EOS      → 프로세스 종료/stdout EOF
  stall    → stall_sec 동안 무프레임
  error    → stderr 치명 패턴 (연결 거부/타임아웃 등)
→ 프로세스 kill 후 지수 백오프(5,10,20,40,60s) 재기동.
  첫 프레임 후 stable_sec(60s) 이상 정상 수신하다 죽으면 attempts 리셋.
"""
from __future__ import annotations

import json
import logging
import os
import select
import subprocess
import threading
import time
from collections import deque

import numpy as np

from system.contracts import CameraState, FrameItem
from system.ingest.frame_queue import FrameQueue

logger = logging.getLogger(__name__)

BACKOFF_SEC = [5, 10, 20, 40, 60]   # 지수 백오프 (마지막 값 유지)
STABLE_SEC = 60.0                   # 이 시간 이상 안정 수신 시 attempts 리셋
STALL_SEC = 10.0                    # 무프레임 stall 판정 (5fps면 프레임 간격 0.2s)
PROBE_TIMEOUT = 10.0                # ffprobe 타임아웃

# stderr 치명 패턴 — 매칭 시 즉시 세션 종료(3신호 중 error).
# 그 외 stderr는 tail로만 보관(디코드 단발 에러 등은 프로세스 생존 시 무시).
_FATAL_STDERR = (
    "connection refused", "connection timed out", "no route to host",
    "server returned 4", "server returned 5", "end of file",
    "conversion failed", "could not find codec", "unauthorized",
    "immediate exit requested",
)


class CameraWorker(threading.Thread):
    """카메라 1대의 ffmpeg 프로세스 수명·재접속을 소유하는 워커 스레드."""

    def __init__(
        self,
        cam_id: str,
        rtsp: str,
        analyze_fps: float,
        frame_queue: FrameQueue,
        *,
        hwaccel_device: int | None = None,
        stall_sec: float = STALL_SEC,
        stable_sec: float = STABLE_SEC,
        backoff: list[int] | None = None,
    ) -> None:
        super().__init__(daemon=True, name=f"ingest-{cam_id}")
        self.cam_id = cam_id
        self.rtsp = rtsp
        self.analyze_fps = analyze_fps
        self.queue = frame_queue
        self.hwaccel_device = hwaccel_device
        self.stall_sec = stall_sec
        self.stable_sec = stable_sec
        self.backoff = backoff or BACKOFF_SEC

        # 상태 어휘는 계약(CameraState.status)과 동일:
        # running | reconnecting | disconnected | disabled
        self.status = "reconnecting"
        self.seq = 0                       # 재접속을 넘어 단조 증가 (드랍 계측용)
        self.width: int | None = None
        self.height: int | None = None
        self.attempts = 0                  # 현재 백오프 시도 횟수

        self._stop_evt = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._last_frame: np.ndarray | None = None
        self._last_frame_ts: float | None = None
        self._fps_win: deque[float] = deque(maxlen=25)   # 수신 fps 슬라이딩 윈도
        self._stderr_tail: deque[str] = deque(maxlen=12)
        self._stderr_fatal = threading.Event()

    # ------------------------------------------------------------ 외부 API
    def state(self) -> CameraState:
        return CameraState(
            cam_id=self.cam_id,
            status=self.status,
            fps_in=self._fps_in(),
            last_frame_ts=self._last_frame_ts,
            drops=self.queue.drops_of(self.cam_id),
        )

    def get_snapshot(self) -> np.ndarray | None:
        """온디맨드 스냅샷 — 마지막 수신 프레임 복사본 (스트림 아님)."""
        frame = self._last_frame
        return None if frame is None else frame.copy()

    def stop(self) -> None:
        self._stop_evt.set()
        self._kill_proc()

    # ------------------------------------------------------------ 메인 루프
    def run(self) -> None:
        while not self._stop_evt.is_set():
            reason, first_frame_mono = self._session()
            if self._stop_evt.is_set():
                break
            # 안정 판정: 첫 프레임 후 stable_sec 이상 수신했으면 백오프 리셋
            if first_frame_mono is not None and \
                    time.monotonic() - first_frame_mono >= self.stable_sec:
                if self.attempts:
                    logger.info("[%s] %.0f초 안정 수신 — 백오프 리셋", self.cam_id, self.stable_sec)
                self.attempts = 0
            delay = self.backoff[min(self.attempts, len(self.backoff) - 1)]
            self.attempts += 1
            self.status = "reconnecting"
            tail = self._stderr_tail[-1] if self._stderr_tail else ""
            logger.warning("[%s] source dead (reason=%s, attempt=%d) — %ds 후 재접속%s",
                           self.cam_id, reason, self.attempts, delay,
                           f" | stderr: {tail}" if tail else "")
            self._stop_evt.wait(delay)
        self._kill_proc()
        self.status = "disconnected"

    # ------------------------------------------------------------ 1회 세션
    def _session(self) -> tuple[str, float | None]:
        """probe → ffmpeg 기동 → 프레임 읽기. (종료 사유, 첫 프레임 시각) 반환."""
        self._stderr_fatal.clear()
        self._stderr_tail.clear()

        wh = self._probe()
        if self._stop_evt.is_set():
            return "stopped", None
        if wh is None:
            return "probe-failed", None
        w, h = wh
        self.width, self.height = w, h
        frame_size = w * h * 3

        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-hwaccel", "cuda"]
        if self.hwaccel_device is not None:
            cmd += ["-hwaccel_device", str(self.hwaccel_device)]
        cmd += ["-rtsp_transport", "tcp", "-i", self.rtsp,
                "-an", "-vf", f"fps={self.analyze_fps:g}",
                "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE)
        except OSError as e:
            logger.error("[%s] ffmpeg 기동 실패: %s", self.cam_id, e)
            return "spawn-failed", None
        proc = self._proc
        threading.Thread(target=self._drain_stderr, args=(proc,),
                         daemon=True, name=f"stderr-{self.cam_id}").start()

        fd = proc.stdout.fileno()
        buf = bytearray()
        first_frame_mono: float | None = None
        last_data = time.monotonic()

        try:
            while not self._stop_evt.is_set():
                if self._stderr_fatal.is_set():
                    return "stderr-error", first_frame_mono
                readable, _, _ = select.select([fd], [], [], 1.0)
                now = time.monotonic()
                if not readable:
                    if proc.poll() is not None:
                        return "process-exit", first_frame_mono
                    if now - last_data > self.stall_sec:
                        return "stall", first_frame_mono
                    continue
                chunk = os.read(fd, frame_size - len(buf))
                if not chunk:                       # stdout EOF (= EOS 신호)
                    return "eos", first_frame_mono
                last_data = now
                buf += chunk
                if len(buf) < frame_size:
                    continue
                frame = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3).copy()
                buf = bytearray()
                self.seq += 1
                ts = time.time()
                self._last_frame = frame
                self._last_frame_ts = ts
                self._fps_win.append(ts)
                if first_frame_mono is None:
                    first_frame_mono = now
                    logger.info("[%s] connected: %dx%d @%.3gfps (dev=%s)",
                                self.cam_id, w, h, self.analyze_fps,
                                self.hwaccel_device)
                self.status = "running"
                self.queue.put(FrameItem(cam_id=self.cam_id, ts=ts,
                                         frame=frame, seq=self.seq))
            return "stopped", first_frame_mono
        finally:
            self._kill_proc()

    # ------------------------------------------------------------ 내부
    def _probe(self) -> tuple[int, int] | None:
        """사전 ffprobe로 해상도 확인 — rawvideo 프레임 크기 고정에 필수."""
        cmd = ["ffprobe", "-v", "error", "-rtsp_transport", "tcp",
               "-select_streams", "v:0",
               "-show_entries", "stream=width,height", "-of", "json", self.rtsp]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=PROBE_TIMEOUT)
            info = json.loads(out.stdout or b"{}")
            st = (info.get("streams") or [{}])[0]
            w, h = int(st.get("width", 0)), int(st.get("height", 0))
            if w > 0 and h > 0:
                return w, h
            err = (out.stderr or b"").decode(errors="replace").strip()
            if err:
                self._stderr_tail.append(err.splitlines()[-1])
        except subprocess.TimeoutExpired:
            self._stderr_tail.append("ffprobe timeout")
        except (OSError, ValueError) as e:
            self._stderr_tail.append(f"ffprobe: {e}")
        return None

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            self._stderr_tail.append(line)
            low = line.lower()
            if any(p in low for p in _FATAL_STDERR):
                self._stderr_fatal.set()

    def _kill_proc(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe and pipe.close()
            except OSError:
                pass
        self._proc = None

    def _fps_in(self) -> float:
        win = list(self._fps_win)
        # 최근 프레임이 stall_sec보다 오래됐으면 0으로 보고
        if len(win) < 2 or time.time() - win[-1] > self.stall_sec:
            return 0.0
        span = win[-1] - win[0]
        return (len(win) - 1) / span if span > 0 else 0.0
