#!/usr/bin/env python3
"""리허설 패키지 준비 CLI (ADR 09 P1).

폴더 배치 전(또는 후) 리허설 패키지를 검사하고 매니페스트를 스캐폴딩한다.

    python tools/rehearsal_prep.py media/vsource/cj/rehearsal            # 검사 + prep 기록
    python tools/rehearsal_prep.py <folder> --scaffold                   # rehearsal.json 없으면 생성
    python tools/rehearsal_prep.py <folder> --encode                     # 부적합 영상 재인코딩(원본 .orig 백업)

검사 기준 (vsource 는 -c:v copy 로 쏘므로 실제 송출 가능성 기준):
  [실패] h264 아님 · 프로파일 baseline 아님(앞머리 concat 불일치) · B-frame 있음 ·
         yuv420p 아님 · 길이/fps 판독 불가
  [경고] 시나리오 내 채널 간 길이 편차 > 0.5s (사이클로 흡수되지만 시작점 정렬 의심)
         grid_preview 밖에 grid_* 파일 존재 (송출 금지 규칙 위반)

--encode 는 tools/rtsp/encode_video.sh 와 같은 조건(H.264 baseline·yuv420p·
B-frame 없음·faststart)으로 부적합 파일만 교체한다.

결과는 rehearsal.json 의 "prep" 에 기록된다 (UI 는 이 기록 + 재검사로 송출 가능 여부 판단).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MANIFEST = "rehearsal.json"
DUR_SPREAD_WARN = 0.5  # 시나리오 내 채널 간 길이 편차 경고 문턱(초)


def probe(f: Path) -> dict:
    """ffprobe 로 검사에 필요한 필드만. 실패 시 {'error': ...}."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries",
             "stream=codec_name,profile,pix_fmt,has_b_frames,width,height,r_frame_rate",
             "-show_entries", "format=duration",
             "-of", "json", str(f)],
            capture_output=True, text=True, timeout=30)
        d = json.loads(out.stdout or "{}")
        st = (d.get("streams") or [{}])[0]
        num, _, den = (st.get("r_frame_rate") or "0/1").partition("/")
        fps = round(float(num) / float(den or 1), 3) if float(den or 1) else None
        return {
            "codec": st.get("codec_name"), "profile": st.get("profile"),
            "pix_fmt": st.get("pix_fmt"), "has_b_frames": st.get("has_b_frames"),
            "wh": f"{st.get('width')}x{st.get('height')}", "fps": fps,
            "duration": round(float(d.get("format", {}).get("duration", 0)), 3) or None,
        }
    except Exception as e:  # noqa: BLE001 — 진단 CLI, 사유만 남기면 된다
        return {"error": f"{type(e).__name__}: {e}"}


def check_file(p: dict) -> list[str]:
    """실패 사유 목록 (빈 리스트 = 적합)."""
    if "error" in p:
        return [f"ffprobe 실패: {p['error']}"]
    bad = []
    if p["codec"] != "h264":
        bad.append(f"코덱 {p['codec']}(≠h264)")
    if (p["has_b_frames"] or 0) > 0:
        bad.append(f"B-frame {p['has_b_frames']} — -c:v copy 송출 시 카메라 미수신 위험")
    if p["profile"] not in ("Baseline", "Constrained Baseline"):
        # 훈련 시작 시 앞머리(정지화면, baseline)를 concat -c copy 로 이어붙인다.
        # 프로파일이 다르면 SPS/PPS 가 중간에 바뀌어 DS 디코더가 멈춘다 —
        # 실측: High 프로파일 영상은 본영상 시작 순간 fps_in 이 0 으로 죽고 트랙 0.
        bad.append(f"프로파일 {p['profile']} — 앞머리(baseline) concat 과 불일치, 재인코딩 필요")
    if p["pix_fmt"] != "yuv420p":
        bad.append(f"pix_fmt {p['pix_fmt']}(≠yuv420p)")
    if not p["duration"]:
        bad.append("길이 판독 불가")
    if not p["fps"]:
        bad.append("fps 판독 불가")
    return bad


