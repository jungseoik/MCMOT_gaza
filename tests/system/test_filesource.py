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


def test_snapshot_skips_black_segments(pkg, tmp_path):
    """전체 연속 시나리오 엣지 — 카메라가 첫 구간에 없으면 0번 프레임이 검정이라 매핑을
    못 한다. segments 로 등장 구간을 알면 그 시각의 (밝은) 프레임을 준비 스냅샷으로 쓴다."""
    root = tmp_path / "media" / "vsource" / "t" / "rehearsal"
    (root / "scenario_full").mkdir()
    # cam1: 앞 1초 검정 + 뒤 2초 회색 (= 두 번째 구간 1~3s 에만 등장)
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=1",
                    "-f", "lavfi", "-i", "color=c=gray:s=64x64:r=10:d=2",
                    "-filter_complex", "[0][1]concat=n=2:v=1:a=0[v]", "-map", "[v]",
                    "-c:v", "libx264", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
                    str(root / "scenario_full" / "cam1.mp4")], check=True)
    m = json.loads((root / "rehearsal.json").read_text(encoding="utf-8"))
    m["scenarios"].append({"id": "scenario_full", "name": "full", "cycle_sec": 0,
                           "segments": [{"scenario": "a", "start_sec": 0.0, "end_sec": 1.0, "cams": ["cam2"]},
                                        {"scenario": "b", "start_sec": 1.0, "end_sec": 3.0, "cams": ["cam1"]}],
                           "streams": [{"cam": "cam1", "file": "scenario_full/cam1.mp4"}]})
    (root / "rehearsal.json").write_text(json.dumps(m), encoding="utf-8")
    vpkg._cache.clear()
    p = vpkg.get("t-rehearsal")
    assert vpkg.snapshot_times(p, "scenario_full", "cam1") == [2.0]      # 등장 구간 시작 + 1s
    assert vpkg.snapshot_times(p, "scenario_01", "cam1") == [0.0]        # segments 없으면 0
    r = vf.FileSourceRunner(queue_put=lambda it: None)
    r.standby(p, "scenario_full", fps=5.0)
    try:
        fr = r.snapshot("rh_cam1")
        assert fr is not None and float(fr.mean()) > 60, "준비 스냅샷이 검정 구간을 피해야 한다"
        assert r.snapshot_is_black("rh_cam1") is False
        assert r.snapshot("rh_cam1", t=0.2) is not None and float(r.snapshot("rh_cam1", t=0.2).mean()) < 12
        assert r.status()["streams"][0]["snapshot_candidates_sec"] == [2.0]
    finally:
        r.stop()
