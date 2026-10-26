#!/usr/bin/env python3
"""CJ 실훈련(2026-10-22) 발표 영상 패키징 — 층별 demo 렌더 → 폴더별 발표용 영상 + README.

입력: tools/demo_viz.py 가 만든 층별 렌더 `ALL_CJ1022_<tag>[_heat].mp4` (+ 사이드카 .json).
      앞 4.0 s 인트로 뒤 t=0 = 경보(훈련 시작). 세션 id 에 의존하지 않는다 — 파일 이름만 본다.

출력 (점판·히트맵판 각각):
    1_피난층종료/16층_피난층종료_0810.mp4      16F 단독, 경보 후 0~490 s
    1_피난층종료/17층_피난층종료_0330.mp4      17F 단독, 0~210 s
    2_최종종료/16층→1층_최종종료_0910.mp4      16F1F_16F | 16F1F_1F 좌우, 0~550 s
    2_최종종료/17층→1층_최종종료_1245.mp4      17F1F_17F | 17F1F_1F 좌우, 0~765 s
    3_전체/전체3개층_1245.mp4                 3F_17F · 3F_16F / 3F_1F · 안내 2×2, 0~765 s
    README.md                                 색인

각 영상: 인트로 4 s 유지 → 컷 시각에서 마지막 프레임을 HOLD 초 정지 + 라벨
        → (있으면) 리포트 클립(tools/report_card.py 산출) 이어붙임.

리포트 클립 찾는 순서 (--report-dir D):
    D/<출력 파일 이름>.mp4  (예: 16층_피난층종료_0810_히트맵.mp4)
    D/<점판 파일 이름>.mp4  (히트맵판도 점판 리포트를 공유)
    D/<출력 id>[_heat].mp4  (예: 16F_floorend_heat.mp4 → 16F_floorend.mp4)
  또는 --report <id>=<path> 로 직접 지정(점·히트맵 공통). 없으면 조용히 건너뛴다.

사용:
    python tools/cj1022_package.py --dry-run
    python tools/cj1022_package.py                       # 전부 (기본 src = <out>/_층별원본)
    python tools/cj1022_package.py --only 16F_floorend --only 3F_all --variant dots
    python tools/cj1022_package.py --src-dir results/demo/05_CJ1022 --out-dir /tmp/pkg_test
    python tools/cj1022_package.py --readme-only
"""
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / "results/demo/05_CJ1022"
SRC_NAME = "_층별원본"           # <out-dir> 아래 층별 원본 폴더
ANSWERS = REPO / "media/vsource/cj/drill-1022/answers.json"
FONT = "/usr/share/fonts/truetype/nanum/NanumSquareB.ttf"

INTRO_SEC = 4.0      # demo_viz.INTRO_SEC — 파일 t=4.0 이 경보 후 0:00
HOLD_SEC = 3.0       # 컷 지점 마지막 프레임 정지 길이
TILE_W, TILE_H = 1920, 1102

# 이벤트 시각(경보 후 초) — answers.json 이 있으면 거기서 읽고, 없으면 이 값
EVENTS_DEFAULT = {
    "16F_floor_end": 490, "16F_final": 550,
    "17F_floor_end": 210, "17F_final": 765,
}

VARIANTS = {"dots": ("", ""), "heat": ("_heat", "_히트맵"),
            "nogrid": ("_nogrid", "_그리드없음")}   # nogrid: 점판에서 우측 카메라 그리드만 뺀 판   # 입력 접미사, 출력 접미사


def mmss(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 60:02d}:{s % 60:02d}"


def load_events() -> dict:
    ev = dict(EVENTS_DEFAULT)
    try:
        d = json.loads(ANSWERS.read_text())
        for f in d.get("floors", []):
            key = {"16층": "16F", "17층": "17F"}.get(f.get("name"))
            if not key:
                continue
            if "floor_end_sec" in f:
                ev[f"{key}_floor_end"] = int(f["floor_end_sec"])
            if "final_end_sec" in f:
                ev[f"{key}_final"] = int(f["final_end_sec"])
    except (OSError, ValueError) as e:
        print(f"[warn] answers.json 못 읽음({e}) — 기본 이벤트 시각 사용", file=sys.stderr)
    return ev


