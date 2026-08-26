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

from system.vsource import overlay
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


# 앞머리(정지화면) 길이 — 카메라가 다시 붙는 데 걸리는 시간을 덮어야 한다.
# 고정 상수를 쓰지 않는다: 대기 송출 때 실제로 붙은 시간을 UI가 재서 넘겨주고,
# 여기서 여유를 얹는다. 측정이 없을 때만 DEFAULT 를 쓴다.
LEAD_STILL_MIN = float(os.environ.get("VSOURCE_LEAD_MIN", "20"))
LEAD_STILL_MAX = float(os.environ.get("VSOURCE_LEAD_MAX", "90"))
LEAD_STILL_DEFAULT = float(os.environ.get("VSOURCE_LEAD_DEFAULT", "45"))
LEAD_MARGIN = float(os.environ.get("VSOURCE_LEAD_MARGIN", "12"))   # 측정치에 얹는 여유
LEAD_DIR = Path("data/vsource_leads")   # 앞머리 mp4 + concat 목록 (캐시)


def lead_seconds(attach_sec: float | None) -> float:
    """앞머리 길이 결정 — 실측 부착시간 + 여유, 범위로 자른다."""
    if attach_sec and attach_sec > 0:
        v = attach_sec + LEAD_MARGIN
    else:
        v = LEAD_STILL_DEFAULT
    return round(max(LEAD_STILL_MIN, min(LEAD_STILL_MAX, v)), 1)


def _build_lead(file: str, still: Path | None, sec: float) -> str | None:
    """정지화면 앞머리 mp4 + concat 목록을 만들고 목록 경로를 돌려준다.

    본영상과 **같은 코덱 파라미터**로 인코딩해야 `-c copy` 로 이어붙일 수 있다
    (실측: 45s 앞머리 인코딩 2.8초, concat 결과 45+181=226.0s).
    재인코딩 없이 붙으므로 송출 중 CPU 부담은 그대로다.
    """
    src = Path(file)
    if not src.is_file() or still is None or not still.is_file():
        return None
    LEAD_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{src.stem}_{int(sec)}s"
    lead_mp4 = LEAD_DIR / f"{tag}.mp4"
    lst = LEAD_DIR / f"{tag}.txt"
    if not lead_mp4.is_file() or lead_mp4.stat().st_mtime < still.stat().st_mtime:
        # key=value 로 읽는다 — 값 순서가 요청 순서와 다르고, profile 은
        # "Constrained Baseline" 처럼 공백을 포함해 위치 기반 파싱이 깨진다(실측).
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate",
             "-of", "default=nk=0:nw=1", str(src)],
            capture_output=True, text=True, timeout=20)
        kv = dict(l.split("=", 1) for l in probe.stdout.strip().splitlines()
                  if "=" in l)
        w, h = kv.get("width"), kv.get("height")
        if not w or not h:
            return None
        try:
            num, den = kv.get("r_frame_rate", "30/1").split("/")
            fps = max(1, round(float(num) / float(den)))
        except (ValueError, ZeroDivisionError):
            fps = 30
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(still),
             "-t", f"{sec:.2f}", "-c:v", "libx264", "-profile:v", "baseline",
             "-pix_fmt", "yuv420p", "-r", str(fps), "-s", f"{w}x{h}",
             "-g", str(fps), "-keyint_min", str(fps), "-sc_threshold", "0",
             "-tune", "stillimage", "-preset", "veryfast", "-an", str(lead_mp4)],
            capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not lead_mp4.is_file():
            logger.warning("[vsource] 앞머리 생성 실패 %s: %s", src.name, r.stderr[:200])
            return None
    lst.write_text(f"file '{lead_mp4.resolve()}'\nfile '{src.resolve()}'\n",
                   encoding="utf-8")
    return str(lst)


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
    stop(restore_pm2=False)                  # 곧 다시 내릴 pm2를 복구하지 않는다
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

    state = {"scenario_id": s.id, "scenario_name": s.name, "mode": "standby",
             "t0": 0.0, "cycle_sec": s.cycle_sec, "loop": False,
             "started_at": time.time(), "pm2_stopped": pm2_stopped,
             "floors": s.floors, "streams": streams}
    _write_state(state)
    logger.info("[vsource] 대기 송출: %s · %d채널 (정지화면)", s.id, len(streams))
    return status()


