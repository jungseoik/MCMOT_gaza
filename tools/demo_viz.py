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
import collections
import math
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
from tools import demo_bottleneck as dbn      # noqa: E402
from tools import demo_ghosts as dghost       # noqa: E402

# 그린 10명 **말고** 실측 그대로 얹을 트랙 (세션별)
#   before — 이 시각 **이전에 시작하는** 트랙 전부 (12 SEI 의 계단 구간)
#   gids   — 따로 지정한 트랙 (다 나간 뒤 뒤늦게 나오는 사람)
EXTRA_RAW = {
    "sess-1789693709646": {"before": 39.0,
                           "gids": ["rh_aihub2_cam36:15", "rh_aihub2_cam35:18"]},
}
from tools import demo_tracks as dtrk         # noqa: E402
from tools import demo_obstacles as dobs      # noqa: E402
from tools import demo_paths as dpaths        # noqa: E402
from tools.session_viz import (                # noqa: E402
    drill_label, find_session, fit, latest_at, put, resolve_clips, text_w,
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
    ap.add_argument("--metric", default="EPFI", choices=sorted(ACCENT) + ["ALL"],
                    help="이 영상이 주인공으로 삼을 지표 — ALL 은 4대 지표를 똑같이 "
                         "(테두리 강조 없음 · 인트로는 층 피난경로). 실훈련처럼 각본이 "
                         "지표 하나를 겨누지 않는 녹화에 쓴다")
    ap.add_argument("--out", default="results/demo")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--fps", type=float, default=0)
    ap.add_argument("--sec", type=float, default=0)
    ap.add_argument("--metrics", default="v1", choices=("v1", "v2"),
                    help="v1=원 관측(기본) · v2=**좌측 맵의 그 점**으로 재산출")
    ap.add_argument("--drop-exits", default="",
                    help="v2 — 없앤 셈 칠 출구 id(콤마). 아무도 안 쓴 가까운 출구를 "
                         "빼면 SEI 가 실제 쓰임새를 반영한다")
    ap.add_argument("--drop-bn", default="", help="v2 — 없앤 셈 칠 병목 id(콤마)")
    ap.add_argument("--v-th", type=float, default=0.30,
                    help="v2 의 피난개시 속도 임계 (사이트 기본 0.5)")
    ap.add_argument("--tag", default="",
                    help="파일명 꼬리표. 같은 세션을 화면만 바꿔 다시 뽑을 때 판을 "
                         "구분한다 — 예 `--tag v3` → IDR_01_v3.mp4")
    ap.add_argument("--title", default="",
                    help="인트로 머리글(기본 AIHub). 각본 이름이 아니라 **어디서 찍었나**")
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
    ap.add_argument("--package", default=None,
                    help="그리드 영상을 찾을 리허설 패키지 id (예 cj-drill-1022). 라벨에 "
                         "시나리오 번호가 없는 녹화에 쓴다 — session_viz 와 같은 인자")
    ap.add_argument("--scenario", default=None,
                    help="그 패키지의 시나리오 id (예 scenario_16F_only)")
    ap.add_argument("--clip-root", type=Path, default=None,
                    help="그리드 카메라 영상을 이 폴더의 같은 상대경로(<시나리오폴더>/<cam>.mp4)"
                         "에서 읽는다 — 비식별 사본(tools/deid/deid_video.py 산출) 등. "
                         "사본이 하나라도 없으면 원본으로 대체하지 않고 멈춘다")
    ap.add_argument("--no-grid", action="store_true",
                    help="우측 카메라 그리드 없이 도면이 화면 가로 전체를 쓴다. "
                         "출력 크기·하단 지표 줄·인트로·지표 값은 그리드판과 같다")
    a = ap.parse_args()

    db, floor = find_session(a.session, a.floor)
    meta = recorder.load_meta(db)
    label = drill_label(a.session) or a.session
    print(f"[demo] {a.session} · {floor} · {label} · 주지표 {a.metric}")

    # 장애물 표시(:8920 편집기) — 그림에도 얹고, **경로 산출에서는 벽**으로 친다.
    # 지표 계산에는 넣지 않는다(사람이 그린 공통 경로 기준 산출은 그대로).
    obstacles = dobs.load(floor)
    dbn.load(floor)        # 병목 편집본(:8940)
    osig = dobs.signature(obstacles)
    if obstacles:
        print(f"[demo] 장애물 표시 {len(obstacles)}개 — 도면에 표출 + 경로 산출에 반영")

    # 없앤 셈 칠 출구·병목 — **화면과 지표가 같은 가정 위에** 있어야 한다.
    _de = [x for x in a.drop_exits.split(",") if x.strip()]
    _db2 = [x for x in a.drop_bn.split(",") if x.strip()]

    # 개인 경로(있으면) — 인트로에 쓴다
    from system.evac import person_routes as pr
    if a.metric == "ALL":
        # 4대 지표 통합판 — 인트로는 **층 피난경로**(사람이 그린 공통 경로)를 깐다.
        # 실훈련은 트랙 조각이 수백 개라 재실자별 경로는 선이 엉켜 읽히지 않고
        # 산출도 오래 걸린다. 지표(EPFI·IDR 정렬도)도 이 공통 경로 기준이다.
        _sr = [r for r in (meta["site_view"].get("routes") or []) if len(r.get("points") or []) >= 2]
        proutes = {"routes": _sr, "stats": {"unreachable": 0, "site_routes": len(_sr)}}
    elif _de or _db2:
        # "그 출구가 없었다면" 을 **그림에도** 적용한다. 안 하면 인트로에는 없앤
        # 출구로 가는 경로가 깔리는데 아래 SEI 는 100 을 띄운다 — 실측 12 SEI:
        # 73개 중 60개가 없앤 exit-0 행이었다. 사이드카(양쪽 출구 판)는 다른
        # 영상이 쓰므로 **캐시를 읽지도 쓰지도 않는다.**
        from system.config.schema import SiteConfig as _SC
        _sv0 = dict(meta["site_view"])
        if _de:
            _sv0["exits"] = [e for e in (_sv0.get("exits") or [])
                             if e["id"] not in set(_de)]
        if _db2:
            _sv0["bottlenecks"] = [b for b in (_sv0.get("bottlenecks") or [])
                                   if b["id"] not in set(_db2)]
        print(f"[demo] 재실자별 경로 산출 중… (없앤 셈: 출구 {_de or '—'} · "
              f"병목 {_db2 or '—'})")
        proutes = pr.generate(db, ROOT / "data/sites/default",
                              extra_walls=[o["points"] for o in obstacles] or None,
                              site_override=_SC.model_validate(_sv0))
    else:
        proutes = pr.load(db)
        if (proutes is None or proutes.get("_obstacles_sig") != osig
                or proutes.get("algo_ver") != pr.ALGO_VER):
            why = ("없음" if proutes is None else
                   "장애물 표시가 바뀜" if proutes.get("_obstacles_sig") != osig
                   else f"산출 방식이 바뀜(v{proutes.get('algo_ver')}→v{pr.ALGO_VER})")
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
    if a.metrics == "v2":
        # **화면에 보이는 그 점으로** 다시 낸 수치. 원 관측은 트랙이 끊기고
        # 카메라 사각이 있어, 보는 것과 세는 것이 어긋난다.
        from tools.demo_metrics_v2 import run_v2
        _d1, _d2, _dots, _site2, _tl2 = run_v2(
            a.session, a.floor, a.tracks if a.tracks != "raw" else "drawn",
            v_th=a.v_th, verbose=False, drop_exits=_de, drop_bn=_db2)
        if _de or _db2:
            print(f"[demo] 없앤 셈 친 것 — 출구 {_de or '—'} · 병목 {_db2 or '—'}")
        res = _d2
        tl = [t.model_dump() if hasattr(t, "model_dump") else t for t in _tl2]
        print(f"[demo] **v2 지표** — 그린 점 {_dots['n']}명 기준 · "
              f"개인 경로 {len(_site2.routes)}개 · v_th {a.v_th}")
    site = dict(meta["site_view"])
    if _de or _db2:
        # 도면에 그리는 출입구선·병목도 같이 뺀다. 없다고 친 출구의 통과선이
        # 그려져 있으면 "저기로도 나갈 수 있었네" 로 읽힌다.
        if _de:
            site["exits"] = [e for e in (site.get("exits") or [])
                             if e["id"] not in set(_de)]
        if _db2:
            site["bottlenecks"] = [b for b in (site.get("bottlenecks") or [])
                                   if b["id"] not in set(_db2)]

    cam_ids = sorted({c["cam_id"] for c in meta.get("cameras", [])})
    clips, desc = resolve_clips(cam_ids, label, a.package, a.scenario)
    if a.clip_root:
        # 비식별 사본으로 바꿔 끼운다. 원본으로 조용히 대체하면 비식별판에 원본 얼굴이
        # 섞이므로, 사본이 없으면 멈춘다.
        sub = {cid: a.clip_root / fp.parent.name / fp.name for cid, fp in clips.items()}
        miss = [str(p) for p in sub.values() if not p.is_file()]
        if miss:
            print("[demo] --clip-root 에 없는 영상:", *miss, sep="\n  "); return 1
        clips = sub
        desc += f" · 영상 출처 {a.clip_root}"
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
    LEFT_W = OW if a.no_grid else int(OW * 0.46)
    RIGHT_W = OW - LEFT_W
    PANE_H = int(OW * 0.44)
    BAR_H = int(OW * 0.135)
    OH = PANE_H + BAR_H

    mp = site.get("map") or {}
    m_per_px = mp.get("m_per_px") or (mp["scale_m"] / mp["scale_px"] if mp.get("scale_px") else 1)
    mapf = ROOT / "data/sites/default" / ("map.png" if floor == "default" else f"map_{floor}.png")
    mimg = cv2.imread(str(mapf))
    cx0 = cy0 = 0
    if a.no_grid:
        # 그리드가 빠지면 도면이 높이에 묶여 양옆이 빈다 — 도면 바깥 흰 여백을
        # 잘라 도면을 키운다. 출구·병목·경로가 잘리지 않게 그 좌표도 포함한다.
        ink = np.argwhere(cv2.cvtColor(mimg, cv2.COLOR_BGR2GRAY) < 200)
        if len(ink):
            ys, xs = ink[:, 0], ink[:, 1]
            pts = [p for e in (site.get("exits") or []) for p in (e.get("line") or [])]
            pts += [p for b in (site.get("bottlenecks") or []) for p in b["polygon"]]
            pts += [p for r in (site.get("routes") or []) for p in (r.get("points") or [])]
            x0_ = min([int(np.percentile(xs, 0.2))] + [int(p[0]) for p in pts])
            x1_ = max([int(np.percentile(xs, 99.8))] + [int(p[0]) for p in pts])
            y0b = min([int(np.percentile(ys, 0.2))] + [int(p[1]) for p in pts])
            y1b = max([int(np.percentile(ys, 99.8))] + [int(p[1]) for p in pts])
            mg = 30
            cx0, cy0 = max(0, x0_ - mg), max(0, y0b - mg)
            cx1 = min(mimg.shape[1], x1_ + mg); cy1 = min(mimg.shape[0], y1b + mg)
            mimg = mimg[cy0:cy1, cx0:cx1]
    base, msc, mox, moy = fit(mimg, LEFT_W, PANE_H)
    mox -= int(cx0 * msc); moy -= int(cy0 * msc)

    def MP(x, y):
        return int(x * msc + mox), int(y * msc + moy)

    # 맵 바탕 — **도형만**. 글자·구역·경로 없음.
    left0 = np.full((PANE_H, LEFT_W, 3), BG[0], np.uint8)
    left0[:] = base
    for bn in (site.get("bottlenecks") or []):          # CBS 병목
        # scale_shape 는 **크기와 이동을 같이** 적용한다(:8940 편집본).
        # scaled() 만 쓰면 크기만 반영되고 옮긴 자리가 무시된다.
        # **그림 = 지표가 재는 영역.** 확대·이동(:8940 편집본, 기본 반경 1.5배·b1/b4 2배)은
        # v2 지표가 그 영역으로 다시 재기 때문에 쓰는 것이다. v1(원 관측)은 site.json 의
        # 원래 병목으로 재므로 그대로 그린다 — 키워 그리면 웹 화면·지표와 영역이 어긋난다
        # (2026-10-24 CJ 영상: 반경 1.5~2배로 그려져 실제보다 2.25~4배 넓게 보였다).
        _bn = (dbn.scale_shape(dict(bn, polygon=[list(p) for p in bn["polygon"]]))
               if a.metrics == "v2" else dict(bn))
        poly = np.array([MP(*p) for p in _bn["polygon"]], np.int32)
        ov = left0.copy(); cv2.fillPoly(ov, [poly], (60, 90, 255))
        cv2.addWeighted(ov, 0.16, left0, 0.84, 0, left0)
        cv2.polylines(left0, [poly], True, (60, 90, 255), 2)
    for e in (site.get("exits") or []):                  # 출입구
        ln = e.get("line") or []
        if len(ln) >= 2:
            cv2.line(left0, MP(*ln[0]), MP(*ln[1]), (255, 190, 60), 6, cv2.LINE_AA)
    dobs.draw(left0, obstacles, MP)                      # 장애물 표시(해치)
    # 범례 — **화면에 나오는 것 전부**를 적는다. 색·도형만 보고 무엇인지 알 수
    # 없으면 발표에서 매번 말로 설명해야 한다.
    #   kind: dot(점) / line(선) / box(면) / hatch(해치)
    _dot = session_color(a.session)
    # 항목: (이름, 색, 모양). 설명문을 한 줄 더 붙여 봤는데 그 줄이 제일 길어서
    # 범례 가로가 도면의 절반을 먹었다 — 이름만 남기고, 무엇을 재는지는 이름에 담는다.
    # 자취·피난경로는 **범례에서 뺀다**. 선이 셋이나 되는데 셋 다 비슷한 파랑이라
    # 오히려 헷갈렸고, 둘은 그림만 봐도 읽힌다(점 뒤를 따라오는 선 / 도입부에만
    # 잠깐 깔리는 선). 범례는 모양만 보고는 모르는 것만 남긴다.
    # 점 색은 판마다 다르다 — 히트 판은 흰색으로 그린다.
    leg: list[tuple] = [("재실자", (255, 255, 255) if a.map == "heat" else _dot,
                         "dot")]
    if site.get("exits"):
        leg.append(("출입구 통과선", (255, 190, 60), "line"))
    if site.get("bottlenecks"):
        leg.append(("밀집 측정 구역", (60, 90, 255), "sector"))
    leg += [(nm, bgr, "hatch") for nm, bgr in dobs.legend(obstacles)]
    if leg:
        # 도면이 흰 바탕이라 글자를 그냥 얹으면 안 읽힌다 — 어두운 판을 깐다.
        # 위치는 **우측 상단** — 사람이 움직이는 곳(좌·하단)을 가리지 않는다.
        FS = 18                           # 본문 글자 크기
        rowh, padx, gw = 30, 13, 28
        wmax = max(text_w(nm, FS, True) for nm, _c, _k in leg)
        # +12 는 여유. text_w 는 글자 폭 합이라 한글 자간·괄호에서 조금 모자란다.
        boxw = int(padx * 2 + gw + 10 + wmax + 12)
        h = 12 + len(leg) * rowh
        x0, y0 = LEFT_W - 14 - boxw, 14
        ov = left0.copy()
        cv2.rectangle(ov, (x0, y0), (x0 + boxw, y0 + h), (26, 26, 30), -1)
        cv2.addWeighted(ov, 0.84, left0, 0.16, 0, left0)
        cv2.rectangle(left0, (x0, y0), (x0 + boxw, y0 + h), (78, 78, 88), 1)
        for i, (nm, bgr, kind) in enumerate(leg):
            cy = y0 + 6 + i * rowh + rowh // 2
            sx = x0 + padx
            if kind == "dot":
                cv2.circle(left0, (sx + gw // 2, cy), 6, bgr, -1, cv2.LINE_AA)
            elif kind == "line":
                cv2.line(left0, (sx, cy), (sx + gw, cy), bgr, 4, cv2.LINE_AA)
            elif kind == "sector":
                # **반원**으로 그린다 — 도면 위 밀집 구역이 부채꼴이라 모양으로
                # 바로 이어진다(네모로 그리면 구역·존과 구분이 안 된다).
                c = (sx + gw // 2, cy + 6)
                ax = (gw // 2 + 1, gw // 2 + 1)
                o2 = left0.copy()
                cv2.ellipse(o2, c, ax, 0, 180, 360, bgr, -1, cv2.LINE_AA)
                cv2.addWeighted(o2, 0.38, left0, 0.62, 0, left0)
                cv2.ellipse(left0, c, ax, 0, 180, 360, bgr, 2, cv2.LINE_AA)
                cv2.line(left0, (c[0] - ax[0], c[1]), (c[0] + ax[0], c[1]),
                         bgr, 2, cv2.LINE_AA)
            else:                                   # hatch
                cv2.rectangle(left0, (sx, cy - 7), (sx + gw, cy + 7),
                              dobs._soft(bgr), 1)
                cv2.line(left0, (sx + 2, cy + 6), (sx + gw - 2, cy - 6),
                         dobs._soft(bgr), 1, cv2.LINE_AA)
            put(left0, nm, (sx + gw + 10, cy - FS + 4), FS, (232, 232, 240), True)
        print(f"[demo] 범례 {boxw}x{h}px · {len(leg)}줄")

    # 히트맵 준비 — 반경은 **실거리**로 잡는다(화면 px 로 고정하면 도면 축척마다
    # 같은 밀집이 다른 그림이 된다).
    HEAT = a.map == "heat"
    if HEAT:
        heat_r = max(6, int(HEAT_R_M / m_per_px * msc * HEAT_SCALE))
        blob = heat_blob(heat_r)
        lut_rgb, lut_a = heat_lut()
        # heat_legend 가 까는 판은 색띠까지만 덮어서, 아래 "낮음/높음" 글자가
        # 흰 도면 위로 나가 안 읽힌다. 먼저 넉넉한 받침을 깔고 그 위에 그린다.
        cv2.rectangle(left0, (14, 14), (14 + 150 + 16, 80), (28, 28, 32), -1)
        heat_legend(left0, 22, 38)      # 좌상단 — 우상단은 도형 범례가 쓴다
        print(f"[demo] 히트맵 판 — 반경 {HEAT_R_M}m = {heat_r}px(누적버퍼)")

    DOT = session_color(a.session)
    outdir = ROOT / a.out
    outdir.mkdir(parents=True, exist_ok=True)
    stem = (f"{a.metric}_{label.split()[0]}"
            + (f"_{a.tag}" if a.tag else ("_v2" if a.metrics == "v2" else ""))
            + ("_heat" if a.map == "heat" else ""))
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
        ideal = dpaths.load(a.session, t_end=dur, obstacles=obstacles,
                            m_per_px=m_per_px)
        if ideal is None:
            print("그린 동선이 없습니다 — tools/path_editor.py (:8930) 에서 먼저 그리세요")
            return 1
        print(f"[demo] {dpaths.summary(ideal)}")
        # **그리지 않은 사람은 실측 그대로** 얹는다. 12 SEI 는 10명이 다 나간 뒤
        # 75초쯤 뒤늦게 한둘이 더 나온다 — 그건 손으로 그릴 게 아니라 있는 그대로
        # 보여 주는 게 맞다.
        _ex = EXTRA_RAW.get(a.session) or {}
        _raw = collections.defaultdict(list)
        for fr in frames:
            for o in fr["objects"]:
                _raw[o["gid"]].append((fr["ts"] - t0, o["x"], o["y"]))
        _want = list(_ex.get("gids") or [])
        _bef = _ex.get("before")
        if _bef is not None:
            # 계단에서 나오기 전은 **손대지 않고 있는 그대로** 보여 준다.
            _want += [g for g, v in _raw.items()
                      if v and min(q[0] for q in v) < _bef and g not in _want]
        for gid in _want:
            pts = sorted(_raw.get(gid) or [])
            if len(pts) < 2:
                continue
            dt_ = ideal["dt"]
            ta, tb = pts[0][0], pts[-1][0]
            g = np.arange(ta, tb + dt_ * 0.5, dt_)
            xs = np.interp(g, [q[0] for q in pts], [q[1] for q in pts])
            ys = np.interp(g, [q[0] for q in pts], [q[2] for q in pts])
            ideal["people"].append({
                "id": gid.split(":")[-1] and f"R{len(ideal['people']) + 1:02d}",
                "t0": float(ta), "exit_at": None,
                "xy": [[round(float(x), 1), round(float(y), 1)] for x, y in zip(xs, ys)]})
            if gid in (_ex.get("gids") or []):
                print(f"        + 실측 그대로 {gid} ({ta:.1f}~{tb:.1f}s)")
        if _want:
            print(f"        + 계단 구간 실측 {len(_want)}개 그대로")
        ideal["n"] = len(ideal["people"])
    # 출구 통과 카운트도 **그린 점 기준**으로 센다. 실측 타임라인을 그대로 쓰면
    # 점은 아직 문 앞에 있는데 카운트만 올라간다(실측 44~49s, 그린 건 46~56s).
    door_ev = None
    if ideal:
        exl = [(e.get("id"), e.get("line")) for e in (site.get("exits") or [])
               if e.get("line")]

        def _sd(q, a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            u = max(0.0, min(1.0, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy)
                             / ((dx * dx + dy * dy) or 1.0)))
            return math.hypot(q[0] - (a[0] + u * dx), q[1] - (a[1] + u * dy))

        door_ev = []
        for pp in ideal["people"]:
            if pp.get("exit_at") is None:
                continue          # 실측 그대로 얹은 트랙 — 통과 시각이 없다
            lx, ly = pp["xy"][-1]
            eid = (min(exl, key=lambda e: _sd((lx, ly), e[1][0], e[1][1]))[0]
                   if exl else "exit-0")
            door_ev.append((float(pp["exit_at"]), eid))
        door_ev.sort()

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

    # 병목 면적(m²) — CBS × 면적 = **정원 초과 인·초**. 그걸 재실 인원으로 나누면
    # "1인당 몇 초 밀렸나"가 된다. 밀도·적분 대신 이걸 근거로 붙인다.
    # 훈련 참여 인원 — 시점별 재실이 아니라 **전체 인원**으로 나눈다
    _n_people = (ideal["n"] if ideal else
                 max((len(f["objects"]) for f in frames), default=10)) or 10
    _bn_area = {}
    for _b in (site.get("bottlenecks") or []):
        _pg = _b.get("polygon") or []
        if len(_pg) >= 3:
            _a = abs(sum(_pg[i][0] * _pg[(i + 1) % len(_pg)][1]
                         - _pg[(i + 1) % len(_pg)][0] * _pg[i][1]
                         for i in range(len(_pg)))) / 2.0
            _bn_area[_b["id"]] = _a * (m_per_px or 0) ** 2

    # 타임라인에는 병목 **밀도**만 들어 있다(bottleneck_density). 누적 CBS 를
    # 1인당 초로 환산하려면 면적이 필요한데, 병목마다 면적이 달라 총합만으로는
    # 못 나눈다 — 그래서 면적 가중 평균을 쓴다. 병목 하나면 정확히 같은 값이다.
    _area_avg = ((sum(_bn_area.values()) / len(_bn_area)) if _bn_area else 0.0)
    _peak_seen = {"v": 0.0}

    def _cbs_note(tp) -> str:
        """CBS 아래 작은 글씨 — 사람이 읽을 수 있는 근거.

        값 자체는 **원값**이다(0.0 이 좋음). 100 − CBS 로 뒤집어 보여 준 적이
        있는데, 0 기준으로 읽는 지표를 100 기준으로 바꾸면 다른 자료와 어긋난다.
        """
        bd = tp.get("bottleneck_density") or {}
        cur = max((v or 0.0 for v in bd.values()), default=0.0)
        _peak_seen["v"] = max(_peak_seen["v"], cur)
        c = tp.get("cbs_total") or 0.0
        peak = _peak_seen["v"]
        if c <= 0:
            return f"임계 초과 없음 · 최대 {peak:.1f}명/m²"
        return (f"1인당 {c * _area_avg / max(1, _n_people):.1f}초 지체 "
                f"· 최대 {peak:.1f}명/m²")

    # 지표 이름은 **한글이 먼저**다. 발표 자리에서 IDR·EPFI 를 아는 사람은 없다.
    # 약어는 근거를 찾아볼 수 있게 작게 곁들인다.
    KO = {"IDR": "반응 신속도", "SEI": "출구 활용도",
          "CBS": "대피 혼잡도", "EPFI": "경로 준수도"}

    def bar_panel(t, tp, obs, exits_now):
        bar = np.full((BAR_H, OW, 3), 22, np.uint8)
        el = max(0.0, t - t0)
        m, s = int(el // 60), int(el % 60)
        put(bar, "경보 후 경과 시간", (26, 10), 22, (175, 175, 180))
        put(bar, f"{m}:{s:02d}", (22, 40), 72, (255, 255, 255), True)
        put(bar, "분:초", (26 + int(text_w(f"{m}:{s:02d}", 72, True)) + 10, 82),
            18, (145, 145, 150))
        iv, idone, ifirst = idr_at(t)
        x = int(OW * 0.15)
        _ex_n = sum(exits_now.values()) if exits_now else 0
        for name, val, sub in (
                ("SEI", f1(tp.get("sei")),
                 (f"출구 {len(exits_now)}곳 · 통과 {_ex_n}명" if exits_now else "—")),
                ("EPFI", f1(tp.get("epfi_avg")), f"재실 {len(obs)}명 기준"),
                ("CBS", f1(tp.get("cbs_total"), 2), _cbs_note(tp)),
                ("IDR", f1(iv, 2),
                 (f"개시 {ifirst:.0f}s · {idone}/{n_zones} 구역" if iv is not None
                  else f"미개시 · 0/{n_zones} 구역"))):
            main = (name == a.metric)
            # **넷 다 같은 크기·같은 색 규칙**으로 그린다. 주지표만 크고 나머지는
            # 회색이면 "무슨 글자인지" 가 안 읽힌다 — 주지표는 테두리로만 구분한다.
            col = ACCENT[name]
            ko = KO.get(name, name)
            put(bar, ko, (x, 10), 28, col, True)
            nw = int(text_w(ko, 28, True))
            put(bar, name, (x + nw + 8, 18), 17, (150, 150, 155))
            put(bar, val, (x, 44), 58, (245, 245, 248), True)
            if name == "IDR" and iv is not None:
                put(bar, "m/s", (x + int(text_w(val, 58, True)) + 8, 76),
                    18, (155, 155, 160))
            put(bar, sub, (x, 110), 16, (145, 145, 150))
            if main:                       # 값·설명까지 감싼다(숫자가 잘리지 않게)
                cv2.rectangle(bar, (x - 13, 2), (x + int(OW * 0.138), 134), col, 2)
            x += int(OW * 0.158)
        # 카운팅 — 따로
        cx = int(OW * 0.785)
        cv2.line(bar, (cx - 24, 12), (cx - 24, BAR_H - 14), (70, 70, 70), 1)
        put(bar, "카운팅", (cx, 10), 20, (170, 170, 175))
        # 재실 / 출구 통과 **두 줄**로. 예전엔 출구가 작아 눈에 안 들어왔다.
        put(bar, "재실", (cx, 44), 22, (170, 170, 175))
        put(bar, f"{len(obs)}명", (cx + 52, 36), 38, (245, 245, 248), True)
        _ex_tot = sum(exits_now.values()) if exits_now else 0
        put(bar, "출구 통과", (cx, 92), 22, (170, 170, 175))
        put(bar, f"{_ex_tot}명", (cx + 96, 84), 38, (255, 190, 60), True)
        if exits_now and len(exits_now) > 1:
            det = " · ".join(f"{k} {v}" for k, v in sorted(exits_now.items()))
            put(bar, det, (cx + 186, 96), 17, (150, 150, 155))
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
            right = np.full((PANE_H, max(RIGHT_W, 560), 3), 18, np.uint8)
            # 인트로는 **지표 한 가지**만 말한다. 각본 이름("문 앞 밀집 2회",
            # "1층 출구 나가기")은 그 영상이 어떻게 찍혔는지지 무엇을 보여주려는
            # 지가 아니다 — 발표에서는 지표가 먼저 읽혀야 한다.
            y0_ = int(PANE_H * 0.30)
            put(right, a.title or "AIHub", (40, y0_), 26, (150, 150, 158), True)
            if a.metric == "ALL":           # 4대 지표 통합 — 넷을 같은 크기로 나열
                put(right, "4대 지표", (40, y0_ + 44), 60, (255, 255, 255), True)
                for i, nm in enumerate(("IDR", "SEI", "CBS", "EPFI")):
                    yy = y0_ + 132 + i * 50
                    put(right, nm, (40, yy), 34, ACCENT[nm], True)
                    put(right, KO[nm], (40 + 120, yy + 2), 32, (235, 235, 240), True)
                cv2.line(right, (42, y0_ + 340), (42 + 150, y0_ + 340),
                         (200, 200, 205), 3, cv2.LINE_AA)
                put(right, "피난경로", (40, y0_ + 358), 26, DOT, True)
            else:
                put(right, a.metric, (40, y0_ + 44), 66, ACCENT[a.metric], True)
                put(right, KO.get(a.metric, a.metric), (40, y0_ + 124), 54,
                    (255, 255, 255), True)
                cv2.line(right, (42, y0_ + 206), (42 + 150, y0_ + 206),
                         ACCENT[a.metric], 3, cv2.LINE_AA)
                put(right, "재실자별 피난경로", (40, y0_ + 226), 26, DOT, True)
            if a.no_grid:
                # 그리드가 없으면 소개 글을 도면 왼쪽 위 어두운 판에 얹는다
                cwid = int(min(460, max(360, mox - 28)))     # 도면 왼쪽 빈 곳에 맞춘다
                card = right[max(0, y0_ - 40):min(PANE_H, y0_ + 400), :cwid]
                hh = min(card.shape[0], PANE_H - 28)
                roi = frame[14:14 + hh, 14:14 + cwid]
                frame[14:14 + hh, 14:14 + cwid] = cv2.addWeighted(card[:hh], 0.88, roi, 0.12, 0)
            else:
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
            n = 0 if a.no_grid else len(caps)
            ccols = max(1, int(np.ceil(np.sqrt(n)))); crows = max(1, int(np.ceil(n / ccols)))
            cw, ch = RIGHT_W // ccols, PANE_H // crows
            for i, cid in enumerate(caps if n else []):
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
            if door_ev is not None:
                ec = {}
                for et, eid in door_ev:
                    if t - t0 >= et:
                        ec[eid] = ec.get(eid, 0) + 1
            frame = np.full((OH, OW, 3), 20, np.uint8)
            frame[:PANE_H, :LEFT_W] = left
            if not a.no_grid:
                frame[:PANE_H, LEFT_W:] = right
                cv2.line(frame, (LEFT_W, 0), (LEFT_W, PANE_H), (60, 60, 60), 1)
            frame[PANE_H:] = bar_panel(t, tp, obs, ec)
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
