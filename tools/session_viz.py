#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""세션 녹화본 시각화 — 좌: 도면 / 우: 카메라 그리드(원본 fps) / 하: 4대 지표.

    python tools/session_viz.py --session sess-1789607919966
    python tools/session_viz.py --session sess-... --floor floor4 --width 1920 --out results/session_viz

왜 이 방식인가 (추론을 다시 돌리지 않는다)
------------------------------------------
tools/rehearsal_viz.py 는 매번 GPU 추론을 다시 돌려서 웹 UI·보고서 수치와 미세하게
어긋날 수 있다. 여기서는 **세션 녹화본(.db)** 의 트랙과 리플레이 타임라인을 그대로
쓴다 — ④ 리플레이 화면과 **같은 수치**가 나온다. 리허설 영상은 그림만 그린다.

  좌  도면 — 구역·병목·출구·경로 + 객체 점(카메라별 색) + 궤적
  우  카메라 그리드 — **원본 fps** 원본 프레임 (박스는 그리지 않는다, 아래 참고)
  하  4대 지표(SEI·EPFI·CBS·IDR) + 인원·출구 통과 — 1초 타임라인을 홀드

맵 좌표는 **리플레이가 내주는 프레임의 객체 좌표를 그대로** 쓴다. .db 의 트랙을
직접 투영하면 안 된다 — 엔진은 valid_roi·min_conf·min_box_h 로 관측을 걸러내는데,
그 필터를 거치지 않은 점까지 찍으면 화면에 없어야 할 점이 흩어진다
(실측: cam1 4개 중 3개가 엔진에서 버려지는 관측이었다).

그리드에 박스를 그리지 않는 이유: 관측은 5fps 인데 영상은 30fps 라 박스가 6프레임마다
튀어 심하게 깜빡인다. 위치 확인은 좌측 도면이 맡는다.

시간축: 관측은 분석 fps(보통 5)라 출력 프레임 사이를 **홀드**한다. 영상은 원본 fps
그대로라 부드럽고, 지표는 라이브와 같은 1초 격자다.

도면 편집본(<session>.ov.json)이 있으면 자동으로 반영한다 — 편집한 도면의 결과를
그대로 영상으로 뽑을 수 있다. --no-overrides 로 끌 수 있다.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from system.metrics import recorder                      # noqa: E402
from system.metrics.replay import run_replay             # noqa: E402

SITE_DIR = ROOT / "data" / "sites" / "default"
VSRC = ROOT / "media" / "vsource"
CAM_RE = re.compile(r"^rh_(?:(?P<ns>[^_]+)_)?(?P<cam>cam\d+)$", re.I)


# ------------------------------------------------------------------ 유틸
def cam_name(cam_id: str) -> str:
    """세션 카메라 id → 패키지 카메라 이름.
    네임스페이스 도입(2026-09-17) 전후 둘 다 받는다: rh_cam9 · rh_aihub2_cam9."""
    m = CAM_RE.match(cam_id)
    return m.group("cam").lower() if m else cam_id


def cam_ns(cam_id: str) -> str | None:
    m = CAM_RE.match(cam_id)
    return (m.group("ns") or "").lower() or None if m else None


def _font(px: int, bold: bool = False):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/truetype/nanum/NanumGothic%s.ttf" % ("Bold" if bold else ""),
              "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"):
        if Path(p).is_file():
            return ImageFont.truetype(p, px)
    return ImageFont.load_default()


def put(img, text, org, px=16, col=(235, 235, 235), bold=False):
    """한글 텍스트 — cv2.putText 는 한글을 못 그린다."""
    from PIL import Image, ImageDraw
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    ImageDraw.Draw(pil).text(org, text, font=_font(px, bold),
                             fill=(col[2], col[1], col[0]))
    img[:] = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def fit(img, w, h):
    s = min(w / img.shape[1], h / img.shape[0])
    r = cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))))
    out = np.zeros((h, w, 3), np.uint8)
    oy, ox = (h - r.shape[0]) // 2, (w - r.shape[1]) // 2
    out[oy:oy + r.shape[0], ox:ox + r.shape[1]] = r
    return out, s, ox, oy


