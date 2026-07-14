"""트랙 간 런타임 인터페이스 (계약 동결 — 변경은 CONTRACT.md 절차로).

데이터 흐름:
  ingest(트랙 A) → FrameItem → tracking(트랙 A) → TrackedObject
  → spatial/metrics(트랙 B) → MapState → api/SSE → 프론트(트랙 C)

시간 규약: ts는 wall-clock epoch 초(float, time.time()).
프레임 인덱스 가정 금지 — 채널별 fps가 다르고 드랍이 존재한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from pydantic import BaseModel

# ---------------------------------------------------------------- 런타임 (A→B)


@dataclass
class FrameItem:
    """ingest → tracking. frame은 BGR ndarray (H,W,3) uint8."""
    cam_id: str
    ts: float                 # epoch 초 (수신 시각)
    frame: np.ndarray
    seq: int = 0              # 카메라별 수신 순번 (드랍 계측용)


@dataclass
class TrackedObject:
    """tracking → spatial/metrics. 좌표는 카메라 프레임 px (맵 투영 전)."""
    cam_id: str
    local_track_id: int
    foot_uv: tuple[float, float]       # bbox 하단 중심 (대표점)
    bbox_xyxy: tuple[float, float, float, float]
    conf: float
    ts: float


# --------------------------------------------------------- MapState (B→C, SSE)


class MapObject(BaseModel):
    """맵 위 객체 1개. 좌표는 맵 원본 px, 속도는 실단위."""
    cam_id: str
    id: int                    # 카메라 내부 local track id
    gid: str                   # 표시용 키 = f"{cam_id}:{id}" (글로벌 병합 전)
    x: float
    y: float
    vx: float = 0.0            # 이동방향 단위벡터 (맵 좌표계)
    vy: float = 0.0
    speed_mps: float | None = None
    align: float | None = None  # 경로 방향정렬도 cosine (-1~1), 경로 없으면 None
    in_bounds: bool = True      # 맵 경계(w×h) 밖 투영 여부 (계약 개정 2026-07-13)


class ZoneState(BaseModel):
    id: str
    count: int = 0
    density: float | None = None       # 명/m² (축척 있을 때)


class BottleneckState(BaseModel):
    id: str
    count: int = 0
    density: float | None = None
    over: bool = False                 # rho_crit 초과 여부


class ExitState(BaseModel):
    id: str
    in_count: int = 0
    out_count: int = 0


class CameraState(BaseModel):
    cam_id: str
    status: str = "disabled"  # running | reconnecting | disconnected | disabled
    fps_in: float = 0.0       # 실제 수신 fps
    last_frame_ts: float | None = None
    drops: int = 0            # FrameQueue oldest-drop 누계


class MapState(BaseModel):
    """운영 뷰 스냅샷 — GET /api/map/state · SSE /api/map/stream (1초)."""
    ts: float
    site_version: int = 0
    objects: list[MapObject] = []
    zones: list[ZoneState] = []
    bottlenecks: list[BottleneckState] = []
    exits: list[ExitState] = []
    cameras: list[CameraState] = []
    session: "SessionLive | None" = None       # 평가 세션 진행 중일 때 (v1.2)


# ------------------------------------------- 평가 세션 · 4대 지표 (계약 v1.2)
# 수식·예외 정의: docs/requirements/삼성화재-피난훈련-정량평가-4대지표-요구사항.md §2


class ZoneMetric(BaseModel):
    """IDR — 구역별 피난 반응."""
    zone_id: str
    evacuation_start_at: float | None = None   # t_e,start (epoch)
    response_delay_sec: float | None = None    # t_e,start − t_alarm
    graph_distance: float | None = None        # 경보위치→구역 최단거리 (m)
    idr: float | None = None
    participant_ratio: float = 0.0             # 판정 시점 r_e
    status: str = "not_started"                # started | not_started


class PersonMetric(BaseModel):
    """EPFI — 객체별 경로 충실도."""
    global_track_id: str                       # gid
    assigned_route_id: str | None = None
    duration_sec: float = 0.0                  # T_i
    mean_deviation_m: float | None = None
    max_deviation_m: float | None = None
    epfi: float | None = None                  # 0~100


class BottleneckMetric(BaseModel):
    """CBS — 병목별 혼잡 누적."""
    bottleneck_id: str
    peak_density: float = 0.0
    over_threshold_sec: float = 0.0
    cbs: float = 0.0                           # ∫max(0,ρ−ρcrit)·w dt
    risk_level: str = "low"                    # low | mid | high


class ExitMetric(BaseModel):
    """SEI — 출구별 실제·설계 분포."""
    exit_id: str
    actual_count: int = 0                      # E_j (고유 최초통과)
    design_capacity: int | None = None         # C_j (사람 입력)
    actual_share: float | None = None
    design_share: float | None = None


class EvaluationResult(BaseModel):
    """세션 결과 — GET /api/session/result (요구사항 §4)."""
    session_id: str
    calibration_version: int = 0               # site version at start
    config_version: int = 0
    alarm_ts: float
    alarm_origin: tuple[float, float]          # 맵 px
    ended_at: float | None = None
    zone_metrics: list[ZoneMetric] = []
    person_metrics: list[PersonMetric] = []
    bottleneck_metrics: list[BottleneckMetric] = []
    exit_metrics: list[ExitMetric] = []
    sei: float | None = None                   # None = insufficient_data
    epfi_avg: float | None = None
    cbs_total: float = 0.0
    quality: dict[str, Any] = {}
    generated_at: float = 0.0


class TimelinePoint(BaseModel):
    """세션 타임라인 1초 샘플 — 대시보드 시간대별 시각화."""
    ts: float
    sei: float | None = None
    cbs_total: float = 0.0
    epfi_avg: float | None = None
    zones_started: int = 0
    exit_counts: dict[str, int] = {}
    bottleneck_density: dict[str, float] = {}


class SessionLive(BaseModel):
    """진행 중 세션 스냅샷 — MapState.session에 실림."""
    session_id: str
    alarm_ts: float
    alarm_origin: tuple[float, float]
    config_version: int = 0    # 세션이 고정한 설정 스냅샷 버전 (v1.4 —
                               # site_version과 다르면 "다음 세션부터 적용" 안내)
    elapsed_sec: float = 0.0
    sei: float | None = None
    cbs_total: float = 0.0
    epfi_avg: float | None = None
    zones_started: int = 0
    zones_total: int = 0


MapState.model_rebuild()  # session: SessionLive forward ref 해석


def mapstate_example() -> dict[str, Any]:
    """프론트/mock 개발용 예시 페이로드."""
    return MapState(
        ts=1752345678.0,
        site_version=3,
        objects=[MapObject(cam_id="cam01", id=7, gid="cam01:7", x=412.5, y=233.1,
                           vx=0.71, vy=-0.71, speed_mps=1.2, align=0.93)],
        zones=[ZoneState(id="z1", count=4, density=0.8)],
        bottlenecks=[BottleneckState(id="b1", count=9, density=2.3, over=True)],
        exits=[ExitState(id="e1", in_count=12, out_count=1)],
        cameras=[CameraState(cam_id="cam01", status="running", fps_in=5.0,
                             last_frame_ts=1752345677.8, drops=0)],
    ).model_dump()