def encode(f: Path) -> bool:
    """부적합 영상을 제자리 교체(원본은 .orig.mp4). encode_video.sh 와 같은 조건."""
    orig = f.with_suffix(".orig.mp4")
    if not orig.exists():
        f.rename(orig)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(orig),
           "-c:v", "libx264", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
           "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
           "-x264opts", "bframes=0:repeat-headers=1",
           "-movflags", "+faststart", "-an", str(f)]
    ok = subprocess.run(cmd).returncode == 0
    if not ok and orig.exists():          # 실패 시 원본 복구
        f.unlink(missing_ok=True)
        orig.rename(f)
    return ok


def scaffold(root: Path) -> dict:
    """scenario_*/cam*.mp4 스캔으로 매니페스트 초안 생성 (기존 파일 있으면 로드만)."""
    mf = root / MANIFEST
    if mf.exists():
        return json.loads(mf.read_text(encoding="utf-8"))
    site, set_ = root.parent.name, root.name
    cams: set[int] = set()
    scenarios = []
    for d in sorted(root.glob("scenario_*")):
        streams = []
        for f in sorted(d.glob("cam*.mp4"),
                        key=lambda x: int(re.sub(r"\D", "", x.stem) or 0)):
            cams.add(int(re.sub(r"\D", "", f.stem)))
            streams.append({"cam": f.stem, "file": f"{d.name}/{f.name}",
                            "duration_sec": probe(f).get("duration")})
        scenarios.append({"id": d.name, "name": f"시나리오 {d.name.split('_')[-1]}",
                          "cycle_sec": 0, "streams": streams})
    m = {"schema": 1, "id": f"{site}-{set_}", "name": f"{site} {set_}",
         "rtsp_prefix": site, "floors": [],
         "cameras": [{"cam": f"cam{n}", "path": f"{site}_cam{n}", "name": f"{site} cam{n}",
                      "floor": None, "analyze_fps": 5.0, "mapping": None}
                     for n in sorted(cams)],
         "scenarios": scenarios, "prep": None}
    mf.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[scaffold] {mf} 생성 (시나리오 {len(scenarios)} · 카메라 {len(cams)})")
    return m


