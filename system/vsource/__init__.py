"""system.vsource — 훈련영상 동기 송출 (리허설·시연용).

영상 파일 묶음을 **시간축이 맞게** RTSP로 내보낸다. 본체(추론·추적·지표)는
건드리지 않는다 — 카메라 입장에선 여전히 RTSP다. 안 켜면 아무 일도 안 일어난다.

설계: docs/architecture/08-훈련영상-동기송출-설계.md
"""
from system.vsource.controller import rtsp_url, start, status, stop
from system.vsource.scenario import Scenario, Stream, load, load_all

__all__ = ["start", "stop", "status", "rtsp_url",
           "load", "load_all", "Scenario", "Stream"]
