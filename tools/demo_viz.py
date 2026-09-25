#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""발표용 전용 시각화 — 녹화 세션 → "지표가 보이는" 영상.

session_viz.py 와 목적이 다르다. 그쪽은 **진단**(기록이 맞나)이고 여기는 **설명**이다.
지표가 무엇을 재고 있는지 한눈에 들어오게 화면을 깎는다:

  ① 인트로 — 재실자별 피난경로를 맵에 한 번 보여주고 사라진다.
     "각자 이 길로 가야 한다"를 먼저 깔아야 뒤의 이탈·개시가 읽힌다.
  ② 본편 — 점은 **한 가지 색**(카메라별 색은 사람이 카메라를 넘을 때마다 바뀌어
     산만하다), 뒤에 **이동 흔적**을 남긴다.
  ③ 맵에는 CBS 병목과 출입구만 **도형으로만** 그린다 — IDR 구역·피난경로·글자는
     전부 뺀다. 도면이 지저분하면 정작 볼 것(사람의 움직임)이 안 보인다.
  ④ 하단 — 경보 후 경과(영상 시작 = 00:00)와 4대 지표를 크게. 카운팅은 따로 패널.

    python tools/demo_viz.py --session sess-... --metric CBS --out results/demo/03_CBS
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2                                    # noqa: E402
import numpy as np                            # noqa: E402

from system.metrics import recorder           # noqa: E402
from system.metrics.replay import run_replay  # noqa: E402
from tools.session_viz import (                # noqa: E402
    drill_label, find_session, fit, latest_at, put, resolve_clips,
)