def arrows_along(img, pts, color, every_px: float = 110.0, head: float = 12.0):
    """폴리라인 진행방향 화살촉 — 일정 **화면 거리**마다 찍는다.

    cv2.arrowedLine 의 tipLength 는 선분 길이 대비 **비율**이라, 고정값을 주면
    긴 구간에서 화살촉이 화면을 덮고 짧은 구간에선 사라진다. 여기서는 목표
    픽셀(head)을 그 구간 길이로 나눠 비율로 환산하고, 구간이 길면 중간에도 찍는다.
    """
    for i in range(1, len(pts)):
        (x0, y0), (x1, y1) = pts[i - 1], pts[i]
        seg = float(np.hypot(x1 - x0, y1 - y0))
        if seg < 6:
            continue
        n = max(1, int(seg // every_px))          # 이 구간에 찍을 개수
        for k in range(1, n + 1):
            t = k / n
            hx, hy = int(x0 + (x1 - x0) * t), int(y0 + (y1 - y0) * t)
            back = min(1.0, head / seg)
            tx, ty = int(hx - (x1 - x0) * back), int(hy - (y1 - y0) * back)
            cv2.arrowedLine(img, (tx, ty), (hx, hy), color, 2,
                            tipLength=min(0.9, head / max(1.0, head)))


def cam_color(i: int):
    import colorsys
    r, g, b = colorsys.hsv_to_rgb((i * 0.137) % 1.0, 0.62, 1.0)
    return (int(b * 255), int(g * 255), int(r * 255))


# ------------------------------------------------------------------ 자료 수집
def find_session(session_id: str, floor: str | None):
    """세션 db 경로와 층 — floor 미지정이면 전 층에서 찾는다."""
    if floor:
        p = SITE_DIR / "sessions" / floor / f"{session_id}.db"
        if p.is_file():
            return p, floor
        raise SystemExit(f"녹화 없음: {p}")
    for d in sorted((SITE_DIR / "sessions").glob("floor*")):
        p = d / f"{session_id}.db"
        if p.is_file():
            return p, d.name
    raise SystemExit(f"녹화 없음: {session_id}")


def drill_label(session_id: str) -> str:
    p = SITE_DIR / "sessions" / "_drills" / f"{session_id}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("label") or ""
    except (OSError, json.JSONDecodeError):
        return ""


def resolve_clips(cam_ids: list[str], label: str,
                  pkg_hint: str | None, scen_hint: str | None):
    """세션 카메라 → 원본 영상 파일. (cam_id → path, 설명문).

    세션 메타에 패키지·시나리오가 안 남아 있는 녹화가 있어(초기 드릴), 라벨의
    scenario_NN 과 **카메라 이름 일치도**로 패키지를 찾는다. 힌트를 주면 그것을 쓴다.
    """
    want = {cam_name(c) for c in cam_ids}
    scen_id = scen_hint
    if not scen_id:
        m = re.search(r"(scenario_\d+|combo_\d+)", label or "")
        if m:
            scen_id = m.group(1)
        else:
            # 라벨이 "경로v2 01 …" 처럼 번호만 있는 경우 — 앞머리 뒤 두 자리를 시나리오로 본다
            m2 = re.match(r"^\S+\s+(\d{1,2})\b", label or "")
            if m2:
                scen_id = f"scenario_{int(m2.group(1)):02d}"
    if not scen_id:
        raise SystemExit(f"시나리오를 알 수 없습니다 — --scenario 로 지정하세요 (라벨: {label!r})")

    best = None
    for man in sorted(VSRC.glob("*/*/rehearsal.json")):
        pkg = json.loads(man.read_text(encoding="utf-8"))
        if pkg_hint and pkg.get("id") != pkg_hint:
            continue
        for s in pkg.get("scenarios", []):
            if s.get("id") != scen_id:
                continue
            have = {st["cam"].lower() for st in s.get("streams", [])}
            score = len(want & have)
            if best is None or score > best[0]:
                best = (score, man.parent, pkg, s)
    if not best or best[0] == 0:
        raise SystemExit(f"{scen_id} 에 맞는 리허설 패키지를 찾지 못했습니다 — --package 로 지정하세요")

    score, root, pkg, s = best
    by_cam = {st["cam"].lower(): root / st["file"] for st in s.get("streams", [])}
    out, miss = {}, []
    for cid in cam_ids:
        f = by_cam.get(cam_name(cid))
        if f and f.is_file():
            out[cid] = f
        else:
            miss.append(cid)
    desc = (f"{pkg['id']}/{scen_id} · 영상 {len(out)}/{len(cam_ids)}대"
            + (f" (없음: {', '.join(cam_name(c) for c in miss)})" if miss else ""))
    return out, desc


def latest_at(seq, t, key=lambda x: x[0]):
    """t 이하에서 가장 최근 원소 — 관측·지표 홀드용."""
    lo, hi, ans = 0, len(seq) - 1, None
    while lo <= hi:
        m = (lo + hi) // 2
        if key(seq[m]) <= t:
            ans = seq[m]; lo = m + 1
        else:
            hi = m - 1
    return ans


# ------------------------------------------------------------------ 본체
def main() -> int:
    ap = argparse.ArgumentParser(description="세션 녹화본 → 도면·그리드·4대지표 영상")
    ap.add_argument("--session", required=True, help="세션 id (sess-...)")
    ap.add_argument("--floor", help="층 id — 미지정이면 자동 탐색")
    ap.add_argument("--package", help="리허설 패키지 id (자동 탐색 실패 시)")
    ap.add_argument("--scenario", help="시나리오 id (자동 탐색 실패 시)")
    ap.add_argument("--out", default="results/session_viz", help="출력 폴더")
    ap.add_argument("--fps", type=float, default=0,
                    help="출력 fps (0=원본 영상 fps 그대로)")
    ap.add_argument("--width", type=int, default=1920, help="출력 가로 px")
    ap.add_argument("--sec", type=float, default=0, help="앞에서 N초만 (0=전체)")
    ap.add_argument("--no-overrides", action="store_true",
                    help="도면 편집본(.ov.json)을 무시하고 녹화 당시 도면으로")
    a = ap.parse_args()

    db, floor = find_session(a.session, a.floor)
    meta = recorder.load_meta(db)
    label = drill_label(a.session)
    print(f"[viz] {a.session} · {floor} · {label or '(라벨 없음)'}")

    ov = {}
    ovp = db.with_suffix(".ov.json")
    if ovp.is_file() and not a.no_overrides:
        ov = json.loads(ovp.read_text(encoding="utf-8"))
        print(f"[viz] 도면 편집본 적용 — {ovp.name}")

    print("[viz] 리플레이로 지표·좌표 재산출…")
    # fps=5 = 분석 격자. 프레임의 objects 가 ④ 리플레이 화면이 그리는 바로 그 좌표다
    # (valid_roi·min_conf 필터가 이미 적용돼 있다).
    result, timeline, frames, _m = run_replay(db, ov, fps=5.0)
    res = result.model_dump()
    tl = [t.model_dump() for t in timeline]
    site = (ov.get("geometry") and {**meta["site_view"], **ov["geometry"]}) or meta["site_view"]

    cam_ids = sorted({c["cam_id"] for c in meta.get("cameras", [])}
                     or {o["cam_id"] for f in frames for o in f["objects"]})
    clips, desc = resolve_clips(cam_ids, label, a.package, a.scenario)
    print(f"[viz] {desc}")
    if not clips:
        return 1

    caps, src_fps = {}, {}
    for cid, f in clips.items():
        cap = cv2.VideoCapture(str(f))
        if not cap.isOpened():
            print(f"  ! 열기 실패 {f}"); continue
        caps[cid] = cap
        src_fps[cid] = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if not caps:
        print("영상을 하나도 열지 못했습니다"); return 1
    out_fps = a.fps or max(src_fps.values())

    t0 = float(meta["alarm_ts"])                      # 영상 t=0 ↔ 경보 시각
    t_end = max(frames[-1]["ts"], tl[-1]["ts"]) if frames else tl[-1]["ts"]
    dur = (t_end - t0) if not a.sec else min(a.sec, t_end - t0)
    n_out = int(dur * out_fps)
    print(f"[viz] 출력 {out_fps:.0f}fps · {dur:.1f}s · {n_out}프레임 "
          f"(관측 {len(frames)}프레임 · 지표 {len(tl)}점)")

    # ---------------------------------------------------------- 레이아웃
    OW = a.width
    LEFT_W = int(OW * 0.42)
    RIGHT_W = OW - LEFT_W
    PANE_H = int(OW * 0.46)
    BAR_H = 120
    OH = PANE_H + BAR_H

    mp = SITE_DIR / f"map_{floor}.png"
    base_map = cv2.imread(str(mp)) if mp.is_file() else None
    if base_map is None:
        mw = int((site.get("map") or {}).get("w") or 1600)
        mh = int((site.get("map") or {}).get("h") or 1200)
        base_map = np.full((mh, mw, 3), 245, np.uint8)
    ms = min(LEFT_W / base_map.shape[1], PANE_H / base_map.shape[0])
    mimg = cv2.resize(base_map, (int(base_map.shape[1] * ms), int(base_map.shape[0] * ms)))
    mox = (LEFT_W - mimg.shape[1]) // 2
    moy = (PANE_H - mimg.shape[0]) // 2
    MP = lambda x, y: (int(x * ms) + mox, int(y * ms) + moy)   # noqa: E731

    fname = next((f.get("name") or floor for f in (site.get("floors") or [])
                  if f.get("id") == floor), floor)
    cols = {cid: cam_color(i) for i, cid in enumerate(cam_ids)}
    ncam = len(caps)
    gcols = 3 if ncam > 4 else 2
    grows = (ncam + gcols - 1) // gcols
    cw, ch = RIGHT_W // gcols, PANE_H // grows

    # 도면 배경(정적 요소)은 한 번만 그린다
    left0 = np.zeros((PANE_H, LEFT_W, 3), np.uint8)
    left0[moy:moy + mimg.shape[0], mox:mox + mimg.shape[1]] = mimg
    for z in (site.get("zones") or []):
        cv2.polylines(left0, [np.array([MP(*p) for p in z["polygon"]], np.int32)],
                      True, (90, 200, 255), 2)
    for bn in (site.get("bottlenecks") or []):
        cv2.polylines(left0, [np.array([MP(*p) for p in bn["polygon"]], np.int32)],
                      True, (80, 140, 255), 2)
    for r in (site.get("routes") or []):
        pp = [MP(*p) for p in (r.get("points") or [])]
        for i in range(len(pp) - 1):
            cv2.line(left0, pp[i], pp[i + 1], (120, 255, 160), 2)
        if len(pp) >= 2:
            arrows_along(left0, pp, (120, 255, 160))
            cv2.circle(left0, pp[0], 5, (120, 255, 160), 2)   # 시작점(속 빈 원)
    for e in (site.get("exits") or []):
        ln = e.get("line") or []
        if len(ln) >= 2:
            cv2.line(left0, MP(*ln[0]), MP(*ln[1]), (60, 90, 255), 3)

    outdir = ROOT / a.out
    outdir.mkdir(parents=True, exist_ok=True)
    tmp = outdir / f"{a.session}_{floor}.tmp.mp4"
    final = outdir / f"{a.session}_{floor}.mp4"
    vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), out_fps, (OW, OH))

    cur = {cid: None for cid in caps}
    pos = {cid: -1 for cid in caps}
    f1 = lambda v, d=1: "—" if v is None else f"{v:.{d}f}"   # noqa: E731

    try:
        for k in range(n_out):
            t = t0 + k / out_fps
            fr_obj = latest_at(frames, t, key=lambda f: f["ts"])
            obs = fr_obj["objects"] if fr_obj else []
            tp = latest_at(tl, t, key=lambda p: p["ts"]) or {}

            # ── 좌: 도면
            left = left0.copy()
            for o in obs:
                p = MP(o["x"], o["y"])
                c = cols.get(o["cam_id"], (200, 200, 200))
                cv2.circle(left, p, 5, c, -1)
                cv2.circle(left, p, 5, (20, 20, 20), 1)
            # 출구별 통과 인원 — 사람이 나갈 때마다 도면 위 숫자가 올라간다.
            # 값은 타임라인의 exit_counts(리플레이와 동일) 시점값.
            for e in (site.get("exits") or []):
                ln = e.get("line") or []
                if len(ln) < 2:
                    continue
                mx = (ln[0][0] + ln[1][0]) / 2.0
                my = (ln[0][1] + ln[1][1]) / 2.0
                ex, ey = MP(mx, my)
                n_out = (tp.get("exit_counts") or {}).get(e["id"], 0)
                txt = f"{e.get('name') or e['id']}  {n_out}명"
                wpx = 11 * len(txt)
                cv2.rectangle(left, (ex - wpx // 2, ey - 34), (ex + wpx // 2, ey - 12),
                              (18, 18, 18), -1)
                cv2.rectangle(left, (ex - wpx // 2, ey - 34), (ex + wpx // 2, ey - 12),
                              (60, 90, 255), 1)
                put(left, txt, (ex - wpx // 2 + 5, ey - 33), 15,
                    (120, 180, 255) if n_out else (150, 150, 150), True)

            cv2.rectangle(left, (0, 0), (LEFT_W, 62), (18, 18, 18), -1)
            put(left, f"{fname} — {label or a.session}", (14, 6), 18, (240, 240, 240), True)
            for i, (lb, c) in enumerate([("구역", (90, 200, 255)), ("병목", (80, 140, 255)),
                                         ("경로", (120, 255, 160)), ("출구", (60, 90, 255))]):
                x = 14 + i * 78
                cv2.rectangle(left, (x, 38), (x + 12, 50), c, -1)
                put(left, lb, (x + 18, 34), 14, (190, 190, 190))

            # ── 우: 카메라 그리드 (원본 fps)
            right = np.zeros((PANE_H, RIGHT_W, 3), np.uint8)
            for i, cid in enumerate(caps):
                want = int(round((t - t0) * src_fps[cid]))
                cap = caps[cid]
                if want != pos[cid]:
                    if want != pos[cid] + 1:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, want))
                    ok, fr = cap.read()
                    if ok:
                        cur[cid] = fr
                    pos[cid] = want
                fr = cur[cid]
                cell = np.zeros((ch, cw, 3), np.uint8)
                if fr is not None:
                    # 박스는 그리지 않는다 — 관측 5fps / 영상 30fps 라 심하게 깜빡인다.
                    cell, sc, ox, oy = fit(fr, cw, ch)
                    put(cell, cam_name(cid), (8, 6), 15, cols[cid], True)
                else:
                    put(cell, f"{cam_name(cid)} — 영상 없음", (10, ch // 2), 15, (120, 120, 120))
                cv2.rectangle(cell, (0, 0), (cw - 1, ch - 1), cols[cid], 2)
                right[(i // gcols) * ch:(i // gcols) * ch + ch,
                      (i % gcols) * cw:(i % gcols) * cw + cw] = cell

            # ── 하: 4대 지표 (1초 타임라인 홀드)
            bar = np.full((BAR_H, OW, 3), 18, np.uint8)
            zm = res.get("zone_metrics") or []
            started = sum(1 for z in zm if z.get("evacuation_start_at") is not None
                          and z["evacuation_start_at"] <= t)
            idrs = [z["idr"] for z in zm if z.get("idr") is not None
                    and (z.get("evacuation_start_at") or 1e18) <= t]
            ec = tp.get("exit_counts") or {}
            put(bar, "4대 지표", (16, 8), 15, (150, 150, 150))
            for i, (lab, val, c) in enumerate([
                    ("SEI 출구효율", f1(tp.get("sei"), 1), (190, 255, 120)),
                    ("EPFI 경로충실", f1(tp.get("epfi_avg"), 1), (255, 220, 120)),
                    ("CBS 병목누적", f1(tp.get("cbs_total"), 2), (120, 170, 255)),
                    ("IDR 평균", (f"{np.mean(idrs):.2f}" if idrs else "—")
                     + f"  ({started}/{len(zm)})", (200, 180, 255))]):
                x = 16 + i * int(OW * 0.135)
                put(bar, lab, (x, 34), 14, (160, 160, 160))
                put(bar, val, (x, 58), 30, c, True)
            x0 = 16 + 4 * int(OW * 0.135)
            put(bar, "관측", (x0, 8), 15, (150, 150, 150))
            # 추적 인원은 표시하지 않는다 — 요구사항. 출구 통과는 도면 위 출구 옆에도
            # 같은 값이 뜨고, 여기엔 전체 합계 성격으로 남긴다.
            for i, (lab, val) in enumerate([
                    ("출구 통과", " · ".join(f"{k2} {v2}" for k2, v2 in sorted(ec.items())) or "—"),
                    ("경과", f"{t - t0:5.1f}s / {dur:.0f}s")]):
                x = x0 + i * int(OW * 0.135)
                put(bar, lab, (x, 34), 14, (160, 160, 160))
                put(bar, val, (x, 60), 20, (235, 235, 235), True)

            frame = np.zeros((OH, OW, 3), np.uint8)
            frame[:PANE_H, :LEFT_W] = left
            frame[:PANE_H, LEFT_W:] = right
            frame[PANE_H:] = bar
            cv2.line(frame, (LEFT_W, 0), (LEFT_W, PANE_H), (60, 60, 60), 1)
            vw.write(frame)
            if k % max(1, int(out_fps * 10)) == 0:
                print(f"  {t - t0:5.1f}s / {dur:.0f}s · 객체 {len(obs)} · "
                      f"CBS {f1(tp.get('cbs_total'), 2)}", flush=True)
    finally:
        vw.release()
        for c in caps.values():
            c.release()

    # 어디서나 재생되게 H.264 재인코딩
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23",
                        str(final)], check=True)
        tmp.unlink(missing_ok=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        final = tmp
        print("  ! ffmpeg 재인코딩 실패 — mp4v 원본을 남깁니다")
    print(f"[viz] 완료 → {final}  ({final.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
