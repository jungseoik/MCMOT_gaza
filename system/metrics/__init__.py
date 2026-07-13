"""system.metrics — 맵 좌표 기반 지표 엔진 (M5, GPU 불필요).

TrackedObject(카메라 px) → 맵 투영 → 속도·정렬도·구역 밀도·병목·통과선 카운트
→ MapState 스냅샷. 후속 4대 지표(IDR·EPFI·CBS·SEI) 엔진이 이 층 위에 올라탄다.
"""
from system.metrics.engine import MetricsEngine

__all__ = ["MetricsEngine"]
