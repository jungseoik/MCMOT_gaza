"""퍼블리셔 — 채널 1개를 T0 기준 사이클 격자에 맞춰 RTSP로 내보낸다.

**이 파일은 모듈이자 실행 스크립트다.** 컨트롤러가 채널마다 하나씩
`python -m system.vsource.publisher ...` 로 띄운다(detach).

동기의 핵심(ADR 08 §2): 프로세스를 미리 띄워두고 **각자 T0까지 정밀 대기 후
ffmpeg를 exec** 한다. 컨트롤러가 순차로 spawn 해도 실제 시작은 T0에 모인다
— 6채널 실측 편차 1.7ms.

사이클 격자: n번째 재생은 `T0 + n*cycle` 에 시작한다. 영상 길이가 채널마다
달라도(ADR 08 §4) 되감기 시점이 전 채널 공통이라 시간축이 유지된다.
길이가 사이클보다 긴 비정상 케이스는 경계에서 잘라 다음 사이클을 밀지 않는다.
"""
from __future__ import annotations

import argparse
import math
import os
import signal
import subprocess
import sys
import time

# -re 없이 보내면 ffmpeg가 파일을 최대 속도로 쏟아붓는다(181초를 몇 초에).
# 그러면 시간축이 무너져 리허설 자체가 성립하지 않는다 — ADR 08 §3.
FFMPEG_BASE = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re"]


def sleep_until(ts: float) -> None:
    """벽시계 ts 까지 대기 (남은 시간의 90%씩 자다가 마지막은 촘촘히)."""
    while True:
        d = ts - time.time()
        if d <= 0:
            return
        time.sleep(d * 0.9 if d > 0.005 else 0)


def ffmpeg_cmd(file: str, url: str) -> list[str]:
    # -c:v copy — 재인코딩하면 채널당 CPU 인코더가 붙어 9채널이면 CPU가 먼저 막힌다.
    return FFMPEG_BASE + ["-i", file, "-c:v", "copy", "-an",
                          "-f", "rtsp", "-rtsp_transport", "tcp", url]


def standby_cmd(still: str, url: str) -> list[str]:
    """대기 송출 — 첫 프레임 한 장을 계속 내보낸다(영상은 멈춰 있는 셈).

    매핑을 하려면 카메라에 프레임이 들어와야 하는데, 본영상을 틀면 시간이
    흘러버린다. 정지화면을 물려두면 카메라가 붙어 매핑은 되고 시간은 안 간다.
    정지화면이라 인코딩 부하는 거의 없다(-tune stillimage).
    """
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re",
            "-loop", "1", "-i", still,
            "-c:v", "libx264", "-profile:v", "baseline", "-preset", "ultrafast",
            "-tune", "stillimage", "-g", "30", "-keyint_min", "30",
            "-sc_threshold", "0", "-x264-params", "bframes=0:repeat-headers=1",
            "-pix_fmt", "yuv420p", "-r", "30", "-an",
            "-f", "rtsp", "-rtsp_transport", "tcp", url]


def run_standby(still: str, url: str) -> int:
    """정지화면을 계속 송출한다. 컨트롤러가 죽일 때까지."""
    proc: subprocess.Popen | None = None

    def _bye(*_a):
        if proc and proc.poll() is None:
            proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)
    while True:                               # ffmpeg가 죽어도 다시 띄운다
        proc = subprocess.Popen(standby_cmd(still, url))
        proc.wait()
        time.sleep(1.0)


def run(file: str, url: str, t0: float, cycle_sec: float, loop: bool) -> int:
    """사이클 격자에 맞춰 송출. loop=False면 1회만."""
    proc: subprocess.Popen | None = None

    def _bye(*_a):
        if proc and proc.poll() is None:
            proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)

    n = 0
    while True:
        start_at = t0 + n * cycle_sec
        # 이미 지나간 사이클은 건너뛴다(늦게 spawn 됐거나 재부착한 경우) —
        # 중간부터 틀면 다른 채널과 위치가 어긋나므로 다음 경계를 기다린다.
        if start_at < time.time() - 0.5:
            if not loop:
                return 0
            n = max(n + 1, math.ceil((time.time() - t0) / cycle_sec))
            continue
        sleep_until(start_at)
        proc = subprocess.Popen(ffmpeg_cmd(file, url))
        deadline = t0 + (n + 1) * cycle_sec if loop else None
        while proc.poll() is None:
            if deadline is not None and time.time() >= deadline:
                proc.terminate()                    # 사이클 경계 — 다음 바퀴를 밀지 않는다
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            time.sleep(0.2)
        if not loop:
            return proc.returncode or 0
        n += 1


def main() -> int:
    ap = argparse.ArgumentParser(description="vsource 채널 퍼블리셔 (컨트롤러가 띄운다)")
    ap.add_argument("--file", help="본영상 (대기 모드에선 불필요)")
    ap.add_argument("--url", required=True)
    ap.add_argument("--t0", type=float, default=0.0, help="동시 시작 시각 (epoch)")
    ap.add_argument("--cycle", type=float, default=0.0, help="사이클 길이 (s)")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--standby", metavar="STILL",
                    help="대기 모드 — 이 이미지를 계속 송출(본영상 대신)")
    a = ap.parse_args()
    if a.standby:
        return run_standby(a.standby, a.url)
    if not a.file:
        ap.error("--file 이 필요합니다 (대기 모드가 아니면)")
    # 프로세스 그룹은 만들지 않는다 — 컨트롤러가 start_new_session=True 로 띄우므로
    # 이미 세션·그룹 리더다. 여기서 setpgrp()를 부르면 EPERM으로 죽는다(실측).
    return run(a.file, a.url, a.t0, a.cycle, a.loop)


if __name__ == "__main__":
    raise SystemExit(main())
