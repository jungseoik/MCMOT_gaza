"""파일 소스 모드 — 리허설 영상을 RTSP 없이 직접 읽어 잠금 동기로 추론한다 (ADR 09 §11).

왜: RTSP 송출 → mediamtx → DS 재수신 경로는 "실제 현장을 흉내" 내는 장치인데, 리허설의
목적(지표 파이프라인 검증·시연)에는 그 흉내가 잡음만 넣었다 — 카메라마다 버퍼·재접속
지연이 달라 수백 ms 어긋나고, 앞머리(정지화면)·재부착 같은 시간축 핵이 끼고, 짧은 클립은
그 사이에 끝난다. 오프라인 시각화(tools/rehearsal_viz.py)가 "원하던 그림"이 나온 이유는
정확히 그 경로가 없기 때문이었다.

어떻게: 시나리오의 영상 N개를 **프레임 인덱스 k 로 잠금** 읽고(전 카메라 같은 순간),
analyze_fps 로 서브샘플해 `FrameItem(ts = t0 + k/fps)` 를 AnalyzerThread 큐에 넣는다.
그 뒤(검출·트래커·conf 매칭·엔진·세션·지표·SSE)는 라이브 ffmpeg 백엔드와 **같은 코드**.
트래커 설정도 DS 워커와 같다(BoostTrack per_instance_ids, max_age = fps×2s, ECC off).

상태 스키마는 controller.status() 와 같게 돌려준다 — ⑤ 리허설 탭·session.js 가 그대로 쓴다.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from system.contracts import CameraState, FrameItem
from system.vsource import package as vpkg

logger = logging.getLogger("system.vsource.file")

CYCLE_PAD_SEC = 2.0          # scenario.py 와 동일
START_MARGIN_SEC = float(os.environ.get("VSOURCE_FILE_START_MARGIN", "0.8"))
FALLBEHIND_SKIP_SEC = 1.0    # 이만큼 뒤처지면 프레임을 건너뛰어 벽시계를 따라간다
# 디코드는 스텝 비용의 85%(실측 1080p 채널당 14ms) — cv2 는 GIL 을 놓으므로 카메라별
# 스레드로 병렬화하면 코어 수만큼 나눠진다. 직렬이면 12채널@5fps 가 실시간 한계였다.
DECODE_WORKERS = int(os.environ.get("VSOURCE_FILE_DECODE_WORKERS", "8"))


class _Cam:
    def __init__(self, cam_id: str, path: str, file: str, fps_analyze: float):
        self.cam_id, self.path, self.file = cam_id, path, file
        self.cap: cv2.VideoCapture | None = None
        self.src_fps = 30.0
        self.total = 0
        self.stride = 6
        self.duration = 0.0
        self.frame0: np.ndarray | None = None
        self.last: np.ndarray | None = None
        self.last_ts: float | None = None
        self.ended = False
        self.seq = 0
        self.drops = 0
        self._fps_ema = 0.0
        self.fps_analyze = fps_analyze

    def open(self) -> None:
        self.close()
        cap = cv2.VideoCapture(self.file)
        if not cap.isOpened():
            raise FileNotFoundError(self.file)
        self.cap = cap
        self.src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.stride = max(1, int(round(self.src_fps / self.fps_analyze)))
        self.duration = self.total / self.src_fps if self.src_fps else 0.0
        self.ended = False

    BLACK_MEAN = 12.0          # 이 밝기 아래면 "검정 구간"으로 본다 (0~255)

    def frame_at(self, sec: float) -> np.ndarray | None:
        """임의 시각의 프레임 — 재생용 cap 을 건드리지 않는 별도 캡처."""
        cap = cv2.VideoCapture(self.file)
        try:
            if not cap.isOpened():
                return None
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, sec) * 1000.0)
            ok, fr = cap.read()
            return fr if ok else None
        finally:
            cap.release()

    def read_first(self, candidates: list[float] | None = None) -> np.ndarray | None:
        """준비(정지) 단계·매핑용 스냅샷.

        전체 연속 시나리오에선 이 카메라가 첫 구간에 없어 0번 프레임이 **검정**일 수 있다.
        candidates(등장 구간 시각)를 차례로 시도해 밝은 프레임을 고르고, 다 검정이면
        마지막 것을 쓴다(그래도 매핑 화면에 "구간에 없음" 경고가 뜬다).
        """
        pick = None
        for t in (candidates or [0.0]):
            fr = self.frame_at(t)
            if fr is None:
                continue
            pick = fr
            if float(fr.mean()) >= self.BLACK_MEAN:
                break
        self.frame0 = pick
        self.last = self.frame0
        self.open()                         # 재생은 정확히 0번 프레임부터
        return self.frame0

    def read_step(self, k: int) -> np.ndarray | None:
        """k번째 분석 프레임(= 원본 k*stride 번째). 끝나면 None."""
        if self.cap is None or self.ended:
            return None
        n_skip = self.stride - 1 if k > 0 else 0
        for _ in range(n_skip):
            if not self.cap.grab():
                self.ended = True
                return None
        ok, fr = self.cap.read()
        if not ok:
            self.ended = True
            return None
        self.last = fr
        return fr

    def mark(self, ts: float) -> None:
        now = time.time()
        if self.last_ts is not None:
            dt = max(1e-3, now - self._wall_prev)
            self._fps_ema = 0.7 * self._fps_ema + 0.3 * (1.0 / dt) if self._fps_ema else 1.0 / dt
        self._wall_prev = now
        self.last_ts = ts
        self.seq += 1

    def state(self, playing: bool) -> CameraState:
        st = "running" if (playing and not self.ended) or (not playing and self.frame0 is not None) \
            else ("disconnected" if self.ended else "reconnecting")
        return CameraState(cam_id=self.cam_id, status=st,
                           fps_in=round(self._fps_ema, 2) if playing and not self.ended else 0.0,
                           last_frame_ts=self.last_ts, drops=self.drops)

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class FileSourceRunner:
    """시나리오 하나를 파일에서 잠금 동기로 재생해 AnalyzerThread 큐에 넣는다.

    queue_put(FrameItem) — 보통 FrameQueue.put. 분석 스레드는 호출자가 소유한다
    (라이브 ffmpeg 백엔드의 AnalyzerThread 그대로; DS 백엔드 서버에서는 파일 모드용으로
    하나 띄운다 — server.py 참조).
    """

    def __init__(self, queue_put, rtsp_host: str = "127.0.0.1:8554"):
        self._put = queue_put
        self._rtsp_host = rtsp_host
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pool: ThreadPoolExecutor | None = None
        self.cams: list[_Cam] = []
        self.pkg_id: str | None = None
        self.scen_id: str | None = None
        self.scenario_name = ""
        self.floors: list[str] = []
        self.mode: str | None = None         # None | "standby" | "play" | "done"
        self.fps = 5.0
        self.t0 = 0.0
        self.cycle_sec = 0.0
        self.loop = False
        self.started_at = 0.0
        self.k = 0
        self.cycle_n = 0

    # ------------------------------------------------------------ 상태
    @property
    def active(self) -> bool:
        return self.mode in ("standby", "play")

    def scenario_id(self) -> str | None:
        return f"{vpkg.SCENARIO_PREFIX}{self.pkg_id}:{self.scen_id}" if self.pkg_id else None

    # ------------------------------------------------------------ 제어
    def standby(self, pkg: dict, scen_id: str, cams_floor: dict[str, str] | None = None,
                fps: float = 5.0) -> dict:
        """준비 — 영상을 열고 0번 프레임을 정지로 붙잡는다 (매핑용). 즉시 완료."""
        scen = next((s for s in pkg.get("scenarios", []) if s.get("id") == scen_id), None)
        if scen is None:
            raise FileNotFoundError(f"{pkg.get('id')}:{scen_id}")
        self.stop()
        root = Path(pkg["_root"])
        cams = []
        for st in scen.get("streams", []):
            cam = st.get("cam")
            if not cam:
                continue
            c = _Cam(vpkg.cam_id_of(cam), vpkg.stream_path(pkg, cam), str(root / st["file"]), fps)
            c.open()
            c.snapshot_candidates = vpkg.snapshot_times(pkg, scen_id, cam)
            c.read_first(c.snapshot_candidates)
            cams.append(c)
        if not cams:
            raise ValueError("시나리오에 영상이 없습니다")
        durs = [c.duration for c in cams if c.duration]
        with self._lock:
            self.cams = cams
            self.pkg_id, self.scen_id = pkg["id"], scen_id
            self.scenario_name = f"{pkg.get('name', pkg['id'])} — {scen.get('name', scen_id)}"
            self.floors = sorted({cams_floor.get(c.cam_id) for c in cams
                                  if cams_floor and cams_floor.get(c.cam_id)})
            self.fps = float(fps)
            self.cycle_sec = float(scen.get("cycle_sec") or 0) or \
                (math.ceil(max(durs) + CYCLE_PAD_SEC) if durs else 0.0)
            self.mode = "standby"
            self.started_at = time.time()
            self.k = 0
        logger.info("[vsource.file] 준비: %s/%s · %d채널 · %.0ffps · 사이클 %.0fs",
                    pkg["id"], scen_id, len(cams), fps, self.cycle_sec)
        return self.status()

    def start(self, loop: bool = False) -> dict:
        """재생 — t0 = 지금 + 여유. 경보 시각 = t0 (앞머리 없음)."""
        if self.mode not in ("standby", "play", "done") or not self.cams:
            raise ValueError("준비(standby)가 먼저 필요합니다")
        self._join_thread()
        for c in self.cams:
            c.open()                                  # 0번 프레임부터 다시
        with self._lock:
            self.loop = bool(loop)
            self.t0 = time.time() + START_MARGIN_SEC
            self.mode = "play"
            self.k = 0
            self.cycle_n = 0
            self.started_at = time.time()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vsource-file", daemon=True)
        self._thread.start()
        logger.info("[vsource.file] 시작: %s/%s · T0=%.3f · loop=%s", self.pkg_id, self.scen_id,
                    self.t0, loop)
        return self.status()

    def stop(self) -> dict:
        self._stop.set()
        self._join_thread()
        with self._lock:
            for c in self.cams:
                c.close()
            n = len(self.cams)
            self.cams = []
            self.mode = None
            self.pkg_id = self.scen_id = None
            self.floors = []
        return {"running": False, "stopped": n, "pm2_restored": [], "orphans_killed": 0,
                "source": "file"}

    def _join_thread(self) -> None:
        th = self._thread
        if th is not None and th.is_alive():
            self._stop.set()
            th.join(timeout=3.0)
        self._thread = None
        self._stop.clear()

    # ------------------------------------------------------------ 재생 루프
    def _run(self) -> None:
        fps = self.fps
        base = self.t0
        k = 0
        try:
            while not self._stop.is_set():
                ts = base + k / fps
                now = time.time()
                if ts > now:
                    # 정밀 대기 — 전 카메라 같은 ts 로 한 스텝
                    if self._stop.wait(min(ts - now, 0.25)):
                        break
                    if time.time() < ts:
                        continue
                elif now - ts > FALLBEHIND_SKIP_SEC:
                    # 추론이 밀렸다 — 프레임을 버려 벽시계를 따라간다 (라이브의 드롭과 동형)
                    skip = int((now - ts) * fps)
                    for c in self.cams:
                        for _ in range(skip * c.stride):
                            if not c.cap or not c.cap.grab():
                                c.ended = True
                                break
                        c.drops += skip
                    k += skip
                    continue
                if self._pool is None:
                    self._pool = ThreadPoolExecutor(max_workers=min(DECODE_WORKERS, max(1, len(self.cams))))
                frames = list(zip(self.cams, self._pool.map(lambda c, kk=k: c.read_step(kk), self.cams)))
                if all(fr is None for _, fr in frames):
                    if self.loop:
                        self.cycle_n += 1
                        base = self.t0 + self.cycle_n * self.cycle_sec
                        k = 0
                        for c in self.cams:
                            c.open()
                        logger.info("[vsource.file] 사이클 %d 시작", self.cycle_n)
                        continue
                    with self._lock:
                        self.mode = "done"
                    logger.info("[vsource.file] 재생 끝 (%d스텝)", k)
                    return
                for c, fr in frames:
                    if fr is None:
                        continue
                    self._put(FrameItem(cam_id=c.cam_id, ts=ts, frame=fr, seq=c.seq))
                    c.mark(ts)
                with self._lock:
                    self.k = k
                k += 1
        except Exception:
            logger.exception("[vsource.file] 재생 루프 실패")
            with self._lock:
                self.mode = "done"
        finally:
            if self._pool is not None:
                self._pool.shutdown(wait=False)
                self._pool = None

    # ------------------------------------------------------------ 조회
    def states(self) -> list[CameraState]:
        with self._lock:
            playing = self.mode == "play"
            return [c.state(playing) for c in self.cams]

    def snapshot(self, cam_id: str, t: float | None = None) -> np.ndarray | None:
        """준비 프레임(기본) 또는 t 초 프레임(매핑 화면 [다른 장면])."""
        with self._lock:
            cam = next((c for c in self.cams if c.cam_id == cam_id), None)
        if cam is None:
            return None
        if t is not None:
            return cam.frame_at(float(t))
        return cam.last if cam.last is not None else cam.frame0

    def snapshot_is_black(self, cam_id: str) -> bool | None:
        with self._lock:
            cam = next((c for c in self.cams if c.cam_id == cam_id), None)
        fr = cam.frame0 if cam is not None else None
        return None if fr is None else bool(float(fr.mean()) < _Cam.BLACK_MEAN)

    def status(self) -> dict:
        with self._lock:
            if self.mode is None or not self.cams:
                return {"running": False, "source": "file"}
            now = time.time()
            playing = self.mode == "play"
            elapsed = now - self.t0 if playing else 0.0
            cyc = self.cycle_sec
            in_cycle = (elapsed % cyc) if (playing and cyc > 0 and elapsed >= 0) else max(0.0, elapsed)
            next_at = (self.t0 + (int(elapsed // cyc) + 1) * cyc) if (playing and self.loop and cyc > 0 and elapsed >= 0) else None
            streams = []
            for c in self.cams:
                streams.append({"path": c.path, "file": c.file, "cam_id": c.cam_id,
                                "duration_sec": round(c.duration, 3),
                                "snapshot_candidates_sec": list(getattr(c, "snapshot_candidates", []) or []),
                                "publishing": not c.ended if playing else True,
                                "receiving": (not c.ended) if playing else True,
                                "pos_sec": (round(in_cycle, 1) if playing and c.duration > in_cycle else None)})
            running = self.mode in ("standby", "play")
            return {
                "running": running, "source": "file",
                "mode": "standby" if self.mode == "standby" else "play",
                "scenario_id": self.scenario_id(), "scenario_name": self.scenario_name,
                "t0": self.t0 if playing else 0.0,
                "lead_sec": 0.0, "alarm_at": self.t0 if playing else None,
                "lead_left_sec": 0.0, "in_lead": False, "attach_measured_sec": None,
                "cycle_sec": cyc, "loop": self.loop,
                "elapsed_sec": round(elapsed, 3), "cycle_pos_sec": round(in_cycle, 3),
                "next_cycle_at": next_at,
                "next_cycle_in": (round(next_at - now, 3) if next_at else None),
                "pm2_stopped": [], "floors": list(self.floors),
                "streams": streams,
                "cams_receiving": sum(1 for s in streams if s["receiving"]),
                "cams_total": len(streams),
                "done": self.mode == "done",
            }