def start(scenario_id: str, loop: bool = True, cameras=None,
          attach_sec: float | None = None) -> dict:
    """시나리오를 동시 송출로 시작. 이미 돌고 있으면 먼저 정지한다.

    attach_sec — 대기 송출 때 카메라가 다 붙는 데 걸린 실측 시간(초).
    이 값으로 앞머리(정지화면) 길이를 정한다. 훈련 시작은 같은 경로의 퍼블리셔를
    갈아끼우는 것이라 카메라가 전부 떨어졌다 다시 붙는데(실측 27초), 그동안
    본영상이 흘러가면 앞부분이 분석에서 빠진다. 앞머리를 그만큼 깔아 덮는다.
    """
    s = sc.load(scenario_id, cameras=cameras)
    if not s.ok:
        raise ValueError("시나리오에 문제가 있어 시작할 수 없습니다: "
                         + " / ".join(s.problems))
    if s.cycle_sec <= 0:
        raise ValueError("사이클 길이를 정할 수 없습니다(영상 길이 불명).")

    prev = _read_state()                     # 대기 송출에서 넘어오는 경우 pm2는 이미 내려가 있다
    stop(restore_pm2=False)                  # 곧 다시 내릴 pm2를 복구하지 않는다

    # 같은 경로에 퍼블리셔 둘은 공존 못 한다 — 점유 중인 pm2를 먼저 내린다.
    pm2_stopped = _pm2_stop([st.path for st in s.streams])
    if prev and prev.get("pm2_stopped"):     # 직전 단계가 내린 것도 복구 목록에 남긴다
        pm2_stopped = sorted(set(pm2_stopped) | set(prev["pm2_stopped"]))

    lead_sec = lead_seconds(attach_sec)
    # 앞머리는 **T0를 정하기 전에** 다 만들어 둔다. 캐시가 없으면 채널당 2.3초가
    # 걸려(실측), T0 계산 뒤에 만들면 6채널에서 T0가 이미 지나버린다 —
    # 퍼블리셔가 "지나간 사이클"로 보고 다음 바퀴까지 건너뛰어 아무것도 안 나온다.
    leads = {}
    for st in s.streams:
        still = _still_for(st.file, st.path)
        leads[st.path] = _build_lead(st.file, Path(still) if still else None,
                                     lead_sec)
    t0 = time.time() + LEAD_SEC
    streams = []
    for st in s.streams:
        lead_list = leads.get(st.path)
        cmd = [sys.executable, "-m", "system.vsource.publisher",
               "--file", st.file, "--url", rtsp_url(st.path),
               "--t0", f"{t0:.6f}", "--cycle", f"{s.cycle_sec:.6f}"]
        if lead_list:
            cmd += ["--lead", lead_list, "--lead-sec", f"{lead_sec:.2f}"]
        if loop:
            cmd.append("--loop")
        streams.append({"path": st.path, "file": st.file,
                        "pid": _spawn(cmd, st.path),
                        "lead": bool(lead_list),
                        "duration_sec": st.duration_sec})

    has_lead = any(x.get("lead") for x in streams)
    state = {"scenario_id": s.id, "scenario_name": s.name, "mode": "play",
             "t0": t0, "cycle_sec": s.cycle_sec, "loop": bool(loop),
             "started_at": time.time(), "pm2_stopped": pm2_stopped,
             "floors": s.floors, "streams": streams,
             # 앞머리를 깐 경우 본영상 t=0(=경보 시각)은 T0 가 아니라 여기다.
             "lead_sec": lead_sec if has_lead else 0.0,
             "attach_measured_sec": attach_sec,
             "alarm_at": t0 + (lead_sec if has_lead else 0.0)}
    _write_state(state)
    logger.info("[vsource] 시작: %s · %d채널 · T0=%.3f · 앞머리 %.1fs · "
                "경보 %.3f · 사이클 %.1fs · loop=%s",
                s.id, len(streams), t0, state["lead_sec"], state["alarm_at"],
                s.cycle_sec, loop)
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


