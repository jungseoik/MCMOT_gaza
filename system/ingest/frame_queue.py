"""ingest → tracking 공유 프레임 큐 (Edge 레포 pipeline/analysis/frame_queue.py 이식).

생산자(카메라 워커 N개)가 빠르고 소비자(분석 스레드 1개)가 느릴 때
가장 오래된 프레임을 버린다(oldest-drop). 드랍은 전체·카메라별로 누계한다
(CameraState.drops 계약 필드).
"""
from __future__ import annotations

import logging
import queue
import threading
from collections import defaultdict

from system.contracts import FrameItem

logger = logging.getLogger(__name__)


class FrameQueue:
    """thread-safe FrameItem 큐. 가득 차면 oldest-drop + 드랍 카운터."""

    def __init__(self, maxsize: int = 64) -> None:
        self._q: queue.Queue[FrameItem] = queue.Queue(maxsize=maxsize)
        self._dropped = 0
        self._dropped_by_cam: dict[str, int] = defaultdict(int)
        self._drop_lock = threading.Lock()

    def put(self, item: FrameItem) -> None:
        try:
            self._q.put_nowait(item)
            return
        except queue.Full:
            pass
        # 가득 참 — 가장 오래된 것 하나 빼고 다시 넣는다
        dropped: FrameItem | None = None
        try:
            dropped = self._q.get_nowait()
        except queue.Empty:
            pass
        try:
            self._q.put_nowait(item)
        except queue.Full:
            # 그 사이 다른 생산자가 채움 — 이번 프레임을 버린 셈
            dropped = item
        if dropped is not None:
            with self._drop_lock:
                self._dropped += 1
                self._dropped_by_cam[dropped.cam_id] += 1
                n = self._dropped
            if n % 30 == 0:
                logger.warning("FrameQueue overflow: dropped=%d", n)

    def get(self, timeout: float = 0.5) -> FrameItem | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def qsize(self) -> int:
        return self._q.qsize()

    @property
    def dropped(self) -> int:
        return self._dropped

    def drops_of(self, cam_id: str) -> int:
        with self._drop_lock:
            return self._dropped_by_cam.get(cam_id, 0)