def cover(img, w, h):
    """셀을 **꽉 채우게** 잘라 넣는다(letterbox 대신 crop).

    fit() 은 비율을 지키느라 위아래 검은 띠를 남기는데, 카메라 7대를 3×3에 넣으면
    그 띠가 오른쪽 화면의 절반을 먹는다. 발표용 화면에서는 가장자리를 조금 잃더라도
    꽉 찬 그림이 낫다(진단용 session_viz 는 fit 을 그대로 쓴다).
    """
    ih, iw = img.shape[:2]
    s = max(w / iw, h / ih)
    r = cv2.resize(img, (max(1, int(iw * s)), max(1, int(ih * s))))
    y = max(0, (r.shape[0] - h) // 2)
    x = max(0, (r.shape[1] - w) // 2)
    return r[y:y + h, x:x + w]

INTRO_SEC = 4.0          # 인트로 길이(초) — 경로를 보여주고 페이드아웃
TRAIL_SEC = 10.0         # 점 뒤에 남길 흔적 길이(초)
BG = (16, 16, 18)
# cv2 는 BGR 이다 — RGB 로 적으면 빨강이 파랑으로 나온다(실측).
ACCENT = {"IDR": (36, 191, 251),    # 주황
          "EPFI": (250, 165, 96),   # 파랑
          "CBS": (113, 113, 248),   # 빨강
          "SEI": (128, 222, 74)}    # 초록
METRIC_DESC = {"IDR": "경보 후 각 구역이 얼마나 빨리 피난을 시작했나",
               "EPFI": "권장 경로를 얼마나 충실히 따라갔나",
               "CBS": "병목에서 임계밀도를 넘긴 혼잡이 얼마나 쌓였나",
               "SEI": "출구들이 설계 의도대로 고르게 쓰였나"}


def session_color(key: str):
    """세션 id 로 점 색 하나를 정한다 — 화면(mcSessionColor)과 같은 규칙."""
    h = 0
    for ch in str(key):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    hsv = np.uint8([[[int(h % 180), 180, 245]]])
    b, g, r = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0][0]
    return int(b), int(g), int(r)


def main() -> int:
    ap = argparse.ArgumentParser(description="발표용 시각화 — 지표가 보이는 영상")
    ap.add_argument("--session", required=True)
    ap.add_argument("--floor")
    ap.add_argument("--metric", default="EPFI", choices=sorted(ACCENT),
                    help="이 영상이 주인공으로 삼을 지표")
    ap.add_argument("--out", default="results/demo")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--fps", type=float, default=0)
    ap.add_argument("--sec", type=float, default=0)
    ap.add_argument("--title", default="")
    ap.add_argument("--th", default="",
                    help='임계값 오버라이드 JSON — 예 \'{"dt_hold":1.0}\'. '
                         "배포 기본값에서 개시가 안 서는 각본을 보여줄 때 쓴다. "
                         "쓰면 화면에 그 사실을 표기한다(숨기면 오해를 부른다).")
    a = ap.parse_args()

    db, floor = find_session(a.session, a.floor)
    meta = recorder.load_meta(db)
    label = drill_label(a.session) or a.session
    print(f"[demo] {a.session} · {floor} · {label} · 주지표 {a.metric}")

    # 개인 경로(있으면) — 인트로에 쓴다
    from system.evac import person_routes as pr
    proutes = pr.load(db)
    if proutes is None:
        print("[demo] 재실자별 경로 산출 중…")
        proutes = pr.generate(db, ROOT / "data/sites/default")
        pr.save(db, proutes)
    print(f"[demo] 개인 경로 {len(proutes['routes'])}개 · 산출 불가 "
          f"{proutes['stats']['unreachable']}명")

    th = json.loads(a.th) if a.th else {}
    if th:
        print(f"[demo] 임계값 오버라이드 {th} — 화면에 표기합니다")
    result, timeline, frames, _m = run_replay(db, {"thresholds": th} if th else {}, fps=5.0)
    res = result.model_dump()
    tl = [t.model_dump() for t in timeline]
    site = meta["site_view"]

    cam_ids = sorted({c["cam_id"] for c in meta.get("cameras", [])})
    clips, desc = resolve_clips(cam_ids, label, None, None)
    print(f"[demo] {desc}")
    caps, src_fps = {}, {}
    for cid, fp in clips.items():
        cap = cv2.VideoCapture(str(fp))
        if cap.isOpened():
            caps[cid] = cap
            src_fps[cid] = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if not caps:
        print("영상을 하나도 열지 못했습니다"); return 1
    out_fps = a.fps or max(src_fps.values())

    t0 = float(meta["alarm_ts"])
    grid_t0 = float(meta.get("source_t0") or t0)
    t_end = max(frames[-1]["ts"], tl[-1]["ts"])
    dur = (t_end - t0) if not a.sec else min(a.sec, t_end - t0)

    # ── 레이아웃
    OW = a.width
    LEFT_W = int(OW * 0.46)
    RIGHT_W = OW - LEFT_W
    PANE_H = int(OW * 0.44)
    BAR_H = int(OW * 0.135)
    OH = PANE_H + BAR_H

    mp = site.get("map") or {}
    m_per_px = mp.get("m_per_px") or (mp["scale_m"] / mp["scale_px"] if mp.get("scale_px") else 1)
    mapf = ROOT / "data/sites/default" / ("map.png" if floor == "default" else f"map_{floor}.png")
    mimg = cv2.imread(str(mapf))
    base, msc, mox, moy = fit(mimg, LEFT_W, PANE_H)

    def MP(x, y):
        return int(x * msc + mox), int(y * msc + moy)

    # 맵 바탕 — **도형만**. 글자·구역·경로 없음.
    left0 = np.full((PANE_H, LEFT_W, 3), BG[0], np.uint8)
    left0[:] = base
    for bn in (site.get("bottlenecks") or []):          # CBS 병목
        poly = np.array([MP(*p) for p in bn["polygon"]], np.int32)
        ov = left0.copy(); cv2.fillPoly(ov, [poly], (60, 90, 255))
        cv2.addWeighted(ov, 0.16, left0, 0.84, 0, left0)
        cv2.polylines(left0, [poly], True, (60, 90, 255), 2)
    for e in (site.get("exits") or []):                  # 출입구
        ln = e.get("line") or []
        if len(ln) >= 2:
            cv2.line(left0, MP(*ln[0]), MP(*ln[1]), (255, 190, 60), 6, cv2.LINE_AA)

    DOT = session_color(a.session)
    outdir = ROOT / a.out
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"{a.metric}_{label.split()[0]}"
    tmp, final = outdir / f"{stem}.tmp.mp4", outdir / f"{stem}.mp4"
    vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), out_fps, (OW, OH))

    f1 = lambda v, d=1: "—" if v is None else f"{v:.{d}f}"   # noqa: E731
    n_zones = len(res.get("zone_metrics") or [])
    n_intro = int(INTRO_SEC * out_fps)
    n_main = int(dur * out_fps)
    cur = {c: None for c in caps}
    pos = {c: -1 for c in caps}
    trail: list[tuple[float, float, float]] = []       # (ts, x, y)

    def bar_panel(t, tp, obs, exits_now):
        bar = np.full((BAR_H, OW, 3), 22, np.uint8)
        el = max(0.0, t - t0)
        m, s = int(el // 60), int(el % 60)
        put(bar, "경보 후 경과", (26, 12), 17, (150, 150, 150))
        put(bar, f"{m}:{s:02d}", (22, 34), 62, (255, 255, 255), True)
        x = int(OW * 0.15)
        for name, val in (("SEI", f1(tp.get("sei"))), ("EPFI", f1(tp.get("epfi_avg"))),
                          ("CBS", f1(tp.get("cbs_total"), 2)),
                          # TimelinePoint 에는 zones_started 만 있다 — 분모는 결과에서 센다
                          ("IDR", f"{tp.get('zones_started', 0)}/{n_zones}")):
            main = (name == a.metric)
            col = ACCENT[name] if main else (140, 140, 140)
            put(bar, name, (x, 14), 20 if main else 16, col, True)
            put(bar, val, (x, 38), 52 if main else 34, col if main else (225, 225, 225), True)
            if main:                                  # 값 줄만 감싼다(바 전체가 아니라)
                cv2.rectangle(bar, (x - 12, 8), (x + int(OW * 0.125), 96), col, 2)
            x += int(OW * 0.145)
        # 카운팅 — 따로
        cx = int(OW * 0.74)
        cv2.line(bar, (cx - 24, 12), (cx - 24, BAR_H - 14), (70, 70, 70), 1)
        put(bar, "카운팅", (cx, 12), 17, (150, 150, 150))
        put(bar, f"재실 {len(obs)}명", (cx, 34), 30, (235, 235, 235), True)
        ex_txt = " · ".join(f"{k} {v}" for k, v in sorted(exits_now.items())) or "—"
        put(bar, f"출구 통과  {ex_txt}", (cx, 72), 24, (255, 190, 60), True)
        if th:   # 기본값과 다른 임계로 잰 값이라는 걸 숨기지 않는다
            put(bar, "임계값 " + " · ".join(f"{k}={v}" for k, v in th.items()),
                (26, BAR_H - 30), 17, (130, 130, 130))
        return bar

    try:
        # ── ① 인트로 — 개인 경로를 깔고 페이드아웃
        for k in range(n_intro):
            p = k / max(1, n_intro - 1)
            left = left0.copy()
            fade = 1.0 if p < 0.65 else max(0.0, 1 - (p - 0.65) / 0.35)
            show = int(len(proutes["routes"]) * min(1.0, p / 0.45))
            ov = left.copy()
            for r in proutes["routes"][:show]:
                pts = np.array([MP(*q) for q in r["points"]], np.int32)
                cv2.polylines(ov, [pts], False, DOT, 2, cv2.LINE_AA)
                cv2.circle(ov, tuple(pts[0]), 4, DOT, -1)
            cv2.addWeighted(ov, 0.85 * fade, left, 1 - 0.85 * fade, 0, left)
            frame = np.full((OH, OW, 3), 20, np.uint8)
            frame[:PANE_H, :LEFT_W] = left
            right = np.full((PANE_H, RIGHT_W, 3), 18, np.uint8)
            put(right, a.title or label, (40, int(PANE_H * 0.36)), 44, (255, 255, 255), True)
            put(right, f"재실자별 피난경로 {len(proutes['routes'])}개",
                (40, int(PANE_H * 0.36) + 70), 28, DOT, True)
            if proutes["stats"]["unreachable"]:
                put(right, f"경로 산출 불가 {proutes['stats']['unreachable']}명",
                    (40, int(PANE_H * 0.36) + 112), 22, (150, 150, 150))
            put(right, a.metric, (40, int(PANE_H * 0.36) + 170), 34,
                ACCENT[a.metric], True)
            put(right, METRIC_DESC[a.metric], (40, int(PANE_H * 0.36) + 216), 24,
                (200, 200, 200))
            frame[:PANE_H, LEFT_W:] = right
            frame[PANE_H:] = bar_panel(t0, {}, [], {})
            vw.write(frame)

        # ── ② 본편
        for k in range(n_main):
            t = t0 + k / out_fps
            fr = latest_at(frames, t, key=lambda f: f["ts"])
            obs = fr["objects"] if fr else []
            tp = latest_at(tl, t, key=lambda p: p["ts"]) or {}
            for o in obs:
                trail.append((t, o["x"], o["y"]))
            trail[:] = [q for q in trail if t - q[0] <= TRAIL_SEC]

            left = left0.copy()
            # 흔적 — 오래된 것일수록 흐리게. 세 단계로 나눠 겹쳐 그린다
            # (점마다 알파를 주려면 매번 addWeighted 라 느리다).
            for lo, hi, al, rr in ((0.66, 1.0, 0.18, 2), (0.33, 0.66, 0.34, 3),
                                   (0.0, 0.33, 0.55, 4)):
                ov = left.copy()
                drew = False
                for ts_, x, y in trail:
                    age = (t - ts_) / TRAIL_SEC
                    if lo <= age < hi:
                        cv2.circle(ov, MP(x, y), rr, DOT, -1); drew = True
                if drew:
                    cv2.addWeighted(ov, al, left, 1 - al, 0, left)
            for o in obs:
                cv2.circle(left, MP(o["x"], o["y"]), 7, DOT, -1)
                cv2.circle(left, MP(o["x"], o["y"]), 7, (20, 20, 20), 1)

            right = np.zeros((PANE_H, RIGHT_W, 3), np.uint8)
            n = len(caps)
            ccols = int(np.ceil(np.sqrt(n))); crows = int(np.ceil(n / ccols))
            cw, ch = RIGHT_W // ccols, PANE_H // crows
            for i, cid in enumerate(caps):
                want = int(round((t - grid_t0) * src_fps[cid]))
                cap = caps[cid]
                if want != pos[cid]:
                    if want != pos[cid] + 1:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, want))
                    ok, im = cap.read()
                    if ok:
                        cur[cid] = im
                    pos[cid] = want
                gy, gx = divmod(i, ccols)
                cell = np.zeros((ch, cw, 3), np.uint8)
                if cur[cid] is not None:
                    cell = cover(cur[cid], cw, ch)
                cv2.rectangle(cell, (0, 0), (cw - 1, ch - 1), (45, 45, 45), 1)
                right[gy * ch:(gy + 1) * ch, gx * cw:(gx + 1) * cw] = cell

            ec = tp.get("exit_counts") or {}
            frame = np.full((OH, OW, 3), 20, np.uint8)
            frame[:PANE_H, :LEFT_W] = left
            frame[:PANE_H, LEFT_W:] = right
            frame[PANE_H:] = bar_panel(t, tp, obs, ec)
            cv2.line(frame, (LEFT_W, 0), (LEFT_W, PANE_H), (60, 60, 60), 1)
            vw.write(frame)
            if k % max(1, int(out_fps * 10)) == 0:
                print(f"  {t - t0:5.1f}s / {dur:.0f}s · 재실 {len(obs)}", flush=True)
    finally:
        vw.release()
        for c in caps.values():
            c.release()

    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22",
                        str(final)], check=True)
        tmp.unlink(missing_ok=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        final = tmp
    # 근거 수치도 같이 남긴다 — 영상만 있으면 나중에 "이 숫자 뭐였지"가 된다
    (outdir / f"{stem}.json").write_text(json.dumps({
        "session_id": a.session, "floor": floor, "label": label, "metric": a.metric,
        "epfi_avg": res.get("epfi_avg"), "sei": res.get("sei"),
        "cbs_total": res.get("cbs_total"),
        "zone_metrics": res.get("zone_metrics"),
        "exit_metrics": res.get("exit_metrics"),
        "person_routes": proutes["stats"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[demo] 완료 → {final}  ({final.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
