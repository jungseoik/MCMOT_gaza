"""파일 소스 모드(ADR 09 §11) — 영상 N개를 프레임 잠금 동기로 읽어 분석 큐에 넣는다.

핵심 계약: 같은 스텝 k 의 전 카메라 FrameItem 은 **같은 ts** 를 갖고(전 채널 같은 순간),
ts 는 t0 + k/fps 격자를 정확히 따른다. RTSP 경로의 지연·재접속이 끼어들 곳이 없다.
"""
import json
import subprocess
import time

import pytest

from system.vsource import filesource as vf
from system.vsource import package as vpkg


@pytest.fixture()
def pkg(tmp_path, monkeypatch):
    root = tmp_path / "media" / "vsource" / "t" / "rehearsal"
    (root / "scenario_01").mkdir(parents=True)
    for cam in ("cam1", "cam2"):                    # 10fps · 1.0s → 분석 5fps 면 5스텝
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=gray:s=64x64:r=10:d=1", "-c:v", "libx264",
                        "-profile:v", "baseline", "-pix_fmt", "yuv420p",
                        str(root / "scenario_01" / f"{cam}.mp4")], check=True)
    m = {"schema": 1, "id": "t-rehearsal", "name": "T", "rtsp_prefix": "t",
         "floors": [{"id": "floor9", "name": "9F"}],
         "cameras": [{"cam": "cam1", "path": "t_cam1", "floor": "floor9", "analyze_fps": 5.0},
                     {"cam": "cam2", "path": "t_cam2", "floor": "floor9", "analyze_fps": 5.0}],
         "scenarios": [{"id": "scenario_01", "name": "s1", "cycle_sec": 0,
                        "streams": [{"cam": "cam1", "file": "scenario_01/cam1.mp4"},
                                    {"cam": "cam2", "file": "scenario_01/cam2.mp4"}]}]}
    (root / "rehearsal.json").write_text(json.dumps(m), encoding="utf-8")
    monkeypatch.setattr(vpkg, "PKG_ROOT", tmp_path / "media" / "vsource")
    monkeypatch.setattr(vf, "START_MARGIN_SEC", 0.2)
    vpkg._cache.clear()
    return vpkg.get("t-rehearsal")


def test_source_default_is_file(pkg):
    assert vpkg.source(pkg) == "file"
    assert vpkg.source({**pkg, "source": "rtsp"}) == "rtsp"


def test_standby_holds_first_frame_and_status_schema(pkg):
    got = []
    r = vf.FileSourceRunner(queue_put=got.append)
    st = r.standby(pkg, "scenario_01", cams_floor={"rh_cam1": "floor9", "rh_cam2": "floor9"}, fps=5.0)
    try:
        assert st["running"] and st["mode"] == "standby" and st["source"] == "file"
        assert st["cams_receiving"] == st["cams_total"] == 2      # 대기 즉시 완료 (부착 없음)
        assert st["floors"] == ["floor9"]
        assert st["scenario_id"] == "pkg:t-rehearsal:scenario_01"
        assert r.snapshot("rh_cam1") is not None                    # 매핑용 정지 프레임
        assert [s.cam_id for s in r.states()] == ["rh_cam1", "rh_cam2"]
        assert not got                                              # 대기 중엔 분석 안 함
        for k in ("t0", "lead_sec", "alarm_at", "cycle_sec", "loop", "streams",
                  "cycle_pos_sec", "next_cycle_in", "pm2_stopped"):
            assert k in st                                          # controller.status() 와 동형
    finally:
        r.stop()


def test_play_lockstep_ts_grid(pkg):
    """전 카메라가 같은 ts 로 한 스텝씩, ts 는 t0 + k/fps 격자 — 이게 동기의 전부다."""
    got = []
    r = vf.FileSourceRunner(queue_put=got.append)
    r.standby(pkg, "scenario_01", fps=5.0)
    st = r.start(loop=False)
    try:
        assert st["mode"] == "play" and st["lead_sec"] == 0.0
        assert abs(st["alarm_at"] - st["t0"]) < 1e-9                 # 경보 = t0 (앞머리 없음)
        deadline = time.time() + 5
        while r.mode == "play" and time.time() < deadline:
            time.sleep(0.05)
        assert r.mode == "done"
        by_step = {}
        for it in got:
            by_step.setdefault(round(it.ts - st["t0"], 3), set()).add(it.cam_id)
        steps = sorted(by_step)
        assert len(steps) == 5, steps                                # 1.0s @5fps
        assert all(by_step[s] == {"rh_cam1", "rh_cam2"} for s in steps), by_step
        gaps = [round(b - a, 3) for a, b in zip(steps, steps[1:])]
        assert gaps == [0.2] * 4, gaps
        assert all(it.frame.shape == (64, 64, 3) for it in got)
        assert r.status()["running"] is False and r.status()["done"] is True
    finally:
        r.stop()
    assert r.status() == {"running": False, "source": "file"}
    assert r.snapshot("rh_cam1") is None                            # 종료 = 전부 해제


def test_stop_midway_releases(pkg):
    got = []
    r = vf.FileSourceRunner(queue_put=got.append)
    r.standby(pkg, "scenario_01", fps=5.0)
    r.start(loop=True)
    time.sleep(0.6)
    out = r.stop()
    assert out["running"] is False and out["stopped"] == 2
    assert r.mode is None and r.cams == []
