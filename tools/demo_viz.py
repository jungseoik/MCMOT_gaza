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
from tools import demo_ghosts as dghost       # noqa: E402
from tools import demo_tracks as dtrk         # noqa: E402
from tools import demo_obstacles as dobs      # noqa: E402
from tools import demo_paths as dpaths        # noqa: E402
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


# ── 밀도 히트맵 (VERSION 2)
# 화면(webui/static/main/heatmap.js)과 **같은 팔레트·같은 반경**을 쓴다. 발표에서
# 보여주는 그림과 운영 화면의 그림이 다르면 그때부터 설명이 꼬인다.
HEAT_R_M = 2.5          # 한 사람이 퍼뜨리는 반경 (실거리 m)
HEAT_SCALE = 0.5        # 누적 버퍼 배율 — 블롭은 어차피 뭉툭하다
HEAT_PEAK = 0.55        # 블롭 중심 알파 (heatmap.js 와 동일)
HEAT_STOPS = [(0.00, (0, 0, 0), 0), (0.12, (40, 110, 220), 90),
              (0.32, (40, 200, 200), 150), (0.55, (70, 210, 90), 185),
              (0.78, (245, 200, 40), 210), (1.00, (240, 60, 50), 230)]


def heat_lut():
    """256단계 조견표 (BGR + 알파) — 픽셀마다 보간하지 않게."""
    rgb = np.zeros((256, 3), np.float32)
    al = np.zeros(256, np.float32)
    for i in range(256):
        t = i / 255.0
        a, b = HEAT_STOPS[0], HEAT_STOPS[-1]
        for k in range(len(HEAT_STOPS) - 1):
            if HEAT_STOPS[k][0] <= t <= HEAT_STOPS[k + 1][0]:
                a, b = HEAT_STOPS[k], HEAT_STOPS[k + 1]
                break
        f = (t - a[0]) / ((b[0] - a[0]) or 1)
        rgb[i] = [a[1][2] + (b[1][2] - a[1][2]) * f,      # BGR 로 뒤집어 담는다
                  a[1][1] + (b[1][1] - a[1][1]) * f,
                  a[1][0] + (b[1][0] - a[1][0]) * f]
        al[i] = (a[2] + (b[2] - a[2]) * f) / 255.0
    return rgb, al


def heat_blob(r_px: int):
    """반경 r 의 방사 감쇠 커널 — 겹칠수록 알파가 쌓인다 = 밀집."""
    r = max(2, int(r_px))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1].astype(np.float32)
    d = np.hypot(xx, yy) / r
    return np.clip(1.0 - d, 0.0, 1.0) ** 1.6 * HEAT_PEAK


