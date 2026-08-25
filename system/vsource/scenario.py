"""시나리오 — 어떤 영상을 어느 RTSP 경로로 내보낼지의 정의·검증.

정의는 `data/scenarios/<id>.json`(git 추적), 영상은 `media/vsource/<id>/`(HF 보관).
설계: docs/architecture/08-훈련영상-동기송출-설계.md §7

검증이 하는 일 — 송출 전에 "이 시나리오로 리허설이 되는가"를 미리 알려준다.
파일이 없거나 코덱이 안 맞으면 송출은 되는데 카메라가 못 받는 상황이 생겨서,
그때 원인을 찾느라 시간을 버린다.
"""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SCENARIO_DIR = Path("data/scenarios")
MEDIA_DIR = Path("media/vsource")

# 사이클(전 채널이 함께 되감기는 주기)을 자동 산출할 때 가장 긴 영상 뒤에 두는 여유.
# 0이면 가장 긴 채널이 끝나는 순간 곧바로 되감겨, 마지막 프레임이 잘린 것처럼 보인다.
CYCLE_PAD_SEC = 2.0


@dataclass
class Stream:
    """채널 1개 — 영상 파일 하나를 RTSP 경로 하나로."""
    path: str                       # RTSP 경로 (rtsp://host:8554/<path>)
    file: str                       # 영상 파일 (레포 루트 기준 상대경로)
    duration_sec: float | None = None
    fps: float | None = None
    codec: str | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass
class Scenario:
    id: str
    name: str
    streams: list[Stream]
    cycle_sec: float = 0.0          # 0이면 로드 시 자동 산출
    note: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.streams) and all(s.ok for s in self.streams)

    @property
    def problems(self) -> list[str]:
        return [f"{s.path}: {p}" for s in self.streams for p in s.problems]

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "note": self.note,
            "cycle_sec": round(self.cycle_sec, 3),
            "ok": self.ok, "problems": self.problems,
            "streams": [
                {"path": s.path, "file": s.file, "ok": s.ok,
                 "duration_sec": (round(s.duration_sec, 3)
                                  if s.duration_sec is not None else None),
                 "fps": (round(s.fps, 3) if s.fps is not None else None),
                 "codec": s.codec, "problems": s.problems}
                for s in self.streams],
        }


_probe_cache: dict[tuple[str, float, int], tuple] = {}


def _probe(p: Path) -> tuple[float | None, float | None, str | None, str | None]:
    """(길이초, fps, 코덱, 오류) — mtime·크기로 캐시해 목록 조회를 싸게 한다."""
    try:
        st = p.stat()
    except OSError:
        return None, None, None, "파일 없음"
    key = (str(p), st.st_mtime, st.st_size)
    if key in _probe_cache:
        return _probe_cache[key]
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate,codec_name",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=0", str(p)],
            capture_output=True, text=True, timeout=20)
        if out.returncode != 0:
            res = (None, None, None, "ffprobe 실패")
        else:
            kv = dict(l.split("=", 1) for l in out.stdout.strip().splitlines()
                      if "=" in l)
            dur = float(kv.get("duration", 0)) or None
            codec = kv.get("codec_name")
            rate = kv.get("r_frame_rate", "0/1")
            try:
                num, den = rate.split("/")
                fps = float(num) / float(den) if float(den) else None
            except ValueError:
                fps = None
            res = (dur, fps, codec, None)
    except (subprocess.TimeoutExpired, OSError):
        res = (None, None, None, "ffprobe 호출 불가")
    _probe_cache[key] = res
    return res


def _validate(s: Stream) -> None:
    p = Path(s.file)
    if not p.is_file():
        s.problems.append(f"영상 파일 없음: {s.file}")
        return
    dur, fps, codec, err = _probe(p)
    s.duration_sec, s.fps, s.codec = dur, fps, codec
    if err:
        s.problems.append(err)
        return
    if not dur or dur <= 0:
        s.problems.append("길이를 읽을 수 없음")
    # 코덱은 카메라가 받을 수 있어야 한다 — tools/rtsp/check_video.sh 와 같은 기준.
    if codec and codec != "h264":
        s.problems.append(f"H.264가 아님({codec}) — tools/rtsp/encode_video.sh 로 변환 필요")


def load(scenario_id: str) -> Scenario:
    """시나리오 1개 로드 + 검증. 없으면 FileNotFoundError."""
    f = SCENARIO_DIR / f"{scenario_id}.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    streams = [Stream(path=str(s["path"]), file=str(s["file"]))
               for s in d.get("streams", [])]
    for s in streams:
        _validate(s)
    sc = Scenario(id=d.get("id", scenario_id), name=d.get("name", scenario_id),
                  streams=streams, cycle_sec=float(d.get("cycle_sec") or 0),
                  note=d.get("note", ""))
    if sc.cycle_sec <= 0:
        # 자동: 가장 긴 영상 + 여유. 길이가 제각각이라 채널별 루프는 못 쓰고
        # 전 채널이 이 주기로 함께 되감긴다 (ADR 08 §4).
        durs = [s.duration_sec for s in streams if s.duration_sec]
        sc.cycle_sec = math.ceil(max(durs) + CYCLE_PAD_SEC) if durs else 0.0
    return sc


def load_all() -> list[Scenario]:
    """data/scenarios/*.json 전부 (id 순)."""
    if not SCENARIO_DIR.is_dir():
        return []
    out = []
    for f in sorted(SCENARIO_DIR.glob("*.json")):
        try:
            out.append(load(f.stem))
        except Exception as e:                      # 깨진 정의 하나가 목록을 못 막게
            out.append(Scenario(id=f.stem, name=f"{f.stem} (로드 실패)",
                                streams=[], note=str(e)))
    return out
