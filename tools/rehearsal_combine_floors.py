#!/usr/bin/env python3
"""층이 다른 두 시나리오를 **시간순으로 이어** 한 시나리오로 만든다 (다층 통합).

    python tools/rehearsal_combine_floors.py --package aihub-drill-1f3f \
           --first scenario_01 --second scenario_12 --out combo_01

왜
--
3층과 1층을 **따로 촬영**해 두 조의 타임라인이 맞춰져 있지 않다(조마다 슬레이트가
다르고 내장 시계는 최대 58초 어긋난다 — docs/촬영-원본메타/docs/STATUS.md).
그래서 "동시"로는 합칠 수 없다. 대신 실제 피난 흐름(상층 → 계단 → 1층)대로
**순차**로 잇는다.

  [ 앞 구간: 3층 카메라만 영상 · 1층은 검정 ]
  [ 뒤 구간: 3층 카메라 검정 · 1층 카메라만 영상 ]

결과는 두 시나리오의 **카메라 합집합**이고 전 채널이 같은 길이·같은 시간축이다.
이래야 건물 훈련(전 층 공유 세션)이 한 시나리오로 돌아간다.

`--gap` 으로 이음매에 검정을 넣을 수 있다(기본 0). 트래커 lost_timeout(3s)보다
길게 주면 앞 구간 트랙이 깨끗이 끊긴다 — 0 이면 ID 가 층을 넘어 이어질 수 있다.
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
                         capture_output=True, text=True, timeout=300).stdout.strip()
    return int(out or 0)


def build_cam(cam: str, segs: list[dict], root: Path, out: Path, gap_frames: int) -> None:
    """카메라 1대 = [구간1][간격][구간2] — 자기 구간이 아니면 검정."""
    inputs: list[str] = []
    fc: list[str] = []
    parts: list[str] = []
    n_in = 0
    for i, sg in enumerate(segs):
        L = sg["frames"]
        f = sg["files"].get(cam)
        if f:
            inputs += ["-i", str(root / f)]
            fc.append(f"[{n_in}:v]fps={FPS},scale={W}:{H},format=yuv420p,"
                      f"tpad=stop={L}:stop_mode=add:color=black,trim=end_frame={L},"
                      f"setpts=N/{FPS}/TB[s{i}]")
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
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs,
                    "-filter_complex", ";".join(fc), "-map", "[out]",
                    "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high",
                    "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", "30", "-keyint_min", "30",
                    "-sc_threshold", "0", "-movflags", "+faststart", "-an", str(out)],
                   check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--package", required=True)
    ap.add_argument("--first", required=True, help="앞 구간 시나리오 id (예: 3층 scenario_01)")
    ap.add_argument("--second", required=True, help="뒤 구간 시나리오 id (예: 1층 scenario_12)")
    ap.add_argument("--out", required=True, help="출력 시나리오 폴더/id (예: combo_01)")
    ap.add_argument("--name", default=None, help="표시 이름 (기본: 자동)")
    ap.add_argument("--gap", type=float, default=0.0, help="이음매 검정 간격(초). 기본 0")
    ap.add_argument("--skip-existing", action="store_true")
    a = ap.parse_args()

    pkg = vpkg.get(a.package)
    if not pkg:
        raise SystemExit(f"패키지 없음: {a.package}")
    root = Path(pkg["_root"])
    by_id = {s["id"]: s for s in pkg.get("scenarios", [])}
    for sid in (a.first, a.second):
        if sid not in by_id:
            raise SystemExit(f"시나리오 없음: {sid} — 있는 것 {sorted(by_id)}")
    cam_floor = {c["cam"]: c.get("floor") for c in pkg.get("cameras", [])}

    segs = []
    for sid in (a.first, a.second):
        s = by_id[sid]
        files = {st["cam"]: st["file"] for st in s.get("streams", []) if st.get("cam")}
        if not files:
            raise SystemExit(f"{sid}: streams 가 비어 있다")
        frames = max(nframes(root / f) for f in files.values())
        fl = sorted({cam_floor.get(c) for c in files})
        segs.append({"id": sid, "name": s.get("name", sid), "files": files,
                     "frames": frames, "floors": fl})
        print(f"   {sid}: {frames}프레임 ({frames / FPS:.1f}s) · 카메라 {len(files)}대 · 층 {fl}")

    cams = sorted(set(segs[0]["files"]) | set(segs[1]["files"]),
                  key=lambda x: int("".join(ch for ch in x if ch.isdigit())))
    gap_frames = int(round(a.gap * FPS))
    total = sum(sg["frames"] for sg in segs) + gap_frames
    print(f"[combo] {a.out}: 카메라 {len(cams)}대 · 전체 {total}프레임 ({total / FPS:.1f}s)"
          f" · 이음매 간격 {a.gap:.0f}s")

    out_dir = root / a.out
    out_dir.mkdir(exist_ok=True)
    for i, cam in enumerate(cams, 1):
        out = out_dir / f"{cam}.mp4"
        if a.skip_existing and out.is_file() and nframes(out) == total:
            print(f"   ({i}/{len(cams)}) {cam} — 있음, 생략", flush=True)
            continue
        print(f"   ({i}/{len(cams)}) {cam} …", flush=True)
        build_cam(cam, segs, root, out, gap_frames)
        got = nframes(out)
        if got != total:
            print(f"        ⚠ 프레임 {got} ≠ {total}", flush=True)

    # 매니페스트 갱신
    mf = root / vpkg.MANIFEST
    d = json.loads(mf.read_text(encoding="utf-8"))
    d["scenarios"] = [s for s in d["scenarios"] if s["id"] != a.out]
    t = 0
    segments = []
    for i, sg in enumerate(segs):
        segments.append({"scenario": sg["id"], "name": sg["name"], "floors": sg["floors"],
                         "start_sec": round(t / FPS, 3),
                         "end_sec": round((t + sg["frames"]) / FPS, 3),
                         "cams": sorted(sg["files"], key=lambda x: int(x[3:]))})
        t += sg["frames"] + (gap_frames if i == 0 else 0)
    d["scenarios"].append({
        "id": a.out,
        "name": a.name or f"통합 — {segs[0]['name']}(3층) → {segs[1]['name']}(1층)",
        "cycle_sec": 0,
        "kind": "combo",
        "note": (f"{a.first}(층 {segs[0]['floors']}) 뒤에 {a.second}(층 {segs[1]['floors']}) 를 "
                 f"이어 붙인 **합성** 시나리오. 두 조를 따로 촬영해 실제로 동시가 아니다 — "
                 f"자기 구간이 아닌 카메라는 검정이라 검출 0 이 된다. 이음매 간격 {a.gap:.0f}s."),
        "gap_sec": a.gap, "total_sec": round(total / FPS, 3),
        "segments": segments,
        "streams": [{"cam": c, "file": f"{a.out}/{c}.mp4", "duration_sec": round(total / FPS, 3)}
                    for c in cams],
    })
    mf.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[combo] rehearsal.json 에 '{a.out}' 추가 (streams {len(cams)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