def copy_site_floorplan(root: Path, m: dict, site_floor: str,
                        pkg_floor: str | None) -> str | None:
    """사이트 층 도면(png + m_per_px)을 패키지로 복사해 층에 연결. 오류 문자열 반환.

    구조가 같은 층의 도면을 재사용하는 흔한 경우를 절차화한다 (예: CJ 10F = 16F).
    매핑은 복사하지 않는다 — 카메라 시점이 달라 어차피 다시 찍어야 한다.
    """
    import shutil
    site_json = Path("data/sites/default/site.json")
    if not site_json.is_file():
        return f"사이트 없음: {site_json}"
    site = json.loads(site_json.read_text(encoding="utf-8"))
    fl = next((f for f in site.get("floors", []) if f.get("id") == site_floor), None)
    if fl is None or not (fl.get("map") or {}).get("image"):
        return f"사이트 층/도면 없음: {site_floor}"
    src = site_json.parent / fl["map"]["image"]
    if not src.is_file():
        return f"도면 파일 없음: {src}"
    floors = m.get("floors") or []
    tgt = next((f for f in floors if f.get("id") == pkg_floor), None) \
        if pkg_floor else (floors[0] if floors else None)
    if tgt is None:
        return f"패키지 층 없음: {pkg_floor or '(floors 비어 있음)'}"
    (root / "floorplan").mkdir(exist_ok=True)
    dst = root / "floorplan" / f"{tgt['id']}.png"
    shutil.copyfile(src, dst)
    tgt["image"] = f"floorplan/{dst.name}"
    tgt["m_per_px"] = fl["map"].get("m_per_px")
    tgt["source"] = (f"{fl['map'].get('source') or '?'} — 사이트 층 "
                     f"{site_floor}({fl.get('name') or ''}) 도면 재사용")
    print(f"[floorplan] {site_floor} → {tgt['image']} "
          f"(m_per_px={tgt['m_per_px']})")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", help="리허설 패키지 폴더 (media/vsource/<site>/<set>)")
    ap.add_argument("--scaffold", action="store_true", help="rehearsal.json 없으면 생성")
    ap.add_argument("--encode", action="store_true", help="부적합 영상 재인코딩(원본 .orig 백업)")
    ap.add_argument("--floorplan-from-site", metavar="FLOOR_ID",
                    help="사이트 층 도면을 패키지 floorplan/ 으로 복사해 층에 연결 "
                         "(구조 동일한 층 재사용 — 예: floor2). 대상 패키지 층은 "
                         "floors[0] (여럿이면 --floor 로 지정)")
    ap.add_argument("--floor", help="--floorplan-from-site 대상 패키지 층 id (기본 floors[0])")
    a = ap.parse_args()

    root = Path(a.folder)
    if not root.is_dir():
        print(f"폴더 없음: {root}", file=sys.stderr)
        return 2
    mf = root / MANIFEST
    if not mf.exists() and not a.scaffold:
        print(f"{MANIFEST} 없음 — --scaffold 로 생성부터", file=sys.stderr)
        return 2
    m = scaffold(root)

    if a.floorplan_from_site:
        err = copy_site_floorplan(root, m, a.floorplan_from_site, a.floor)
        if err:
            print(err, file=sys.stderr)
            return 2

    fails, warns, results = [], [], {}
    for sc in m.get("scenarios", []):
        durs = []
        for st in sc["streams"]:
            f = root / st["file"]
            if not f.is_file():
                fails.append(f"{sc['id']}/{st['cam']}: 파일 없음 ({st['file']})")
                continue
            p = probe(f)
            bad = check_file(p)
            st["duration_sec"] = p.get("duration")
            if p.get("duration"):
                durs.append(p["duration"])
            results[st["file"]] = {k: p.get(k) for k in
                                   ("codec", "profile", "pix_fmt", "has_b_frames",
                                    "wh", "fps", "duration")}
            if bad and a.encode:
                print(f"[encode] {st['file']}: {', '.join(bad)} → 재인코딩")
                if encode(f):
                    p = probe(f)
                    bad = check_file(p)
                    st["duration_sec"] = p.get("duration")
                else:
                    bad.append("재인코딩 실패")
            if bad:
                fails.append(f"{sc['id']}/{st['cam']}: {', '.join(bad)}")
        if durs and max(durs) - min(durs) > DUR_SPREAD_WARN:
            warns.append(f"{sc['id']}: 채널 간 길이 편차 {max(durs)-min(durs):.2f}s — 시작점 정렬 확인 필요")
    stray = [str(f.relative_to(root)) for f in root.glob("scenario_*/grid_*")]
    if stray:
        warns.append(f"grid 파일이 시나리오 폴더에 있음(송출 금지 — grid_preview/ 로): {stray}")

    m["prep"] = {"checked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                 "tool": "tools/rehearsal_prep.py", "ok": not fails,
                 "fails": fails, "warns": warns, "files": results}
    mf.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    n = sum(len(s["streams"]) for s in m.get("scenarios", []))
    print(f"\n검사 {n}파일 · 시나리오 {len(m.get('scenarios', []))} — "
          f"{'✅ 전부 적합' if not fails else f'❌ 부적합 {len(fails)}'}"
          f"{f' · ⚠️ 경고 {len(warns)}' if warns else ''}")
    for x in fails:
        print("  ❌", x)
    for x in warns:
        print("  ⚠️ ", x)
    print(f"→ {mf} 의 prep 에 기록됨")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