@dataclass
class Output:
    id: str
    folder: str
    stem: str               # 점판 파일 이름(확장자 없음)
    layout: str             # single | hstack | grid3
    tags: list              # 입력 태그 (grid3: [17F, 16F, 1F])
    end_sec: float          # 경보 후 컷 시각
    label: str              # 컷 정지 화면 라벨
    title: str              # README 용 한 줄
    question: str           # README: 이 영상으로 답하는 질문
    floors: str
    short_tag: str = ""     # grid3: 먼저 끝나는 타일(16층) 태그
    short_note: str = ""


def build_outputs(ev: dict) -> list[Output]:
    e16, f16 = ev["16F_floor_end"], ev["16F_final"]
    e17, f17 = ev["17F_floor_end"], ev["17F_final"]
    return [
        Output("16F_floorend", "1_피난층종료", f"16층_피난층종료_{mmss(e16).replace(':', '')}",
               "single", ["16F"], e16, f"16층 피난층 종료 · {mmss(e16)}",
               "16층 단독 — 경보부터 16층 피난층 종료까지",
               "16층 사람들이 비상구로 얼마나 빨리·어디로 빠져나갔나 (16층만)", "16층"),
        Output("17F_floorend", "1_피난층종료", f"17층_피난층종료_{mmss(e17).replace(':', '')}",
               "single", ["17F"], e17, f"17층 피난층 종료 · {mmss(e17)}",
               "17층 단독 — 경보부터 17층 피난층 종료까지",
               "17층 사람들이 비상구로 얼마나 빨리·어디로 빠져나갔나 (17층만)", "17층"),
        Output("16F1F_final", "2_최종종료", f"16층→1층_최종종료_{mmss(f16).replace(':', '')}",
               "hstack", ["16F1F_16F", "16F1F_1F"], f16,
               f"16층 → 1층 최종 종료 · {mmss(f16)}",
               "16층 · 1층 좌우 합본 — 16층 인원이 1층 밖으로 나갈 때까지",
               "16층에서 출발한 사람들이 계단을 내려와 1층 출구로 나가기까지 전 과정", "16층 · 1층"),
        Output("17F1F_final", "2_최종종료", f"17층→1층_최종종료_{mmss(f17).replace(':', '')}",
               "hstack", ["17F1F_17F", "17F1F_1F"], f17,
               f"17층 → 1층 최종 종료 · {mmss(f17)}",
               "17층 · 1층 좌우 합본 — 17층 인원이 1층 밖으로 나갈 때까지",
               "17층에서 출발한 사람들이 계단을 내려와 1층 출구로 나가기까지 전 과정", "17층 · 1층"),
        Output("3F_all", "3_전체", f"전체3개층_{mmss(f17).replace(':', '')}",
               "grid3", ["3F_17F", "3F_16F", "3F_1F"], f17,
               f"전체 3개 층 종료 · {mmss(f17)}",
               "17층 · 16층 / 1층 · 안내 2×2 합본 — 건물 전체",
               "건물 전체(3개 층)가 같은 시계로 어떻게 움직였나 — 층 간 시차·1층 혼잡",
               "17층 · 16층 · 1층", short_tag="3F_16F",
               short_note=f"16층 영상 종료 · {mmss(f16)} (16층 인원 1층 탈출)"),
    ]


# ───────────────────────── ffprobe ─────────────────────────
def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate:format=duration",
         "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    st = d["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    return {"w": int(st["width"]), "h": int(st["height"]),
            "fps": float(num) / float(den), "dur": float(d["format"]["duration"])}


def sidecar(path: Path) -> dict:
    try:
        return json.loads(path.with_suffix(".json").read_text())
    except (OSError, ValueError):
        return {}


# ───────────────────────── 필터그래프 ─────────────────────────
def esc_path(p: Path) -> str:
    """drawtext textfile= 값용 이스케이프 (필터그래프 안)."""
    return str(p).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def drawtext(tf: Path, size: int, color: str, x: str, y: str, enable: str = "",
             box: str = "") -> str:
    s = (f"drawtext=fontfile={FONT}:textfile='{esc_path(tf)}':fontsize={size}:"
         f"fontcolor={color}:x={x}:y={y}")
    if box:
        s += f":box=1:boxcolor={box}:boxborderw=24"
    if enable:
        s += f":enable='{enable}'"
    return s


