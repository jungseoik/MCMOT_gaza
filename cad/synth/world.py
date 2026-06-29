#!/usr/bin/env python3
"""
world.py — 추천1/추천2가 공유하는 '하나의 피난 시나리오' 정의.
  - 도면 로드(1회), 카메라들, 군중 시뮬, 분석구역/가상선.
같은 월드를 두 방식이 공유하므로 GT가 일관된다(특히 멀티캠 ID 병합 검증).
좌표는 평면도 남서코너=(0,0) m (데모 근사값, 추후 실측 재보정 전제).
"""
import os, math
import numpy as np

from scene import load_segments
from camera import Camera
import crowd


def _yaw_to(cam_m, look_m):
    """cam→look 수평 yaw(deg). tilt는 별도 고정값으로 강제(하방 내려보기)."""
    return math.degrees(math.atan2(look_m[1] - cam_m[1], look_m[0] - cam_m[0]))

DXF = os.path.join(os.path.dirname(__file__), "..", "17F.dxf")
W, H = 1280, 720

# 피난 시나리오(미터): SW 개방 사무공간 → 코어 계단 출구
EXIT_XY = (26.0, 27.0)
SPAWN_RECT = (5.0, 5.0, 22.0, 20.0)
N_AGENTS = 11
FPS = 15
DURATION_S = 22.0

# 병목 분석구역(출구 앞) + 비상구 가상 통과선
ZONE_POLY = [(22.0, 24.0), (29.0, 24.0), (29.0, 29.5), (22.0, 29.5)]
ZONE_AREA_M2 = 7.0 * 5.5
VLINE = ((23.0, 26.0), (29.0, 26.0))   # y가 26을 넘어가면 통과


def build_scene():
    segs, ext = load_segments(DXF)
    return segs, ext


def build_cameras(ext):
    """camA: 추천1/2 공용 주 카메라. camB: 추천2 멀티캠(반대편, 출구 겹침).
    높은 천장 설치 + 광축 하방 TILT(>=45°)로 '내려다보는' CCTV 시점."""
    TILT = 47.0          # 하방 틸트각(deg) — 45° 이상 내려다봄
    CAM_H = 4.6          # 설치 높이 m(상부 코너 마운트)
    A = (17.0, 15.0)     # 병목 남서쪽에서 출구를 내려다봄
    B = (24.0, 9.0)      # 같은 개방부 남동쪽 → 같은 군중을 반대 각도로(멀티캠 겹침)
    camA = Camera(ext, cam_m=A, cam_h_m=CAM_H, hfov_deg=82, W=W, H=H,
                  yaw_deg=_yaw_to(A, (27.0, 28.0)), tilt_deg=TILT)
    camB = Camera(ext, cam_m=B, cam_h_m=CAM_H, hfov_deg=82, W=W, H=H,
                  yaw_deg=_yaw_to(B, (15.0, 22.0)), tilt_deg=TILT)
    return {"camA": camA, "camB": camB}


def build_crowd():
    return crowd.simulate(n_agents=N_AGENTS, fps=FPS, duration_s=DURATION_S,
                          spawn_rect=SPAWN_RECT, exit_xy=EXIT_XY, seed=0)
