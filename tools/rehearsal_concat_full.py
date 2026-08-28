#!/usr/bin/env python3
"""리허설 패키지의 시나리오들을 시간순으로 이어 붙인 '전체' 시나리오를 만든다.

    python tools/rehearsal_concat_full.py --package cj-rehearsal            # scenario_01..14 → scenario_15_full
    python tools/rehearsal_concat_full.py --package cj-rehearsal --gap 4 --out scenario_15_full

카메라 위치(cam1~14)는 고정이고 시나리오마다 촬영된 카메라가 다르다. 시나리오 s 에 카메라 c
영상이 없으면 그 구간 길이만큼 **검정 프레임**을 넣는다(검출 0 = 그 시간엔 안 봄).
그래서 결과 14개 영상은 **프레임 단위로 같은 길이·같은 시간축**이다.

- 구간 길이 = 그 시나리오 카메라들의 최장 프레임 수 (1~2프레임 차는 뒤를 검정으로 채움)
- 시나리오 사이 `--gap` 초 검정 (기본 4s): 하드컷이면 사람들이 순간이동해 트래커 ID 가 엉킨다.
  lost_timeout(3s) 보다 길게 비우면 깨끗하게 끊긴다. 0 이면 붙여 넣는다.
- 인코딩은 준비 CLI 와 같은 조건(H.264 baseline·yuv420p·30fps·g30·B-frame 0·faststart).
- 끝나면 rehearsal.json 에 시나리오 항목을 추가/갱신한다 (streams 전 카메라 + segments 표:
  각 시나리오가 전체 영상의 몇 초부터 몇 초까지인지).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from system.vsource import package as vpkg      # noqa: E402

FPS = 30
W, H = 1920, 1080


def nframes(f: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                          "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(f)],
                         capture_output=True, text=True, timeout=120).stdout.strip()
    return int(out or 0)


def build_cam(cam: str, segs: list[dict], root: Path, out: Path, gap_frames: int) -> None:
    """카메라 1대의 전체 영상 — 구간마다 (영상 or 검정) + 간격 검정 → concat → 인코딩."""
    inputs: list[str] = []
    parts: list[str] = []
    fc: list[str] = []
    n_in = 0
    for i, sg in enumerate(segs):
        L = sg["frames"]
        f = sg["files"].get(cam)
        if f:
            inputs += ["-i", str(root / f)]
            # 프레임 수를 L 로 맞춘다: 짧으면 뒤를 검정으로, 길면 자른다
            fc.append(f"[{n_in}:v]fps={FPS},scale={W}:{H},format=yuv420p,"
                      f"tpad=stop={L}:stop_mode=add:color=black,trim=end_frame={L},setpts=N/{FPS}/TB[s{i}]")
            n_in += 1
        else:
            fc.append(f"color=c=black:s={W}x{H}:r={FPS}:d={L / FPS:.4f},format=yuv420p,"
                      f"trim=end_frame={L},setpts=N/{FPS}/TB[s{i}]")
        parts.append(f"[s{i}]")
        if gap_frames and i < len(segs) - 1:
            fc.append(f"color=c=black:s={W}x{H}:r={FPS}:d={gap_frames / FPS:.4f},format=yuv420p,"
                      f"trim=end_frame={gap_frames},setpts=N/{FPS}/TB[g{i}]")
            parts.append(f"[g{i}]")
    fc.append("".join(parts) + f"concat=n={len(parts)}:v=1:a=0[out]")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs,
           "-filter_complex", ";".join(fc), "-map", "[out]",
           "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
           "-r", str(FPS), "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
           "-x264opts", "bframes=0:repeat-headers=1", "-movflags", "+faststart", "-an", str(out)]
    subprocess.run(cmd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--package", required=True)
    ap.add_argument("--out", default="scenario_15_full", help="출력 시나리오 폴더/id")
    ap.add_argument("--name", default="전체 (01~14 연속)")
    ap.add_argument("--gap", type=float, default=4.0, help="시나리오 사이 검정 간격(초). 0=없음")
    ap.add_argument("--only", nargs="*", help="특정 카메라만 (테스트용, 예: cam8 cam9)")
    ap.add_argument("--skip-existing", action="store_true",
                    help="프레임 수가 맞는 결과가 이미 있으면 인코딩 생략 (병렬 실행 뒤 매니페스트만 갱신할 때)")
    a = ap.parse_args()

    pkg = vpkg.get(a.package)
    if not pkg:
        raise SystemExit(f"패키지 없음: {a.package}")
    root = Path(pkg["_root"])
    scen = [s for s in pkg["scenarios"] if s["id"] != a.out and s.get("streams")]
    scen.sort(key=lambda s: s["id"])
    cams = sorted({c["cam"] for c in pkg["cameras"]}, key=lambda x: int("".join(ch for ch in x if ch.isdigit())))
    if a.only:
        cams = [c for c in cams if c in set(a.only)]
    gap_frames = int(round(a.gap * FPS))

    # 구간 길이(프레임) — 시나리오별 최장
    segs = []
    print(f"[full] {a.package}: 시나리오 {len(scen)}개 · 카메라 {len(cams)}대 · 간격 {a.gap:.0f}s")
    for s in scen:
        files = {st["cam"]: st["file"] for st in s["streams"] if st.get("cam")}
        frames = max(nframes(root / f) for f in files.values())
        segs.append({"id": s["id"], "name": s.get("name", s["id"]), "files": files, "frames": frames})
        print(f"   {s['id']}: {frames}프레임 ({frames / FPS:.2f}s) · 카메라 {sorted(files, key=lambda x: int(x[3:]))}")
    total = sum(sg["frames"] for sg in segs) + gap_frames * (len(segs) - 1)
    print(f"[full] 전체 길이 {total}프레임 = {total / FPS:.1f}s")

    out_dir = root / a.out
    out_dir.mkdir(exist_ok=True)
    for i, cam in enumerate(cams, 1):
        out = out_dir / f"{cam}.mp4"
        if a.skip_existing and out.is_file() and nframes(out) == total:
            print(f"[full] ({i}/{len(cams)}) {cam} — 있음({total}프레임), 생략", flush=True)
            continue
        print(f"[full] ({i}/{len(cams)}) {cam} → {out.relative_to(root)} …", flush=True)
        build_cam(cam, segs, root, out, gap_frames)
        got = nframes(out)
        flag = "" if got == total else f"  ⚠ 프레임 {got} ≠ {total}"
        print(f"        {got}프레임{flag}", flush=True)

    # 매니페스트 갱신 (--only 로 일부만 만들었으면 건너뜀)
    if not a.only:
        mf = root / vpkg.MANIFEST
        d = json.loads(mf.read_text(encoding="utf-8"))
        d["scenarios"] = [s for s in d["scenarios"] if s["id"] != a.out]
        t = 0
        segments = []
        for i, sg in enumerate(segs):
            segments.append({"scenario": sg["id"], "name": sg["name"],
                             "start_sec": round(t / FPS, 3), "end_sec": round((t + sg["frames"]) / FPS, 3),
                             "cams": sorted(sg["files"], key=lambda x: int(x[3:]))})
            t += sg["frames"] + (gap_frames if i < len(segs) - 1 else 0)
        d["scenarios"].append({
            "id": a.out, "name": a.name, "cycle_sec": 0,
            "note": f"시나리오 01~{len(segs):02d} 를 시간순 연결. 카메라가 없는 구간은 검정. 시나리오 사이 검정 {a.gap:.0f}s.",
            "gap_sec": a.gap, "total_sec": round(total / FPS, 3),
            "segments": segments,
            "streams": [{"cam": c, "file": f"{a.out}/{c}.mp4", "duration_sec": round(total / FPS, 3)} for c in cams],
        })
        mf.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[full] rehearsal.json 에 '{a.out}' 추가 (streams {len(cams)} · segments {len(segments)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