@dataclass
class Job:
    out: Output
    variant: str
    inputs: list
    dst: Path
    report: Path | None
    cmd: list = field(default_factory=list)
    expect_sec: float = 0.0
    gpu: int = 0


def plan_job(o: Output, variant: str, src: Path, out_dir: Path, report_dir: Path | None,
             report_map: dict, work: Path, gpu: int) -> Job:
    isfx, osfx = VARIANTS[variant]
    inputs = [src / f"ALL_CJ1022_{t}{isfx}.mp4" for t in o.tags]
    for p in inputs:
        if not p.exists():
            raise FileNotFoundError(p)
    dst = out_dir / o.folder / f"{o.stem}{osfx}.mp4"

    # 리포트 클립
    report = None
    cands = []
    if o.id in report_map:
        cands.append(Path(report_map[o.id]))
    if report_dir:
        cands += [report_dir / f"{o.stem}{osfx}.mp4", report_dir / f"{o.stem}.mp4",
                  report_dir / f"{o.id}{isfx}.mp4", report_dir / f"{o.id}.mp4"]
    for c in cands:
        if c.exists() and c.stat().st_size > 0:
            report = c
            break

    info = [probe(p) for p in inputs]
    fps = info[0]["fps"]
    # 파일 기준 컷 시각 — +1프레임: 시계가 정확히 mm:ss 를 보여주는 프레임까지 포함
    T = INTRO_SEC + o.end_sec + 1.0 / fps
    tw, th = TILE_W, TILE_H

    # 라벨 텍스트 파일
    jw = work / f"{o.id}_{variant}"
    jw.mkdir(parents=True, exist_ok=True)
    tf_label = jw / "label.txt"
    tf_label.write_text(o.label)

    args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    for p in inputs:
        args += ["-i", str(p)]
    fc = []
    norm = lambda i: (f"[{i}:v]fps={fps:g},scale={tw}:{th}:force_original_aspect_ratio=decrease,"
                      f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
                      f"trim=end={T:.3f},setpts=PTS-STARTPTS")

    if o.layout == "single":
        fc.append(f"{norm(0)}[m]")
        cut = min(T, info[0]["dur"])
        W, H = tw, th
    elif o.layout == "hstack":
        fc.append(f"{norm(0)}[a]")
        fc.append(f"{norm(1)}[b]")
        fc.append("[a][b]hstack=inputs=2:shortest=1[m]")
        cut = min(T, info[0]["dur"], info[1]["dur"])
        W, H = 2 * tw, th
    elif o.layout == "grid3":
        i17, i16, i1 = 0, 1, 2
        cut = min(T, info[i17]["dur"], info[i1]["dur"])
        d16 = info[i16]["dur"]
        tf_short = jw / "short.txt"
        tf_short.write_text(o.short_note)
        fc.append(f"{norm(i17)}[a]")
        # 16층: 먼저 끝남 → 마지막 프레임 정지 + 어둡게 + 안내
        fc.append(f"{norm(i16)},tpad=stop_mode=clone:stop_duration={max(0.0, cut - d16) + 1:.3f},"
                  f"trim=end={cut:.3f},"
                  f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.45:t=fill:enable='gte(t,{d16 - 0.05:.3f})',"
                  + drawtext(tf_short, 46, "white", "(w-text_w)/2", "(h-text_h)/2",
                             enable=f"gte(t,{d16 - 0.05:.3f})", box="black@0.6") + "[b]")
        fc.append(f"{norm(i1)}[c]")
        # 안내 패널
        meta = [sidecar(p) for p in inputs]
        sess = meta[0].get("session_id", "?")
        lines = [
            (60, "white", "CJ제일제당센터 피난대피훈련"),
            (44, "0xC8C8D0", "전체 3개 층 합본 (17층 · 16층 · 1층)"),
            (34, "0x9A9AA4", "2026-10-22 · 00:00 = 경보(훈련 시작 14:59:55)"),
            (30, "0x9A9AA4", "세 화면 시계 동기 — 같은 순간의 세 층"),
            (30, "0x9A9AA4", f"16층 화면은 {mmss(EV['16F_final'])}(16층 인원 1층 탈출)에서 멈춘다"),
            (30, "0x9A9AA4", "지표는 층별 값 · 임계값 dt_hold=2.0 · "
                             + {"heat": "히트맵판(밀도)", "nogrid": "점판(원 관측) · 카메라 그리드 제외"}.get(variant, "점판(원 관측)")),
            (26, "0x7A7A84", f"세션 {sess}"),
        ]
        dts, y = [], 300
        for k, (sz, col, txt) in enumerate(lines):
            tf = jw / f"panel{k}.txt"
            tf.write_text(txt)
            dts.append(drawtext(tf, sz, col, "90", str(y)))
            y += int(sz * 1.9) + (20 if k == 1 else 0)
        args += ["-f", "lavfi", "-i", f"color=c=0x141418:s={tw}x{th}:r={fps:g}:d={cut:.3f}"]
        fc.append(f"[3:v]{','.join(dts)},setsar=1[t]")
        fc.append(f"[a][b][c][t]xstack=inputs=4:layout=0_0|{tw}_0|0_{th}|{tw}_{th}:shortest=1[m]")
        W, H = 2 * tw, 2 * th
    else:
        raise ValueError(o.layout)

    # 컷 지점 정지 + 라벨
    en = f"gte(t,{cut - 0.02:.3f})"
    if o.layout == "grid3":
        # 2×2 는 가운데 띠가 지표 줄을 가리므로 안내 패널(우하) 위쪽에 크게
        fsize, bh, by = 72, 190, th + 70
        hold = (f"drawbox=x={tw}:y={by}:w={tw}:h={bh}:color=0xB4282E@0.85:t=fill:enable='{en}',"
                + drawtext(tf_label, fsize, "white", f"{tw}+({tw}-text_w)/2", f"{by}+{bh}/2-text_h/2", enable=en))
    else:
        fsize = 64 if W > tw else 52
        bh = int(fsize * 2.6)
        hold = (f"drawbox=x=0:y=ih*0.36:w=iw:h={bh}:color=black@0.6:t=fill:enable='{en}',"
                + drawtext(tf_label, fsize, "white", "(w-text_w)/2", f"h*0.36+{bh}/2-text_h/2", enable=en))
    fc.append(f"[m]trim=end={cut:.3f},tpad=stop_mode=clone:stop_duration={HOLD_SEC},{hold},format=yuv420p[main]")
    total = cut + HOLD_SEC
    last = "[main]"
    if report is not None:
        ri = len(inputs) + (1 if o.layout == "grid3" else 0)
        args += ["-i", str(report)]
        fc.append(f"[{ri}:v]fps={fps:g},scale={W}:{H}:force_original_aspect_ratio=decrease,"
                  f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x141418,setsar=1,format=yuv420p,"
                  f"setpts=PTS-STARTPTS[rep]")
        fc.append("[main][rep]concat=n=2:v=1:a=0[outv]")
        last = "[outv]"
        total += probe(report)["dur"]

    args += ["-filter_complex", ";".join(fc), "-map", last, "-an",
             "-c:v", "h264_nvenc", "-gpu", str(gpu), "-preset", "p4", "-rc", "vbr",
             "-cq", "24", "-b:v", "0", "-r", f"{fps:g}",
             "-movflags", "+faststart", str(dst.with_suffix(".part.mp4"))]
    return Job(o, variant, inputs, dst, report, args, total, gpu)


