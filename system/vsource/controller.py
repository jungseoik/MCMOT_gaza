"""컨트롤러 — 시나리오 하나를 N채널 동시 송출로 띄우고 상태를 집계한다.

설계: docs/architecture/08-훈련영상-동기송출-설계.md

두 가지가 이 층의 존재 이유다.
1. **동시 시작** — 채널별 퍼블리셔를 미리 spawn 하고 공통 T0를 넘겨준다.
   순차로 띄워도 실제 시작은 T0에 모인다(실측 편차 1.7ms).
2. **경로 인수** — 카메라가 이미 보고 있는 RTSP 경로를 그대로 쓴다(§5).
   같은 경로에 퍼블리셔 둘은 공존 못 하므로 pm2 송출을 먼저 내린다.

프로세스는 detach(`start_new_session=True`) 해서 :8900 재시작에도 살아남고,
상태파일로 재부착한다 — 서버가 자주 재시작되는데(현재 37회) 그때마다 리허설이
끊기면 시연을 못 한다.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from system.vsource import scenario as sc

logger = logging.getLogger("system.vsource")

STATE_FILE = Path("data/vsource_state.json")
LOG_DIR = Path("data/vsource_logs")     # 채널별 퍼블리셔 stderr — 조용히 죽으면
                                        # 원인을 볼 데가 없다(실측으로 겪음)
STILL_DIR = Path("data/vsource_stills")  # 대기 송출용 첫 프레임 (영상당 1장, 캐시)
RTSP_HOST = os.environ.get("VSOURCE_RTSP_HOST", "127.0.0.1:8554")
LEAD_SEC = float(os.environ.get("VSOURCE_LEAD_SEC", "2.0"))   # spawn 여유
PM2_RESTORE = os.environ.get("VSOURCE_PM2_RESTORE", "1").strip().lower() \
    not in ("0", "false", "no")


def rtsp_url(path: str) -> str:
    return f"rtsp://{RTSP_HOST}/{path}"


# ------------------------------------------------------------------ pm2 연동
def _pm2(*args: str) -> tuple[int, str]:
    try:
        r = subprocess.run(["pm2", *args], capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def _pm2_online() -> set[str]:
    """현재 online 인 pm2 프로세스 이름들. pm2가 없으면 빈 집합."""
    code, out = _pm2("jlist")
    if code != 0:
        return set()
    try:
        return {p["name"] for p in json.loads(out or "[]")
                if p.get("pm2_env", {}).get("status") == "online"}
    except (ValueError, KeyError, TypeError):
        return set()


def _pm2_stop(names: list[str]) -> list[str]:
    """경로를 점유 중인 pm2 송출을 내린다. 실제로 내린 이름만 돌려준다."""
    online = _pm2_online()
    stopped = []
    for n in names:
        if n in online:
            code, _ = _pm2("stop", n)
            if code == 0:
                stopped.append(n)
                logger.info("[vsource] pm2 정지: %s", n)
    return stopped


def _pm2_start(names: list[str]) -> None:
    for n in names:
        _pm2("start", n)
        logger.info("[vsource] pm2 복구: %s", n)


# ------------------------------------------------------------------ 상태파일
def _read_state() -> dict | None:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_state(st: dict | None) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if st is None:
        STATE_FILE.unlink(missing_ok=True)
        return
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


# ---------------------------------------------------- 리허설 밖 카메라 파킹
# 리허설은 준비한 영상이 전부여야 한다. 시나리오에 없는 카메라를 켜둔 채 두면
# 그 층까지 건물 훈련 참여 층으로 잡혀 리허설과 무관한 실영상이 지표에 섞인다.
# 켜는 동안만 끄고, 정지하면 원래 상태로 되돌린다.
_park_cb = None       # (cam_id, enabled) -> None  — API 층이 주입


def set_park_hook(fn) -> None:
    """카메라 활성 토글 훅 주입 (server.py 가 자기 store/ingest 로 처리)."""
    global _park_cb
    _park_cb = fn


def _park(cam_ids: list[str], enabled: bool) -> list[str]:
    if not cam_ids or _park_cb is None:
        return []
    done = []
    for cid in cam_ids:
        try:
            _park_cb(cid, enabled)
            done.append(cid)
        except Exception:
            logger.exception("[vsource] 카메라 %s 토글 실패", cid)
    if done:
        logger.info("[vsource] 카메라 %s: %s", "복구" if enabled else "파킹", done)
    return done


def _still_for(file: str, path: str) -> str | None:
    """영상 첫 프레임을 PNG로 뽑아 캐시. 대기 송출용."""
    STILL_DIR.mkdir(parents=True, exist_ok=True)
    out = STILL_DIR / f"{path}.png"
    try:
        src_m = Path(file).stat().st_mtime
        if out.is_file() and out.stat().st_mtime >= src_m:
            return str(out)
    except OSError:
        return None
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", file, "-frames:v", "1", str(out)],
            capture_output=True, timeout=30)
        return str(out) if r.returncode == 0 and out.is_file() else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _spawn(cmd: list[str], path: str) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    lf = open(LOG_DIR / f"{path}.log", "w")
    p = subprocess.Popen(cmd, cwd=os.getcwd(), start_new_session=True,
                         stdout=lf, stderr=subprocess.STDOUT)
    lf.close()
    return p.pid


# ------------------------------------------------------------------ 제어
def standby(scenario_id: str, cameras=None) -> dict:
    """대기 송출 — 각 채널의 **첫 프레임을 정지화면으로** 계속 내보낸다.

    매핑을 하려면 카메라에 프레임이 들어와야 하는데 본영상을 틀면 시간이
    흘러버린다. 정지화면이면 카메라가 붙어 매핑은 되고 영상은 멈춰 있다.
    준비가 끝나면 start()로 t=0부터 본영상을 튼다(전환 실측 1.2초).
    """
    s = sc.load(scenario_id, cameras=cameras)
    if not s.ok:
        raise ValueError("시나리오에 문제가 있어 시작할 수 없습니다: "
                         + " / ".join(s.problems))
    prev = _read_state()                     # 재생→대기 전환이면 pm2는 이미 내려가 있다
    stop(restore_pm2=False, unpark=False)    # 곧 다시 쓸 pm2·카메라를 건드리지 않는다
    pm2_stopped = _pm2_stop([st.path for st in s.streams])
    if prev and prev.get("pm2_stopped"):     # 직전 단계가 내린 것도 복구 목록에 승계
        pm2_stopped = sorted(set(pm2_stopped) | set(prev["pm2_stopped"]))

    streams, missing = [], []
    for st in s.streams:
        still = _still_for(st.file, st.path)
        if not still:
            missing.append(st.path)
            continue
        pid = _spawn([sys.executable, "-m", "system.vsource.publisher",
                      "--url", rtsp_url(st.path), "--standby", still], st.path)
        streams.append({"path": st.path, "file": st.file, "pid": pid,
                        "duration_sec": st.duration_sec})
    if missing:
        logger.warning("[vsource] 첫 프레임 추출 실패: %s", missing)

    parked = (prev.get("parked") if prev else None) or _park(
        s.outside_cams(cameras), False)
    state = {"scenario_id": s.id, "scenario_name": s.name, "mode": "standby",
             "t0": 0.0, "cycle_sec": s.cycle_sec, "loop": False,
             "started_at": time.time(), "pm2_stopped": pm2_stopped,
             "parked": parked, "streams": streams}
    _write_state(state)
    logger.info("[vsource] 대기 송출: %s · %d채널 (정지화면)", s.id, len(streams))
    return status()


def start(scenario_id: str, loop: bool = True, cameras=None) -> dict:
    """시나리오를 동시 송출로 시작. 이미 돌고 있으면 먼저 정지한다."""
    s = sc.load(scenario_id, cameras=cameras)
    if not s.ok:
        raise ValueError("시나리오에 문제가 있어 시작할 수 없습니다: "
                         + " / ".join(s.problems))
    if s.cycle_sec <= 0:
        raise ValueError("사이클 길이를 정할 수 없습니다(영상 길이 불명).")

    prev = _read_state()                     # 대기 송출에서 넘어오는 경우 pm2는 이미 내려가 있다
    stop(restore_pm2=False, unpark=False)    # 곧 다시 쓸 pm2·카메라를 건드리지 않는다

    # 같은 경로에 퍼블리셔 둘은 공존 못 한다 — 점유 중인 pm2를 먼저 내린다.
    pm2_stopped = _pm2_stop([st.path for st in s.streams])
    if prev and prev.get("pm2_stopped"):     # 직전 단계가 내린 것도 복구 목록에 남긴다
        pm2_stopped = sorted(set(pm2_stopped) | set(prev["pm2_stopped"]))

    t0 = time.time() + LEAD_SEC
    streams = []
    for st in s.streams:
        cmd = [sys.executable, "-m", "system.vsource.publisher",
               "--file", st.file, "--url", rtsp_url(st.path),
               "--t0", f"{t0:.6f}", "--cycle", f"{s.cycle_sec:.6f}"]
        if loop:
            cmd.append("--loop")
        streams.append({"path": st.path, "file": st.file,
                        "pid": _spawn(cmd, st.path),
                        "duration_sec": st.duration_sec})

    parked = (prev.get("parked") if prev else None) or _park(
        s.outside_cams(cameras), False)
    state = {"scenario_id": s.id, "scenario_name": s.name, "mode": "play",
             "t0": t0, "cycle_sec": s.cycle_sec, "loop": bool(loop),
             "started_at": time.time(), "pm2_stopped": pm2_stopped,
             "parked": parked, "streams": streams}
    _write_state(state)
    logger.info("[vsource] 시작: %s · %d채널 · T0=%.3f · 사이클 %.1fs · loop=%s",
                s.id, len(streams), t0, s.cycle_sec, loop)
    return status()


def _sweep_orphans() -> int:
    """상태파일에 없는 떠돌이 퍼블리셔 정리.

    detach 프로세스라 상태파일이 유실되면(수동 삭제·비정상 종료) 영영 못 잡는다.
    그런 게 남아 있으면 pm2 복구와 경로를 다투게 되므로 이름으로 훑어 없앤다.
    """
    try:
        out = subprocess.run(["pgrep", "-f", "system.vsource.publisher"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0
    n = 0
    for line in out.split():
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            n += 1
        except OSError:
            pass
    return n


def stop(restore_pm2: bool | None = None, unpark: bool = True) -> dict:
    """송출 정지. restore_pm2 미지정이면 VSOURCE_PM2_RESTORE(기본 on).

    unpark=False 는 모드 전환(대기↔재생) 중 내부 호출용 — 파킹한 카메라를
    껐다 켰다 하면 그때마다 엔진이 재적재돼 전환이 느려진다.
    """
    st = _read_state()
    if not st:
        orphans = _sweep_orphans()
        return {"running": False, "stopped": orphans, "pm2_restored": [],
                "orphans_killed": orphans}
    n = 0
    for s in st.get("streams", []):
        pid = s.get("pid")
        if not pid or not _alive(pid):
            continue
        try:                                  # 퍼블리셔와 그 자식 ffmpeg를 함께
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                continue
        n += 1
    deadline = time.time() + 1.5              # 종료 대기 후 남으면 강제
                                              # (퍼블리셔는 SIGTERM에 곧바로 응답한다)
    while time.time() < deadline:
        if not any(_alive(s.get("pid")) for s in st.get("streams", [])):
            break
        time.sleep(0.1)
    for s in st.get("streams", []):
        if _alive(s.get("pid")):
            try:
                os.killpg(os.getpgid(s["pid"]), signal.SIGKILL)
            except OSError:
                pass

    unparked = _park(st.get("parked") or [], True) if unpark else []
    restored: list[str] = []
    do_restore = PM2_RESTORE if restore_pm2 is None else restore_pm2
    if do_restore and st.get("pm2_stopped"):
        _pm2_start(st["pm2_stopped"])
        restored = list(st["pm2_stopped"])
    orphans = _sweep_orphans()                # 상태파일 밖의 떠돌이도 함께
    _write_state(None)
    logger.info("[vsource] 정지: %d채널 · pm2 복구 %d · 떠돌이 %d",
                n, len(restored), orphans)
    return {"running": False, "stopped": n, "pm2_restored": restored,
            "orphans_killed": orphans, "cams_restored": unparked}


def status() -> dict:
    """현재 송출 상태. 서버가 재시작돼도 상태파일로 재부착된다."""
    st = _read_state()
    if not st:
        return {"running": False}
    now = time.time()
    t0, cycle = float(st["t0"]), float(st["cycle_sec"])
    live = [s for s in st.get("streams", []) if _alive(s.get("pid"))]
    elapsed = now - t0
    in_cycle = (elapsed % cycle) if (cycle > 0 and elapsed >= 0) else max(0.0, elapsed)
    # 다음 사이클 경계 — "안 끊고 다음 바퀴에 시작" 모드가 이 값을 쓴다.
    if elapsed < 0:
        next_at = t0
    elif cycle > 0 and st.get("loop"):
        next_at = t0 + (int(elapsed // cycle) + 1) * cycle
    else:
        next_at = None
    mode = st.get("mode", "play")
    if mode == "standby":                     # 정지화면 — 시간 개념이 없다
        return {
            "running": bool(live), "mode": "standby",
            "scenario_id": st.get("scenario_id"),
            "scenario_name": st.get("scenario_name"),
            "cycle_sec": cycle, "pm2_stopped": st.get("pm2_stopped", []),
            "parked_cams": st.get("parked", []),
            "streams": [{"path": s["path"], "file": s["file"],
                         "duration_sec": s.get("duration_sec"),
                         "publishing": _alive(s.get("pid")), "pos_sec": None}
                        for s in st.get("streams", [])],
        }
    return {
        "running": bool(live),
        "mode": mode,
        "scenario_id": st.get("scenario_id"),
        "scenario_name": st.get("scenario_name"),
        "t0": t0,
        "cycle_sec": cycle,
        "loop": bool(st.get("loop")),
        "elapsed_sec": round(elapsed, 3),
        "cycle_pos_sec": round(in_cycle, 3),
        "next_cycle_at": next_at,
        "next_cycle_in": (round(next_at - now, 3) if next_at else None),
        "pm2_stopped": st.get("pm2_stopped", []),
        "parked_cams": st.get("parked", []),
        "streams": [
            {"path": s["path"], "file": s["file"],
             "duration_sec": s.get("duration_sec"),
             "publishing": _alive(s.get("pid")),
             # 이 채널이 지금 영상 몇 초 지점을 내보내고 있나 (끝났으면 None)
             "pos_sec": (round(in_cycle, 1)
                         if (s.get("duration_sec") or 0) > in_cycle else None)}
            for s in st.get("streams", [])],
    }