def stop(restore_pm2: bool | None = None) -> dict:
    """송출 정지. restore_pm2 미지정이면 VSOURCE_PM2_RESTORE(기본 on).

    카메라 상태는 건드리지 않는다 — 리허설은 송출만 담당하고, 훈련 범위는
    `floors` 로 제한한다(카메라를 끄면 디폴트 세팅이 깨지고 워커 재시작에
    23초가 든다).
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
            "orphans_killed": orphans}


def status() -> dict:
    """현재 송출 상태. 서버가 재시작돼도 상태파일로 재부착된다."""
    st = _read_state()
    if not st:
        return {"running": False}
    now = time.time()
    t0, cycle = float(st["t0"]), float(st["cycle_sec"])
    live = [s for s in st.get("streams", []) if _alive(s.get("pid"))]
    lead_sec = float(st.get("lead_sec") or 0.0)
    # 앞머리를 깐 경우 본영상 t=0(=경보 시각)은 T0 가 아니라 T0+앞머리다.
    # 사이클 위치·경계도 전부 이 기준으로 센다.
    alarm_at = float(st.get("alarm_at") or t0)
    elapsed = now - alarm_at
    in_cycle = (elapsed % cycle) if (cycle > 0 and elapsed >= 0) else max(0.0, elapsed)
    # 다음 사이클 경계 — "안 끊고 다음 바퀴에 시작" 모드가 이 값을 쓴다.
    if elapsed < 0:
        next_at = alarm_at
    elif cycle > 0 and st.get("loop"):
        next_at = alarm_at + (int(elapsed // cycle) + 1) * cycle
    else:
        next_at = None
    mode = st.get("mode", "play")
    if mode == "standby":                     # 정지화면 — 시간 개념이 없다
        return {
            "running": bool(live), "mode": "standby",
            "scenario_id": st.get("scenario_id"),
            "scenario_name": st.get("scenario_name"),
            "cycle_sec": cycle, "pm2_stopped": st.get("pm2_stopped", []),
            "floors": st.get("floors", []),
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
        # 앞머리(정지화면) 구간 — 카메라가 다시 붙는 동안 본영상이 안 흐르게 깐다
        "lead_sec": lead_sec,
        "alarm_at": alarm_at,
        "lead_left_sec": (round(alarm_at - now, 2) if now < alarm_at else 0.0),
        "in_lead": now < alarm_at,
        "attach_measured_sec": st.get("attach_measured_sec"),
        "cycle_sec": cycle,
        "loop": bool(st.get("loop")),
        "elapsed_sec": round(elapsed, 3),
        "cycle_pos_sec": round(in_cycle, 3),
        "next_cycle_at": next_at,
        "next_cycle_in": (round(next_at - now, 3) if next_at else None),
        "pm2_stopped": st.get("pm2_stopped", []),
        "floors": st.get("floors", []),
        "streams": [
            {"path": s["path"], "file": s["file"],
             "duration_sec": s.get("duration_sec"),
             "publishing": _alive(s.get("pid")),
             # 이 채널이 지금 영상 몇 초 지점을 내보내고 있나 (끝났으면 None)
             "pos_sec": (round(in_cycle, 1)
                         if (s.get("duration_sec") or 0) > in_cycle else None)}
            for s in st.get("streams", [])],
    }


# ------------------------------------------------------------ 리허설 매핑 오버레이
def active_scenario_id() -> str | None:
    """지금 도는 시나리오 id — 안 돌면 None."""
    st = _read_state()
    if not st or not any(_alive(x.get("pid")) for x in st.get("streams", [])):
        return None
    return st.get("scenario_id")


def active_overlay() -> dict[str, dict]:
    """리허설이 도는 동안만 얹을 카메라 오버레이.

    안 돌면 빈 dict → production 설정이 그대로 쓰인다.
    """
    sid = active_scenario_id()
    return overlay.load(sid) if sid else {}