def run_job(j: Job) -> tuple[Job, float, str]:
    j.dst.parent.mkdir(parents=True, exist_ok=True)
    part = j.dst.with_suffix(".part.mp4")
    t0 = time.time()
    r = subprocess.run(j.cmd, capture_output=True, text=True)
    if r.returncode != 0:
        part.unlink(missing_ok=True)
        return j, time.time() - t0, r.stderr.strip()[-2000:] or f"rc={r.returncode}"
    part.replace(j.dst)
    return j, time.time() - t0, ""


# ───────────────────────── README ─────────────────────────
def exit_name(eid: str) -> str:
    """1층 exit-N → 출구N+1 (README 표기, 카메라-이상-기록·채점결과 문서와 맞춤)."""
    if eid.startswith("exit-") and eid[5:].isdigit():
        return f"출구{int(eid[5:]) + 1}"
    return eid


def fmt_dur(sec: float) -> str:
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def write_readme(out_dir: Path, src: Path, outputs: list[Output]) -> Path:
    rel_src = src.relative_to(out_dir) if src.is_relative_to(out_dir) else Path(SRC_NAME)
    L = []
    L.append("# 05_CJ1022 — CJ제일제당센터 실훈련(2026-10-22) 발표 영상\n")
    L.append("각본 리허설(01~04)과 달리 **실제 피난훈련 통녹화**다. 화면 구성은 모든 영상이 같다 — "
             "좌 2D 도면 · 우 카메라 그리드(원본 fps) · 하 경보 후 경과 시간 + 4대 지표(IDR·SEI·CBS·EPFI) + 카운팅.\n")
    L.append("이 README 와 아래 폴더는 `python tools/cj1022_package.py` 가 만든다(손으로 고치지 말 것 — 다시 돌리면 덮인다).\n")

    L.append("## 무엇을 보면 되나\n")
    L.append("| 질문 | 볼 영상 |")
    L.append("|---|---|")
    for o in outputs:
        L.append(f"| {o.question} | `{o.folder}/{o.stem}.mp4` |")
    L.append("| 사람이 어디에 몰렸나(밀집) | 위 영상의 `_히트맵` 판 |")
    L.append("| 카메라 화면 없이 도면만 크게 | `_그리드없음` 판 (16층·17층 단독 · 전체 3개 층) |")
    L.append(f"| 한 층만 따로, 원 길이 그대로 | `{rel_src}/` 층별 원본 |\n")

    L.append("## 폴더\n")
    L.append("| 폴더 | 영상 | 내용 | 경보 후 구간 | 길이(대략) |")
    L.append("|---|---|---|---|---:|")
    for o in outputs:
        extra = 0.0
        for v, (_, osfx) in VARIANTS.items():
            p = out_dir / o.folder / f"{o.stem}{osfx}.mp4"
            if p.exists():
                try:
                    extra = probe(p)["dur"]
                except Exception:
                    pass
                break
        dur = fmt_dur(extra) if extra else fmt_dur(INTRO_SEC + o.end_sec + HOLD_SEC)
        sfx = [VARIANTS[v][1] for v in ("heat", "nogrid")
               if (out_dir / o.folder / f"{o.stem}{VARIANTS[v][1]}.mp4").exists()]
        sfx_s = f"[{' · '.join(sfx)}]" if sfx else ""
        L.append(f"| `{o.folder}/` | `{o.stem}{sfx_s}.mp4` | {o.title} | 00:00 ~ {mmss(o.end_sec)} | {dur} |")
    L.append(f"| `{rel_src}/` | `ALL_CJ1022_<tag>[_heat · _nogrid].mp4` + `.json` | 위 영상의 재료(층별 렌더, demo_viz 산출) | 세션 전체 | |\n")

    L.append("### 영상 한 편의 흐름\n")
    L.append(f"1. **인트로 {INTRO_SEC:.0f}초** — 도면·피난경로 소개(시계 0:00 이전).")
    L.append("2. **본편** — 하단 시계 0:00 = 경보(훈련 시작, 실제 14:59:55). 지표는 시간에 따라 쌓인다.")
    L.append(f"3. **종료 정지 {HOLD_SEC:.0f}초** — 컷 시각의 마지막 화면을 멈추고 라벨(예: \"16층 피난층 종료 · 08:10\")을 띄운다.")
    L.append("4. **리포트**(있을 때만) — 우리 UI 의 결과 리포트 화면 클립(`tools/report_card.py`) → "
             "**피난훈련 평가 보고서** 슬라이드(`tools/cj1022_eval_report.py` — 종합 평가 · 피난 타임라인 · "
             "출구별 분석(출구별 분담·출구 앞 CBS) · 건물 종합(여러 층일 때)).\n")

    L.append("### 용어\n")
    L.append("- **피난층 종료**: 그 층에서 마지막 인원이 비상구로 빠져나간 시각(사람이 영상으로 판정, "
             "`media/vsource/cj/drill-1022/answers.json`). 16층 08:10 · 17층 03:30.")
    L.append("- **최종 종료**: 그 층 인원이 1층 밖으로 나간 시각. 16층 09:10 · 17층 12:45(= 녹화 끝).")
    L.append("- **합본**: 여러 층의 영상을 한 화면에 나란히(좌우 또는 2×2) 붙인 것. **시계가 동기**돼 있어 "
             "같은 순간의 여러 층을 함께 본다. 각 칸의 지표는 **그 층의 값**이다(건물 합산 아님).")
    L.append("- **점판 / 히트맵판(`_히트맵`)**: 점판은 추적된 사람을 원 관측 그대로 점으로(④ 리플레이와 같은 그림), "
             "히트맵판은 같은 데이터를 밀도(반경 2.5 m)로 칠한다 — 몰림을 볼 때.")
    L.append("- **그리드없음판(`_그리드없음`)**: 점판에서 우측 카메라 그리드만 뺀 것. 도면이 화면 가로 전체를 쓰고"
             "(도면 흰 여백은 잘라 키움) 인트로 소개 글은 도면 왼쪽 위에 얹는다. 지표·컷·리포트는 점판과 같다"
             "(`demo_viz.py --no-grid` → `cj1022_package.py --variant nogrid`).\n")

    L.append("## 원천 세션 (④ 리플레이 → 건물 훈련)\n")
    L.append("층별 원본 사이드카(`.json`)의 `session_id` 에서 읽었다.\n")
    L.append("| 층별 원본 | 세션 | 라벨 | SEI | EPFI | CBS | IDR 개시 | 출구 통과 |")
    L.append("|---|---|---|---:|---:|---:|---:|---|")
    tags = []
    for o in outputs:
        for t in o.tags:
            if t not in tags:
                tags.append(t)
    for t in tags:
        m = sidecar(src / f"ALL_CJ1022_{t}.mp4")
        if not m:
            L.append(f"| `{t}` | (사이드카 없음) | | | | | | |")
            continue
        zm = (m.get("zone_metrics") or [{}])[0]
        idr = f"{zm['response_delay_sec']:.0f} s" if zm.get("response_delay_sec") is not None else "—"
        ex = " · ".join(f"{exit_name(e['exit_id'])} {e['actual_count']}" for e in m.get("exit_metrics", []))
        f = lambda k, n=1: f"{m[k]:.{n}f}" if isinstance(m.get(k), (int, float)) else "—"
        L.append(f"| `{t}` | `{m.get('session_id', '?')}` | {m.get('label', '')} | {f('sei')} | "
                 f"{f('epfi_avg')} | {f('cbs_total', 2)} | {idr} | {ex} |")
    L.append("")
    L.append("> 지표는 **세션 전체** 값이다(사이드카 기준). `1_피난층종료` 영상은 피난층 종료 시각에서 잘리므로 "
             "화면 마지막 숫자가 표의 값과 조금 다를 수 있다. 같은 층은 세션이 달라도 값이 같다"
             "(프레임 손실 없는 녹화는 결정적).\n")

    L.append("## 설정\n")
    L.append("- 지표: **4대 지표 통합 표시**(`demo_viz.py --metric ALL`) — 한 지표를 주인공으로 세우지 않는다.")
    L.append("- 임계값 **dt_hold 2.0** (배포 기본 3.0 과 다름 — 화면 좌하단에 표기). 리플레이 편집본 저장값.")
    L.append("- 점: **원 관측 그대로**(raw) — 각본 영상처럼 N 명으로 이어 붙이지 않는다.")
    L.append("- 출구 카운트: **선(line) 통과 카운트**, 복도경로. 전 세션 프레임 손실 없이(`VSOURCE_FILE_NODROP=1`) 녹화.\n")

    L.append("## 알려진 한계\n")
    L.append("- **1층 cam16** 은 현장에서 \"사용 불가\"로 표시됐지만 매핑해 두고 **그대로 포함**했다(0~42초 검정). "
             "1층 지표는 참고용.")
    L.append("- **1층 cam1·cam4·cam8** 은 설치 중 화면·화각 변경 구간을 **검정으로 덮었다** "
             "(cam1 0:00~2:59 · cam4 0:00~3:52, 6:56~ · cam8 0:00~0:43, 9:04~). 그 구간은 관측 공백 — "
             "특히 출구2(북쪽)는 cam4 유효 구간 밖 통과를 놓칠 수 있다. "
             "→ `media/vsource/cj/drill-1022/docs/카메라-이상-기록.md`")
    L.append("- 출구 카운트 정답(사람 육안 근사) 대비 채점 → `media/vsource/cj/drill-1022/docs/출구-카운트-채점결과.md`.")
    L.append("- 3개 층 합본의 16층 칸은 09:10 에서 멈춘다(16층 녹화가 거기서 끝남).\n")

    L.append("## 다시 만들기\n")
    L.append("```bash\n# 1) 층별 원본 (예: 전체 3개 층 세션의 1층, 점판)")
    L.append("python tools/demo_viz.py --session <세션> --floor floor9 --metric ALL \\")
    L.append("  --package cj-drill-1022 --scenario scenario_all --tag 3F_1F \\")
    L.append("  --title \"CJ제일제당센터 · 전체 3개 층 · 1층\" --th '{\"dt_hold\":2.0}' --map dots \\")
    L.append(f"  --out results/demo/05_CJ1022/{SRC_NAME}")
    L.append("# 2) 패키징 (+ 리포트 클립 있으면 --report-dir)")
    L.append("python tools/cj1022_package.py [--report-dir <리포트 클립 폴더>]\n```")
    L.append("mp4 는 git 에 안 들어간다(`results/demo/*/*.mp4` ignore).")
    p = out_dir / "README.md"
    p.write_text("\n".join(L) + "\n")
    return p


