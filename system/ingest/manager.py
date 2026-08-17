"""IngestManager — SiteStore의 CameraConfig 목록으로 카메라 워커 무리를 운영.

기동/중지/추가/제거/enabled 토글 + 카메라별 CameraState 제공 + 온디맨드 스냅샷.
50채널 확장 전제: ffmpeg CUDA 컨텍스트가 채널당 ~0.6GB VRAM을 쓰므로
gpu_devices=[0,1]을 주면 -hwaccel_device를 라운드로빈으로 분산한다.
"""
from __future__ import annotations

import logging
import threading

import numpy as np

from system.config.schema import CameraConfig
from system.contracts import CameraState
from system.ingest.camera_worker import CameraWorker
from system.ingest.frame_queue import FrameQueue

logger = logging.getLogger(__name__)


class IngestManager:
    def __init__(
        self,
        frame_queue: FrameQueue | None = None,
        *,
        gpu_devices: list[int] | None = None,
        stall_sec: float | None = None,
    ) -> None:
        self.queue = frame_queue or FrameQueue(maxsize=64)
        self._gpu_devices = gpu_devices
        self._gpu_rr = 0
        self._stall_sec = stall_sec
        self._cfgs: dict[str, CameraConfig] = {}
        self._workers: dict[str, CameraWorker] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 기동/중지
    def start(self, cameras: list[CameraConfig]) -> None:
        """SiteStore.list_cameras() 결과로 일괄 기동 (enabled만)."""
        for cfg in cameras:
            self.add_camera(cfg)

    def stop(self) -> None:
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for w in workers:
            w.stop()
        for w in workers:
            w.join(timeout=5.0)
        logger.info("IngestManager stopped (%d workers)", len(workers))

    # ------------------------------------------------------------ 카메라 CRUD
    def add_camera(self, cfg: CameraConfig, *, defer_restart: bool = False) -> None:
        """카메라 1대 추가.

        defer_restart는 DsIngestManager와의 인터페이스 호환용으로만 받는다 —
        이 백엔드는 카메라마다 독립 ffmpeg 워커라 '슬롯 재시작' 개념이 없고,
        추가가 다른 채널에 영향을 주지 않는다.
        """
        with self._lock:
            if cfg.cam_id in self._workers:
                raise ValueError(f"이미 실행 중인 카메라: {cfg.cam_id}")
            self._cfgs[cfg.cam_id] = cfg
            if cfg.enabled:
                self._spawn_locked(cfg)

    def add_cameras(self, cfgs: list[CameraConfig]) -> None:
        """여러 대 일괄 추가 — 이 백엔드에서는 순차 추가와 결과가 같다
        (카메라별 독립 워커라 묶어서 얻는 이득이 없다)."""
        for cfg in cfgs:
            self.add_camera(cfg)

    def remove_camera(self, cam_id: str) -> None:
        with self._lock:
            self._cfgs.pop(cam_id, None)
            worker = self._workers.pop(cam_id, None)
        if worker is not None:
            worker.stop()
            worker.join(timeout=5.0)

    def update_cameras(self, cfgs: list[CameraConfig]) -> None:
        """여러 대 변경 일괄 반영 — 이 백엔드에서는 순차 변경과 결과가 같다
        (카메라별 독립 워커라 묶어서 얻는 이득이 없다)."""
        for cfg in cfgs:
            self.update_camera(cfg)

    def update_camera(self, cfg: CameraConfig, *, defer_restart: bool = False) -> None:
        """rtsp/analyze_fps/enabled 변경 반영 — 워커 재기동.

        defer_restart는 DsIngestManager와의 인터페이스 호환용 (이 백엔드는
        카메라별 독립 워커라 다른 채널에 영향을 주지 않는다)."""
        with self._lock:
            worker = self._workers.pop(cfg.cam_id, None)
            self._cfgs[cfg.cam_id] = cfg
        if worker is not None:
            worker.stop()
            worker.join(timeout=5.0)
        with self._lock:
            if cfg.enabled and cfg.cam_id not in self._workers:
                self._spawn_locked(cfg)

    def set_enabled(self, cam_id: str, enabled: bool) -> None:
        with self._lock:
            cfg = self._cfgs.get(cam_id)
        if cfg is None:
            raise KeyError(f"미등록 카메라: {cam_id}")
        if cfg.enabled == enabled and (enabled == (cam_id in self._workers)):
            return
        self.update_camera(cfg.model_copy(update={"enabled": enabled}))

    # ------------------------------------------------------------ 상태/스냅샷
    def states(self) -> list[CameraState]:
        with self._lock:
            cfgs = dict(self._cfgs)
            workers = dict(self._workers)
        out: list[CameraState] = []
        for cam_id in sorted(cfgs):
            w = workers.get(cam_id)
            if w is not None:
                out.append(w.state())
            else:
                out.append(CameraState(cam_id=cam_id, status="disabled",
                                       drops=self.queue.drops_of(cam_id)))
        return out

    def state(self, cam_id: str) -> CameraState | None:
        for st in self.states():
            if st.cam_id == cam_id:
                return st
        return None

    def get_snapshot(self, cam_id: str) -> np.ndarray | None:
        """온디맨드 스냅샷 (BGR ndarray) — 셋업/디버깅용, 스트림 아님."""
        with self._lock:
            w = self._workers.get(cam_id)
        return None if w is None else w.get_snapshot()

    # ------------------------------------------------------------ 내부
    def _spawn_locked(self, cfg: CameraConfig) -> None:
        device = None
        if self._gpu_devices:
            device = self._gpu_devices[self._gpu_rr % len(self._gpu_devices)]
            self._gpu_rr += 1
        kwargs = {} if self._stall_sec is None else {"stall_sec": self._stall_sec}
        w = CameraWorker(cfg.cam_id, cfg.rtsp, cfg.analyze_fps, self.queue,
                         hwaccel_device=device, **kwargs)
        self._workers[cfg.cam_id] = w
        w.start()