def heat_overlay(base, pts, blob, lut_rgb, lut_a):
    """도면 위에 밀도장을 깔아 돌려준다. base 는 건드리지 않는다."""
    h, w = base.shape[:2]
    sh, sw = max(1, int(h * HEAT_SCALE)), max(1, int(w * HEAT_SCALE))
    acc = np.zeros((sh, sw), np.float32)
    r = blob.shape[0] // 2
    for x, y in pts:
        cx, cy = int(x * HEAT_SCALE), int(y * HEAT_SCALE)
        x0, y0 = max(0, cx - r), max(0, cy - r)
        x1, y1 = min(sw, cx + r + 1), min(sh, cy + r + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        acc[y0:y1, x0:x1] += blob[y0 - (cy - r):y1 - (cy - r),
                                  x0 - (cx - r):x1 - (cx - r)]
    if acc.max() <= 0:
        return base
    idx = np.clip(acc * 255.0, 0, 255).astype(np.uint8)
    col = lut_rgb[idx]                       # (sh,sw,3) BGR
    alp = lut_a[idx][..., None]
    small = base[::1, ::1]
    col = cv2.resize(col, (w, h), interpolation=cv2.INTER_LINEAR)
    alp = cv2.resize(alp, (w, h), interpolation=cv2.INTER_LINEAR)[..., None]
    return (base * (1 - alp) + col * alp).astype(np.uint8)


def heat_legend(img, x0, y0):
    """밀집도 색띠 — 색만 깔아 두면 "빨간 게 뭔데"가 남는다."""
    w, h = 150, 12
    g = np.linspace(0, 255, w).astype(np.uint8)
    rgb, _ = heat_lut()
    bar = rgb[g][None, :, :].repeat(h, 0).astype(np.uint8)
    ov = img.copy()
    cv2.rectangle(ov, (x0 - 8, y0 - 24), (x0 + w + 8, y0 + h + 8), (28, 28, 32), -1)
    cv2.addWeighted(ov, 0.80, img, 0.20, 0, img)
    img[y0:y0 + h, x0:x0 + w] = bar
    put(img, "밀집도", (x0, y0 - 23), 15, (232, 232, 240), True)
    put(img, "낮음", (x0, y0 + h + 1), 13, (170, 170, 180))
    put(img, "높음", (x0 + w - 30, y0 + h + 1), 13, (170, 170, 180))


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
    ap.add_argument("--map", default="dots", choices=("dots", "heat"),
                    help="2D 맵 표출 — dots(점+흔적) / heat(밀도 히트맵)")
    ap.add_argument("--tracks", default="raw", choices=("raw", "ideal", "drawn"),
                    help="점의 출처 — raw(원 관측, ④ 리플레이와 같은 그림) / "
                         "ideal(이어 붙인 N명) / drawn(**사람이 :8930 편집기에서 "
                         "직접 그린 동선** — 리플레이 타임라인 위에 찍은 것이라 "
                         "그리드와 싱크가 맞는다).")
    a = ap.parse_args()

    db, floor = find_session(a.session, a.floor)
    meta = recorder.load_meta(db)
    label = drill_label(a.session) or a.session
    print(f"[demo] {a.session} · {floor} · {label} · 주지표 {a.metric}")

    # 장애물 표시(:8920 편집기) — 그림에도 얹고, **경로 산출에서는 벽**으로 친다.
    # 지표 계산에는 넣지 않는다(사람이 그린 공통 경로 기준 산출은 그대로).
    obstacles = dobs.load(floor)
    osig = dobs.signature(obstacles)
    if obstacles:
        print(f"[demo] 장애물 표시 {len(obstacles)}개 — 도면에 표출 + 경로 산출에 반영")

    # 개인 경로(있으면) — 인트로에 쓴다
    from system.evac import person_routes as pr
    proutes = pr.load(db)
    if proutes is None or proutes.get("_obstacles_sig") != osig:
        why = "없음" if proutes is None else "장애물 표시가 바뀜"
        print(f"[demo] 재실자별 경로 산출 중… ({why})")
        proutes = pr.generate(db, ROOT / "data/sites/default",
                              extra_walls=[o["points"] for o in obstacles] or None)
        proutes["_obstacles_sig"] = osig
        pr.save(db, proutes)
    print(f"[demo] 개인 경로 {len(proutes['routes'])}개 · 산출 불가 "
          f"{proutes['stats']['unreachable']}명")

    # 붙박이 오검출 제외 — 화면뿐 아니라 **엔진에도 넣지 않는다**(지표와 그림이
    # 어긋나면 그게 더 나쁜 거짓말이다). 목록은 사람이 크롭으로 확인해 확정한 것.
    ghosts = dghost.load(a.session)
    if ghosts:
        print(f"[demo] 붙박이 오검출 {len(ghosts)}개 트랙 제외 — "
              + ", ".join(f"{g['key']}({g.get('verified') or g['reason']})" for g in ghosts))

    th = json.loads(a.th) if a.th else {}
    if th:
        print(f"[demo] 임계값 오버라이드 {th} — 화면에 표기합니다")
    ov = {}
    if th:
        ov["thresholds"] = th
    if ghosts:
        ov["exclude_tracks"] = [g["key"] for g in ghosts]
    result, timeline, frames, _m = run_replay(db, ov, fps=5.0)
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
    dobs.draw(left0, obstacles, MP)                      # 장애물 표시(해치)
    leg = dobs.legend(obstacles)                         # 범례 — 색만으론 안 읽힌다
    if leg:
        # 도면이 흰 바탕이라 글자를 그냥 얹으면 안 읽힌다 — 어두운 판을 깐다
        h = 10 + len(leg) * 24
        x0, y0 = 12, PANE_H - 12 - h
        ov = left0.copy()
        cv2.rectangle(ov, (x0, y0), (x0 + 168, y0 + h), (28, 28, 32), -1)
        cv2.addWeighted(ov, 0.80, left0, 0.20, 0, left0)
        for i, (nm, bgr) in enumerate(leg):
            y = y0 + 6 + i * 24
            cv2.rectangle(left0, (x0 + 10, y + 4), (x0 + 26, y + 18), bgr, -1)
            put(left0, nm, (x0 + 34, y + 1), 16, (232, 232, 240), True)

    # 히트맵 준비 — 반경은 **실거리**로 잡는다(화면 px 로 고정하면 도면 축척마다
    # 같은 밀집이 다른 그림이 된다).
    HEAT = a.map == "heat"
    if HEAT:
        heat_r = max(6, int(HEAT_R_M / m_per_px * msc * HEAT_SCALE))
        blob = heat_blob(heat_r)
        lut_rgb, lut_a = heat_lut()
        heat_legend(left0, 22, 38)      # 좌하단은 장애물 범례가 쓴다
        print(f"[demo] 히트맵 판 — 반경 {HEAT_R_M}m = {heat_r}px(누적버퍼)")

    DOT = session_color(a.session)
    outdir = ROOT / a.out
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"{a.metric}_{label.split()[0]}" + ("_heat" if a.map == "heat" else "")
    tmp, final = outdir / f"{stem}.tmp.mp4", outdir / f"{stem}.mp4"
    vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), out_fps, (OW, OH))

    f1 = lambda v, d=1: "—" if v is None else f"{v:.{d}f}"   # noqa: E731
    n_zones = len(res.get("zone_metrics") or [])

    def interp_objects(t):
        """관측(5fps)을 출력 fps 로 **선형 보간**한다.

        보간 없이 latest_at 으로 집으면 점이 초당 5번만 움직여 30fps 영상에서
        뚝뚝 끊긴다. 앞뒤 프레임을 gid 로 짝지어 사이를 메운다 — ④ 리플레이
        화면(currentInterp)과 같은 방식이라 보이는 그림이 일치한다.
        좌표를 만들어내는 게 아니라 **표출만 매끄럽게** 하는 것이고, 지표 계산에는
        일절 안 쓴다(지표는 원 관측으로 이미 산출돼 있다).
        """
        n = len(frames)
        lo, hi = 0, n - 1
        i = 0
        while lo <= hi:                                   # ts <= t 인 마지막 프레임
            mid = (lo + hi) // 2
            if frames[mid]["ts"] <= t:
                i = mid; lo = mid + 1
            else:
                hi = mid - 1
        A = frames[i]
        B = frames[min(n - 1, i + 1)]
        span = (B["ts"] - A["ts"]) or 1.0
        al = max(0.0, min(1.0, (t - A["ts"]) / span))
        nxt = {o["gid"]: o for o in B["objects"]}
        out = []
        for o in A["objects"]:
            b = nxt.get(o["gid"])
            out.append({**o,
                        "x": o["x"] + (b["x"] - o["x"]) * al if b else o["x"],
                        "y": o["y"] + (b["y"] - o["y"]) * al if b else o["y"]})
        return out
    # 이상적 궤적 — 카메라 경계에서 끊기는 id 를 이어 붙여 각본 인원만큼 만든
    # 연속 궤적. **표출 전용**이고 위의 지표(res/tl)는 원 관측 그대로다.
    ideal = None
    if a.tracks == "ideal":
        ideal = dtrk.load(a.session)
        if ideal is None:
            print("이상적 궤적이 없습니다 — tools/demo_tracks_build.py 를 먼저 돌리세요")
            return 1
    elif a.tracks == "drawn":
        from system.evac.person_routes import _edits_from_floor_json
        from system.evac.route_solver import build_distfield
        _carve, _blocks = _edits_from_floor_json(ROOT / "data/sites/default",
                                                 floor, m_per_px)
        if not _carve:
            _carve = [r["points"] for r in (site.get("routes") or [])
                      if len(r.get("points") or []) >= 2]
        _df = build_distfield(str(mapf),
                              [[tuple(e["line"][0]), tuple(e["line"][1])]
                               for e in (site.get("exits") or []) if e.get("line")],
                              m_per_px, carve_paths=_carve or None, carve_w_m=2.0,
                              clearance_m=0.15, extra_walls=_blocks or None,
                              hard_walls=[o["points"] for o in obstacles] or None)
        ideal = dpaths.load(a.session, t_end=dur, df=_df, m_per_px=m_per_px)
        if ideal is None:
            print("그린 동선이 없습니다 — tools/path_editor.py (:8930) 에서 먼저 그리세요")
            return 1
        print(f"[demo] {dpaths.summary(ideal)}")
    if ideal:
        print(f"[demo] 이상적 궤적 {ideal['n']}명 — 끊김 없이 표출(지표는 원 관측 그대로)")

        def interp_objects(t):                       # noqa: F811
            return dtrk.at(ideal, t - t0)

    n_intro = int(INTRO_SEC * out_fps)
    n_main = int(dur * out_fps)
    cur = {c: None for c in caps}
    pos = {c: -1 for c in caps}
    trail: list[tuple[float, float, float]] = []       # (ts, x, y)

    def idr_at(t):
        """그 시점의 IDR — 이미 **개시한 구역들의 평균 m/s**.

        타임라인에는 개시 구역 수(zones_started)만 있어서 "1/1" 밖에 못 띄웠다.
        지표 자체는 m/s 라 수치로 보여야 한다(④ 리플레이 카드와 같은 계산).
        아직 아무 구역도 개시 안 했으면 None — 0 이 아니다.
        """
        done = [z for z in (res.get("zone_metrics") or [])
                if z.get("evacuation_start_at") is not None
                and z["evacuation_start_at"] <= t and z.get("idr") is not None]
        if not done:
            return None, 0, None
        v = sum(z["idr"] for z in done) / len(done)
        first = min(z["evacuation_start_at"] for z in done) - t0
        return v, len(done), first

    def bar_panel(t, tp, obs, exits_now):
        bar = np.full((BAR_H, OW, 3), 22, np.uint8)
        el = max(0.0, t - t0)
        m, s = int(el // 60), int(el % 60)
        put(bar, "경보 후 경과", (26, 12), 17, (150, 150, 150))
        put(bar, f"{m}:{s:02d}", (22, 34), 62, (255, 255, 255), True)
        iv, idone, ifirst = idr_at(t)
        x = int(OW * 0.15)
        for name, val, sub in (
                ("SEI", f1(tp.get("sei")), "출구 활용"),
                ("EPFI", f1(tp.get("epfi_avg")), "경로 충실"),
                ("CBS", f1(tp.get("cbs_total"), 2), "병목 누적"),
                ("IDR", f1(iv, 2),
                 (f"개시 {ifirst:.0f}s · {idone}/{n_zones} 구역" if iv is not None
                  else f"미개시 · 0/{n_zones} 구역"))):
            main = (name == a.metric)
            col = ACCENT[name] if main else (140, 140, 140)
            put(bar, name, (x, 14), 20 if main else 16, col, True)
            put(bar, val, (x, 38), 52 if main else 34, col if main else (225, 225, 225), True)
            if name == "IDR" and iv is not None:
                put(bar, "m/s", (x + (118 if main else 84), 66 if main else 58),
                    16, (150, 150, 150))
            put(bar, sub, (x, 100 if main else 80), 15, (120, 120, 120))
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
            obs = interp_objects(t)
            tp = latest_at(tl, t, key=lambda p: p["ts"]) or {}
            for o in obs:
                trail.append((t, o["x"], o["y"]))
            trail[:] = [q for q in trail if t - q[0] <= TRAIL_SEC]

            left = left0.copy()
            if HEAT:
                # 밀집을 보여주는 판 — 흔적은 뺀다. 흔적선과 밀도장이 겹치면
                # 어느 쪽이 "몰려 있다"인지 안 읽힌다.
                left = heat_overlay(left, [MP(o["x"], o["y"]) for o in obs],
                                    blob, lut_rgb, lut_a)
                for o in obs:                       # 누가 몇 명인지는 남겨 둔다
                    cv2.circle(left, MP(o["x"], o["y"]), 4, (255, 255, 255), -1)
                    cv2.circle(left, MP(o["x"], o["y"]), 4, (30, 30, 30), 1)
            elif ideal:
                # 사람마다 **자기 자취**를 끝까지 남긴다 — id 가 유지된다는 게
                # 눈에 보여야 한다. 최근 구간일수록 진하게 두 겹으로 긋는다.
                ov = left.copy()
                for pp in ideal["people"]:
                    tr_ = dtrk.trail(ideal, pp, t - t0)
                    if len(tr_) >= 2:
                        cv2.polylines(ov, [np.array([MP(*q) for q in tr_], np.int32)],
                                      False, DOT, 2, cv2.LINE_AA)
                cv2.addWeighted(ov, 0.40, left, 0.60, 0, left)
                for pp in ideal["people"]:
                    tr_ = dtrk.trail(ideal, pp, t - t0, back=6.0)
                    if len(tr_) >= 2:
                        cv2.polylines(left, [np.array([MP(*q) for q in tr_], np.int32)],
                                      False, DOT, 3, cv2.LINE_AA)
                for o in obs:
                    cv2.circle(left, MP(o["x"], o["y"]), 8, DOT, -1)
                    cv2.circle(left, MP(o["x"], o["y"]), 8, (20, 20, 20), 1)
            else:
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
