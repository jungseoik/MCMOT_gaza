"""system.tracking — 공유 TRT 추론 + 카메라별 트래커 (M3).

단일 AnalyzerThread가 FrameQueue를 소비해 cam_id별 BoostTrack 인스턴스
(ID 공간 격리)로 추적하고 on_tracks 콜백으로 TrackedObject를 넘긴다.
스모크: `python -m system.tracking --help`
"""
from system.tracking.analyzer import AnalyzerThread

__all__ = ["AnalyzerThread"]
