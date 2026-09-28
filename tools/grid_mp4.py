#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""세션의 카메라를 **격자로 합친 mp4 한 개**를 만든다 — 편집기(:8930) 배경용.

카메라마다 따로 틀면 브라우저가 수백 MB 를 물고 멈춘다(03 CBS 7대 = 622MB).
프레임을 한 장씩 받아 오는 방법도 써 봤지만 스크럽할 때마다 7번 요청한다.
**한 개로 합쳐 두면** 그냥 비디오 하나를 재생하는 일이 된다.

    python tools/grid_mp4.py --session <sess-id> [--floor <층>]

결과 `results/demo/_annotate/grid_<세션>.mp4` (15fps · 960px · 몇 MB).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2                                          # noqa: E402
import numpy as np                                  # noqa: E402

from system.metrics import recorder                 # noqa: E402
from tools.session_viz import (                     # noqa: E402
    drill_label, find_session, resolve_clips,
)

OUT = ROOT / "results/demo/_annotate"


def path(sid: str) -> Path:
    return OUT / f"grid_{sid}.mp4"


def build(sid: str, floor: str | None = None, width: int = 960,
          fps: float = 15.0, verbose: bool = True) -> Path:
    db, fl = find_session(sid, floor)
    meta = recorder.load_meta(db)
    t0 = float(meta["alarm_ts"])
    base = float(meta.get("source_t0") or t0)
    cams = sorted({c["cam_id"] for c in (meta.get("cameras") or [])})
    clips, desc = resolve_clips(cams, drill_label(sid) or sid, None, None)
    caps, src = {}, {}
    for cid, fp in clips.items():
        cap = cv2.VideoCapture(str(fp))
        if cap.isOpened():
            caps[cid] = cap
            src[cid] = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if not caps:
        raise SystemExit("영상을 하나도 열지 못했습니다")
    ids = sorted(caps)
    n = len(ids)
    cols = int(np.ceil(np.sqrt(n)))
    rows = int(np.ceil(n / cols))
    cw = width // cols
    ch = int(cw * 9 / 16)
    W, H = cw * cols, ch * rows
    dur = max(cap.get(cv2.CAP_PROP_FRAME_COUNT) / (src[c] or 30)
              for c, cap in caps.items())
    dur = min(dur, 180.0)
    if verbose:
        print(f"[grid] {sid} · {fl} · {desc} · {n}대 {cols}x{rows} · "
              f"{W}x{H} · {dur:.0f}s @{fps:g}fps")

    OUT.mkdir(parents=True, exist_ok=True)
    tmp = path(sid).with_suffix(".tmp.mp4")
    vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    cur = {c: None for c in ids}
    pos = {c: -1 for c in ids}
    try:
        for k in range(int(dur * fps)):
            t = k / fps
            frame = np.zeros((H, W, 3), np.uint8)
            for i, cid in enumerate(ids):
                want = int(round((t + (t0 - base)) * src[cid]))
                if want != pos[cid]:
                    # 매 프레임 seek 하면 느리다(출력 15fps · 원본 30fps 라 늘
                    # 건너뛴다). 가까우면 **grab 으로 흘려보낸다** — 디코드를
                    # 건너뛰어 훨씬 싸다.
                    gap = want - pos[cid]
                    if 0 < gap <= 8:
                        for _ in range(gap - 1):
                            caps[cid].grab()
                    elif gap != 1:
                        caps[cid].set(cv2.CAP_PROP_POS_FRAMES, max(0, want))
                    ok, im = caps[cid].read()
                    if ok:
                        cur[cid] = im
                    pos[cid] = want
                gy, gx = divmod(i, cols)
                cell = np.zeros((ch, cw, 3), np.uint8)
                if cur[cid] is not None:
                    im = cur[cid]
                    s = max(cw / im.shape[1], ch / im.shape[0])
                    r = cv2.resize(im, (max(1, int(im.shape[1] * s)),
                                        max(1, int(im.shape[0] * s))))
                    y = max(0, (r.shape[0] - ch) // 2)
                    x = max(0, (r.shape[1] - cw) // 2)
                    cell = r[y:y + ch, x:x + cw]
                cv2.rectangle(cell, (0, 0), (cw - 1, ch - 1), (40, 40, 40), 1)
                cv2.putText(cell, cid.split("_")[-1], (6, 16),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3)
                cv2.putText(cell, cid.split("_")[-1], (6, 16),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
                frame[gy * ch:(gy + 1) * ch, gx * cw:(gx + 1) * cw] = cell
            vw.write(frame)
            if verbose and k % int(fps * 15) == 0:
                print(f"    {t:5.0f}s / {dur:.0f}s", flush=True)
    finally:
        vw.release()
        for c in caps.values():
            c.release()
    out = path(sid)
    # 브라우저가 바로 재생하도록 H.264 로 바꾼다(mp4v 는 크롬에서 안 난다)
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp),
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                       check=True)
        tmp.unlink()
    except (FileNotFoundError, subprocess.CalledProcessError):
        tmp.replace(out)
        if verbose:
            print("    ffmpeg 없음 — mp4v 그대로(브라우저에서 안 날 수 있다)")
    if verbose:
        print(f"[grid] 완료 → {out.relative_to(ROOT)} "
              f"({out.stat().st_size / 1e6:.1f} MB)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="카메라 격자 합본 mp4")
    ap.add_argument("--session", required=True)
    ap.add_argument("--floor")
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--fps", type=float, default=15.0)
    a = ap.parse_args()
    build(a.session, a.floor, a.width, a.fps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
