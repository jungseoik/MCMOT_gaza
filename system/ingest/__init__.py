"""system.ingest — 멀티카메라 RTSP 인제스트 (M2).

CameraWorker(카메라당 ffmpeg+NVDEC 서브프로세스, 3신호 재접속 워치독)
→ FrameQueue(oldest-drop) → tracking 분석 스레드.
스모크/부하 실측: `python -m system.ingest --help`
"""
from system.ingest.camera_worker import CameraWorker
from system.ingest.frame_queue import FrameQueue
from system.ingest.manager import IngestManager

__all__ = ["CameraWorker", "FrameQueue", "IngestManager"]