EV = load_events()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--src-dir", type=Path, default=None, help="층별 원본 폴더 (기본 <out-dir>/_층별원본)")
    ap.add_argument("--report-dir", type=Path, default=None)
    ap.add_argument("--report", action="append", default=[], metavar="ID=PATH")
    ap.add_argument("--only", action="append", default=[],
                    help="출력 id 또는 파일 이름 일부 (반복 가능): 16F_floorend 17F_floorend 16F1F_final 17F1F_final 3F_all")
    ap.add_argument("--variant", choices=["dots", "heat", "nogrid", "both"], default="both")
    ap.add_argument("--jobs", type=int, default=4, help="동시 인코딩 수 (GPU 2장에 번갈아 배정)")
    ap.add_argument("--gpus", default="0,1")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-readme", action="store_true")
    ap.add_argument("--readme-only", action="store_true")
    ap.add_argument("--skip-existing", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)

    out_dir = a.out_dir.resolve()
    src = (a.src_dir or out_dir / SRC_NAME).resolve()
    outputs = build_outputs(EV)
    sel = [o for o in outputs if not a.only or any(s == o.id or s in o.stem for s in a.only)]
    if not sel:
        print("선택된 출력 없음", file=sys.stderr)
        return 2
    if a.readme_only:
        print(write_readme(out_dir, src, outputs))
        return 0

    report_map = dict(r.split("=", 1) for r in a.report)
    gpus = [int(g) for g in a.gpus.split(",") if g.strip()]
    variants = ["dots", "heat"] if a.variant == "both" else [a.variant]
    work = Path(tempfile.mkdtemp(prefix="cj1022_pkg_"))
    jobs, k = [], 0
    for o in sel:
        for v in variants:
            try:
                j = plan_job(o, v, src, out_dir, a.report_dir, report_map, work, gpus[k % len(gpus)])
            except FileNotFoundError as e:
                print(f"[skip] {o.id}/{v}: 입력 없음 {e}", file=sys.stderr)
                continue
            if a.skip_existing and j.dst.exists():
                print(f"[have] {j.dst}")
                continue
            jobs.append(j)
            k += 1

    for j in jobs:
        print(f"── {j.out.id}/{j.variant} → {j.dst.relative_to(out_dir) if j.dst.is_relative_to(out_dir) else j.dst}"
              f"  (예상 {j.expect_sec:.1f} s, gpu{j.gpu}, 리포트 {j.report or '없음'})")
        if a.dry_run:
            print("   " + shlex.join(j.cmd))
    if a.dry_run:
        print(f"[dry-run] {len(jobs)} 개")
        shutil.rmtree(work, ignore_errors=True)
        return 0

    fails = 0
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        futs = [ex.submit(run_job, j) for j in jobs]
        for f in as_completed(futs):
            j, dt, err = f.result()
            if err:
                fails += 1
                print(f"[FAIL] {j.dst.name} ({dt:.0f}s)\n{err}", file=sys.stderr)
            else:
                d = probe(j.dst)["dur"]
                flag = "" if abs(d - j.expect_sec) < 0.5 else f"  [!] 예상 {j.expect_sec:.1f}"
                print(f"{time.strftime('%T')} [ok] {j.dst.name}  {d:.2f}s  ({dt:.0f}s 소요){flag}")
    shutil.rmtree(work, ignore_errors=True)
    if not a.no_readme:
        print(write_readme(out_dir, src, outputs))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
