#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""운영 뷰 녹화 + 원본 카메라 그리드를 **싱크 맞춰** 가로로 붙인다.

`results/rehearsal_sessions/scenario_15_full_with_grid.mp4`(2240×720) 를 다시 만드는
도구다. 좌 1280×720 은 운영 뷰 화면 녹화, 우 960×720 은 그 리허설이 쓴 원본
클립을 격자로 깐 것이다 — 맵의 점과 영상 속 사람이 같이 보여야 설득이 된다.

싱크는 **눈대중이 아니라 기록**으로 맞춘다. `record_opsview.py` 가 남긴 사이드카의
`clip_t0_at_rec_sec`(클립 t=0 이 녹화 몇 초 지점인가)를 그대로 쓴다. 녹화는 브라우저를
띄우고 토글을 맞춘 뒤 재생을 시작하므로, 앞쪽 몇 초는 클립이 아직 안 돈다.

    python tools/compose_grid.py --rec results/rehearsal_sessions/scenario_15_full.mp4 \\
           --clips media/vsource/cj/rehearsal/scenario_15_full

클립은 전부 같은 길이(452.47s)에 **t=0 정렬**돼 있다(카메라가 없는 구간은 검정).
그래서 격자는 그냥 같은 시각에서 잘라 붙이면 된다.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def cam_no(p: Path) -> int:
    m = re.search(r"(\d+)", p.stem)
    return int(m.group(1)) if m else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rec", required=True, help="운영 뷰 녹화 mp4")
    ap.add_argument("--clips", required=True, help="원본 클립 폴더(camN.mp4)")
    ap.add_argument("--out", default="", help="기본: <rec>_with_grid.mp4")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--left-w", type=int, default=1280)
    ap.add_argument("--right-w", type=int, default=960)
    ap.add_argument("--h", type=int, default=720)
    ap.add_argument("--shift", type=float, default=0.0,
                    help="싱크 미세조정(초). +면 그리드를 뒤로 민다")
    ap.add_argument("--fps", type=float, default=25.0)
    a = ap.parse_args()

    rec = ROOT / a.rec if not Path(a.rec).is_absolute() else Path(a.rec)
    side = rec.with_suffix(".json")
    if not side.is_file():
        print(f"사이드카가 없습니다: {side} — record_opsview.py 로 뽑은 녹화여야 합니다")
        return 1
    meta = json.loads(side.read_text(encoding="utf-8"))
    t0 = float(meta["clip_t0_at_rec_sec"]) + a.shift

    cdir = ROOT / a.clips if not Path(a.clips).is_absolute() else Path(a.clips)
    cams = sorted(cdir.glob("cam*.mp4"), key=cam_no)
    if not cams:
        print(f"클립이 없습니다: {cdir}")
        return 1
    # 클립 길이 — 출력 길이를 **못 박는 데** 쓴다. 안 박으면 빈 칸을 채우는
    # color 소스가 끝나지 않아 xstack 이 무한히 돌고, 파일이 끝없이 커진다
    # (실측: 1시간 45분 동안 1.1GB 까지 불어났다).
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(cams[0])],
        capture_output=True, text=True).stdout.strip())
    rows = math.ceil(len(cams) / a.cols)
    cw = a.right_w // a.cols
    ch = a.h // rows
    out = ROOT / (a.out or str(rec.with_name(rec.stem + "_with_grid.mp4")))
    print(f"[grid] 카메라 {len(cams)}대 · {a.cols}×{rows} · 칸 {cw}x{ch} · "
          f"클립 {dur:.1f}s · 클립 t=0 → 녹화 {t0:.2f}s")

    # 녹화의 t0 지점부터를 클립 t=0 과 맞춘다. 앞쪽(준비 구간)은 버린다 —
    # 그래야 좌우가 같은 시각을 가리킨다.
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{t0:.3f}", "-i", str(rec)]
    for c in cams:
        cmd += ["-i", str(c)]

    fc = [f"[0:v]scale={a.left_w}:{a.h}:flags=lanczos,"
          f"fps={a.fps},setpts=PTS-STARTPTS[L]"]
    for i, c in enumerate(cams):
        # 가로 맞춰 줄이고 칸 높이에 **레터박스** — 잘라내면 사람이 화면 밖으로 나간다
        fc.append(f"[{i+1}:v]scale={cw}:-2:flags=bilinear,"
                  f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:black,"
                  f"drawtext=text='{c.stem}':x=6:y=4:fontsize=14:fontcolor=white:"
                  f"box=1:boxcolor=black@0.5:boxborderw=3,"
                  f"fps={a.fps},setpts=PTS-STARTPTS[c{i}]")
    # 빈 칸은 검정으로 채운다(격자가 어긋나지 않게)
    blanks = a.cols * rows - len(cams)
    for b in range(blanks):
        fc.append(f"color=c=black:s={cw}x{ch}:r={a.fps}:d={dur:.3f}[b{b}]")
    cells = [f"[c{i}]" for i in range(len(cams))] + [f"[b{b}]" for b in range(blanks)]
    lay = "|".join(f"{(k % a.cols) * cw}_{(k // a.cols) * ch}" for k in range(len(cells)))
    fc.append(f"{''.join(cells)}xstack=inputs={len(cells)}:layout={lay}[R]")
    fc.append("[L][R]hstack=inputs=2[V]")

    cmd += ["-filter_complex", ";".join(fc), "-map", "[V]",
            "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-threads", "0",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    prog = out.with_suffix(".progress.txt")
    cmd += ["-progress", str(prog), "-nostats"]
    print(f"[grid] 인코딩 중… 진행률 → {prog.name}", flush=True)
    r = subprocess.run(cmd)
    if r.returncode != 0:
        return r.returncode
    prog.unlink(missing_ok=True)
    print(f"[grid] 완료 → {out} ({out.stat().st_size / 1e6:.1f} MB · "
          f"{a.left_w + a.right_w}x{a.h})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
