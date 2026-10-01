#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""층 라이브 **발표용** 시각화 — 좌 도면 / 우 2×2 카메라 / 하 4대지표(demo v3 판).

`floor_live_viz.py` 와 소스(라이브 RTSP + 서버 `/api/map/state`)는 같고, **화면이
다르다**. 저쪽은 진단용이라 구역·경로·라벨을 다 얹는데, 발표에서는 그게 전부
잡음이다. 여기서는 `tools/demo_viz.py` 의 v3 화면 규칙을 그대로 쓴다:

  · 4대지표는 **한글 이름이 먼저**, 넷 다 같은 크기 (IDR/EPFI 를 아는 사람은 없다)
  · 경보 후 경과 시계를 크게, 카운팅(재실·출구 통과)은 따로 패널
  · 도면에는 **병목·출입구만 도형으로**. 구역·글자는 뺀다
  · 우측은 **2×2 그리드** — 카메라가 셋이면 넷째 칸은 요약 카드
  · 범례를 우상단에 (색만 깔아 두면 매번 말로 설명해야 한다)

`--routes`/`--coverage` 를 주지 않으면 **피난경로·CCTV 커버리지는 그리지 않는다**
(기본 끔). 17F 는 상시 사무 공간이라 "각자 이 길로" 라는 그림이 맞지 않는다.

    python tools/floor_demo_viz.py --floor default --cams cam01 cam02 cam03 \
           --sec 180 --out results/floor_viz/17F_demo_2x2.mp4
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
import warnings
from collections import defaultdict, deque
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2                                     # noqa: E402
import numpy as np                             # noqa: E402

from tools.demo_viz import (                   # noqa: E402
    ACCENT, HEAT_R_M, HEAT_SCALE, cover, heat_blob, heat_legend, heat_lut,
    heat_overlay,
)
from tools.session_viz import _font, put, text_w   # noqa: E402

BASE = "http://localhost:8900"
# 지표 이름은 한글이 먼저 (demo_viz 와 같은 표)
KO = {"IDR": "반응 신속도", "SEI": "출구 활용도",
      "CBS": "대피 혼잡도", "EPFI": "경로 준수도"}
DOT = (110, 235, 130)          # 재실자 점 — 출입구(주황)·병목(파랑)과 안 겹치는 초록
EXIT_C = (255, 190, 60)
BN_C = (60, 90, 255)
TRAIL_SEC = 10.0


def api(path: str, body=None, timeout=30):
    req = urllib.request.Request(
        BASE + path,
        data=(json.dumps(body).encode() if body is not None else None),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def puts(img, items):
    """텍스트 여러 개를 **PIL 왕복 한 번**에 그린다.

    session_viz.put 은 호출마다 이미지를 통째로 PIL 로 바꿨다 되돌린다. 1920×259
    하단 바에 25번 부르면 프레임당 136ms 라 24fps(프레임당 41ms)를 못 맞춘다 —
    그러면 벽시계가 영상보다 앞서가고, 화면의 경과 시계가 실제보다 빨리 흐른다
    (실측 1.7배). 지표·시계가 붙는 패널은 전부 이걸로 그린다.

    items: (문자열, (x, y), px, BGR, bold) 목록
    """
    from PIL import Image, ImageDraw
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    for text, org, px, col, bold in items:
        d.text(org, text, font=_font(px, bold), fill=(col[2], col[1], col[0]))
    img[:] = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


class Grab(threading.Thread):
    """RTSP 1채널 **최신 프레임만** 유지 — 읽는 쪽이 느려도 밀리지 않게."""

    def __init__(self, url: str):
        super().__init__(daemon=True)
        self.url, self.frame, self.ok = url, None, False
        self._stop = False

    def run(self):
        # 디코더 스레드를 2 로 묶는다. 기본(auto)은 코어 수만큼 띄워서
        # 128코어 장비에서 채널당 128 스레드가 생긴다(실측: 서버가 멎었다).
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 2])
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        while not self._stop:
            ok, fr = cap.read()
            if not ok:
                time.sleep(0.2)
                cap.release()
                cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG,
                                       [cv2.CAP_PROP_N_THREADS, 2])
                continue
            self.frame, self.ok = fr, True
        cap.release()

    def stop(self):
        self._stop = True


class StateFeed(threading.Thread):
    """`/api/map/stream` (SSE) 를 받아 **스냅샷을 ts 와 함께 쌓아 둔다**.

    왜 폴링이 아니라 SSE 인가 — 5Hz 로 폴링하면 서버 스냅샷 주기와 어긋나
    같은 스냅샷을 두 번 받거나(실측 2/29회) 하나를 건너뛴다. 웹 UI 도 SSE 를 쓴다.

    왜 쌓아 두는가 — 그려야 매끄럽다. 분석은 5fps 인데 영상은 24fps 라, 받은
    좌표를 그대로 홀드하면 **초당 5번만 움직여 뚝뚝 끊긴다**. 앞뒤 스냅샷 사이를
    보간하려면 "다음 것" 이 필요하므로, 그리는 시각을 한 주기(LAG) 뒤로 물린다.
    """

    def __init__(self, floor: str, keep: int = 16):
        super().__init__(daemon=True)
        self.floor, self._stop = floor, False
        self.snaps: deque = deque(maxlen=keep)
        self.lock = threading.Lock()
        # 보간이 실제로 됐는지 — 못 하면 마지막 스냅샷을 쥐고 멈춘다(=끊김).
        # 예전에 lag 0.3s 로 두고 "보간했다" 고 했는데 실측 6.7% 만 됐다.
        self.n_interp = self.n_hold = 0

    def run(self):
        while not self._stop:
            try:
                req = urllib.request.Request(
                    f"{BASE}/api/map/stream?floor={self.floor}")
                with urllib.request.urlopen(req, timeout=30) as r:
                    for raw in r:
                        if self._stop:
                            return
                        line = raw.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue
                        d = json.loads(line[5:].strip())
                        with self.lock:
                            if self.snaps and self.snaps[-1]["ts"] == d["ts"]:
                                continue
                            self.snaps.append(d)
            except Exception:                         # noqa: BLE001
                time.sleep(0.5)                       # 끊기면 다시 붙는다

    def latest(self):
        with self.lock:
            return self.snaps[-1] if self.snaps else {}

    def at(self, t: float):
        """t 시점의 화면 상태 — 앞뒤 스냅샷을 **gid 로 짝지어 보간**.

        선형이 아니라 **Catmull-Rom**(비균등 시간 Hermite)이다. 선형으로 이으면
        스냅샷마다(0.2초) 진행 방향이 각지게 꺾여, 매 프레임 그려도 여전히
        덜컹거린다. 앞 1개·뒤 2개 스냅샷의 유한차분으로 접선을 잡아 속도까지
        이어 준다 — 그래서 `--lag` 이 분석 주기의 두 배는 돼야 한다.

        좌표를 지어내는 게 아니라 표출만 매끄럽게 하는 것이고, **지표는 서버가
        낸 값 그대로** 쓴다.
        """
        with self.lock:
            snaps = list(self.snaps)
        if not snaps:
            return [], {}
        i = None
        for k in range(len(snaps) - 1):
            if snaps[k]["ts"] <= t <= snaps[k + 1]["ts"]:
                i = k
                break
        if i is None:                       # 아직 다음 게 안 왔다 — 마지막 걸 쥔다
            self.n_hold += 1
            a = snaps[-1]
            return list(a.get("objects") or []), a
        self.n_interp += 1
        s1, s2 = snaps[i], snaps[i + 1]
        s0 = snaps[i - 1] if i - 1 >= 0 else None
        s3 = snaps[i + 2] if i + 2 < len(snaps) else None
        h = (s2["ts"] - s1["ts"]) or 1.0
        u = max(0.0, min(1.0, (t - s1["ts"]) / h))
        u2, u3 = u * u, u * u * u
        h00, h10 = 2 * u3 - 3 * u2 + 1, u3 - 2 * u2 + u
        h01, h11 = -2 * u3 + 3 * u2, u3 - u2
        by = lambda sn: {o["gid"]: o for o in (sn.get("objects") or [])} if sn else {}
        m0, m2, m3 = by(s0), by(s2), by(s3)
        out = []
        for o in (s1.get("objects") or []):
            g = o["gid"]
            q2 = m2.get(g)
            if q2 is None:                  # 다음 스냅샷에 없다 — 있는 자리에 둔다
                out.append(o)
                continue
            q0, q3 = m0.get(g), m3.get(g)
            pos = []
            for ax in ("x", "y"):
                p1, p2 = o[ax], q2[ax]
                # 접선 — 시간 간격이 흔들려도 맞도록 실제 ts 로 나눈다
                t1 = ((p2 - q0[ax]) / (s2["ts"] - s0["ts"]) * h
                      if q0 is not None and s0 is not None else p2 - p1)
                t2 = ((q3[ax] - p1) / (s3["ts"] - s1["ts"]) * h
                      if q3 is not None and s3 is not None else p2 - p1)
                pos.append(h00 * p1 + h10 * t1 + h01 * p2 + h11 * t2)
            out.append({**o, "x": pos[0], "y": pos[1]})
        return out, s1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--floor", default="default")
    ap.add_argument("--cams", nargs="+", required=True)
    ap.add_argument("--sec", type=float, default=180.0)
    ap.add_argument("--fps", type=float, default=24.0)
    ap.add_argument("--lag", type=float, default=0.70,
                    help="보간을 위해 그리는 시각을 뒤로 무는 폭(초). 스냅샷 ts 가 "
                         "벽시계보다 0.17s(최대 0.32s) 뒤에 있고 Catmull-Rom 은 뒤로 "
                         "2개가 더 필요하다 — 실측 보간 성립률 0.3s:7%% · 0.5s:88%% · "
                         "0.7s:95%%")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--out", default="results/floor_viz/live_demo.mp4")
    ap.add_argument("--title", default="")
    ap.add_argument("--metric", default="",
                    help="주인공으로 테두리를 두를 지표 (비우면 넷 다 동등)")
    ap.add_argument("--intro", type=float, default=3.0, help="제목 화면 길이(초)")
    ap.add_argument("--map", default="dots", choices=("dots", "heat"),
                    help="2D 맵 표출 — dots(점+자취) / heat(밀도 히트맵)")
    ap.add_argument("--pad", type=float, default=0.12, help="줌 여백 비율")
    ap.add_argument("--loop-track", default="",
                    help="tools/demo_loopfold.py 가 만든 복원 궤적 json. 주면 좌측 "
                         "점을 **원 관측 대신 그것으로** 그린다 — id 가 사람당 하나로 "
                         "유지되고 끊김·제자리 오검출이 없다. 지표는 원 관측 그대로다")
    ap.add_argument("--sync-loop", action="store_true",
                    help="원본 클립이 되감기는 순간을 기다렸다 시작한다. 17F RTSP 는 "
                         "15.04초짜리를 무한 루프해서, 15초마다 화면 속 사람이 전부 "
                         "순간이동한다(2D 맵의 점이 튀는 주원인) — 한 바퀴 안에 "
                         "담으면 그 지점을 피한다(--sec 14 이하)")
    ap.add_argument("--routes", action="store_true",
                    help="피난경로도 그린다 (기본: 안 그림)")
    ap.add_argument("--coverage", action="store_true",
                    help="CCTV 커버리지(대응점 영역)도 그린다 (기본: 안 그림)")
    ap.add_argument("--loop-shift", type=float, default=0.0,
                    help="복원 궤적의 위상을 이만큼 민다(초). 점이 영상보다 "
                         "앞/뒤로 밀려 보이면 조정")
    ap.add_argument("--session", action="store_true", default=True)
    ap.add_argument("--no-session", dest="session", action="store_false")
    a = ap.parse_args()

    _site = api(f"/api/site?floor={a.floor}")
    # /api/site 최상위는 legacy 전역값이다 — 층의 진짜 도면·요소는 floors[] 안.
    site = next((f for f in (_site.get("floors") or []) if f.get("id") == a.floor), None)
    if site is None:
        raise SystemExit(f"층 없음: {a.floor} — 있는 층 "
                         + str([f.get("id") for f in (_site.get("floors") or [])]))
    fname = site.get("name") or a.floor
    _cl = api("/api/cameras")
    cams = {c["cam_id"]: c for c in (_cl["cameras"] if isinstance(_cl, dict) else _cl)}
    picked = [cams[c] for c in a.cams if c in cams]
    if len(picked) != len(a.cams):
        raise SystemExit(f"카메라 없음: {set(a.cams) - set(cams)}")

    # ── 줌 영역 — 지정 카메라 대응점을 감싸는 정사각 + 여백
    pts = []
    for c in picked:
        pts += [tuple(p) for p in ((c.get("mapping") or {}).get("map_pts") or [])]
    if not pts:
        raise SystemExit("대응점(map_pts) 없는 카메라 — ② 매핑 먼저")
    P = np.array(pts, np.float64)
    x0, y0 = P.min(axis=0)
    x1, y1 = P.max(axis=0)
    mw, mh = x1 - x0, y1 - y0
    x0 -= mw * a.pad; x1 += mw * a.pad
    y0 -= mh * a.pad; y1 += mh * a.pad
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    side = max(x1 - x0, y1 - y0) / 2
    x0, x1, y0, y1 = cx - side, cx + side, cy - side, cy + side

    mapf = ROOT / "data/sites/default" / ((site.get("map") or {}).get("image") or "map.png")
    base_img = cv2.imread(str(mapf))
    if base_img is None:
        raise SystemExit(f"도면 이미지 없음: {mapf}")
    H, W = base_img.shape[:2]
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(W, int(x1)), min(H, int(y1))
    crop = base_img[y0:y1, x0:x1].copy()

    mp = site.get("map") or {}
    m_per_px = mp.get("m_per_px")
    if not m_per_px:
        sc = mp.get("scale") or {}
        if sc.get("p1") and sc.get("p2") and sc.get("meters"):
            m_per_px = sc["meters"] / max(1e-6, float(np.hypot(
                sc["p2"][0] - sc["p1"][0], sc["p2"][1] - sc["p1"][1])))
    m_per_px = m_per_px or 0.0

    # ── 레이아웃 (demo_viz 와 같은 비율)
    OW = a.width
    LEFT_W = int(OW * 0.46)
    RIGHT_W = OW - LEFT_W
    PANE_H = int(OW * 0.44)
    BAR_H = int(OW * 0.135)
    OH = PANE_H + BAR_H

    s = min(LEFT_W / crop.shape[1], PANE_H / crop.shape[0])
    mm = cv2.resize(crop, (max(1, int(crop.shape[1] * s)), max(1, int(crop.shape[0] * s))))
    mox, moy = (LEFT_W - mm.shape[1]) // 2, (PANE_H - mm.shape[0]) // 2

    def MP(x, y):
        return int((x - x0) * s + mox), int((y - y0) * s + moy)

    # 사람은 **프레임당 0.9~1.8px** 움직인다(1.2 m/s · 화면 1px = 3.8cm · 24fps).
    # 정수 픽셀로 반올림하면 그 이동이 계단으로 양자화돼, 보간을 해도 덜컹거린다
    # — 어떤 프레임은 1px 튀고 어떤 프레임은 그대로다. cv2 의 shift(고정소수점)로
    # 1/16px 단위로 그린다. shift 는 중심과 반지름에 **함께** 적용된다.
    SH = 4
    K = 1 << SH

    def MPQ(x, y):
        """서브픽셀 좌표 — cv2 shift=SH 로 넘길 고정소수점 정수."""
        return (int(round(((x - x0) * s + mox) * K)),
                int(round(((y - y0) * s + moy) * K)))

    # 맵 바탕 — **도형만**. 구역·글자는 없다.
    left0 = np.full((PANE_H, LEFT_W, 3), 16, np.uint8)
    left0[moy:moy + mm.shape[0], mox:mox + mm.shape[1]] = mm
    if a.coverage:                       # 기본은 안 그린다
        for c in picked:
            q = [MP(*p) for p in ((c.get("mapping") or {}).get("map_pts") or [])]
            if len(q) >= 3:
                cv2.polylines(left0, [np.array(q, np.int32)], True, (150, 150, 150), 1)
    if a.routes:                         # 기본은 안 그린다
        for r in (site.get("routes") or []):
            q = [MP(*p) for p in (r.get("points") or [])]
            for i in range(len(q) - 1):
                cv2.line(left0, q[i], q[i + 1], (250, 165, 96), 2, cv2.LINE_AA)
    bn_area = {}
    for bn in (site.get("bottlenecks") or []):
        pg = bn.get("polygon") or []
        if len(pg) < 3:
            continue
        poly = np.array([MP(*p) for p in pg], np.int32)
        ov = left0.copy(); cv2.fillPoly(ov, [poly], BN_C)
        cv2.addWeighted(ov, 0.16, left0, 0.84, 0, left0)
        cv2.polylines(left0, [poly], True, BN_C, 2)
        ar = abs(sum(pg[i][0] * pg[(i + 1) % len(pg)][1]
                     - pg[(i + 1) % len(pg)][0] * pg[i][1]
                     for i in range(len(pg)))) / 2.0
        bn_area[bn["id"]] = ar * (m_per_px ** 2)
    for e in (site.get("exits") or []):
        ln = e.get("line") or []
        if len(ln) >= 2:
            cv2.line(left0, MP(*ln[0]), MP(*ln[1]), EXIT_C, 6, cv2.LINE_AA)

    # ── 범례 (우상단) — 화면에 나오는 것 전부
    leg = ([("재실자", (255, 255, 255), "dot")] if a.map == "heat"
           else [("재실자", DOT, "dot"), ("지나온 자취", DOT, "line")])
    if site.get("exits"):
        leg.append(("출입구 통과선", EXIT_C, "line"))
    if site.get("bottlenecks"):
        leg.append(("병목 구역", BN_C, "box"))
    if a.routes and site.get("routes"):
        leg.append(("권장 피난경로", (250, 165, 96), "line"))
    rowh, padx = 19, 9
    wmax = max(text_w(nm, 12, True) for nm, _c, _k in leg)
    boxw = int(padx * 2 + 24 + wmax)
    hh = 8 + len(leg) * rowh
    lx, ly = LEFT_W - 12 - boxw, 12
    ov = left0.copy()
    cv2.rectangle(ov, (lx, ly), (lx + boxw, ly + hh), (26, 26, 30), -1)
    cv2.addWeighted(ov, 0.82, left0, 0.18, 0, left0)
    cv2.rectangle(left0, (lx, ly), (lx + boxw, ly + hh), (70, 70, 78), 1)
    for i, (nm, bgr, kind) in enumerate(leg):
        yy = ly + 5 + i * rowh + 8
        sx = lx + padx
        if kind == "dot":
            cv2.circle(left0, (sx + 7, yy), 4, bgr, -1)
        elif kind == "line":
            cv2.line(left0, (sx, yy), (sx + 15, yy), bgr, 3, cv2.LINE_AA)
        else:
            cv2.rectangle(left0, (sx, yy - 5), (sx + 15, yy + 5), bgr, -1)
        put(left0, nm, (sx + 21, yy - 8), 12, (228, 228, 236), True)

    # ── 히트맵 판 준비 — 반경은 **실거리**로 잡는다(화면 px 로 고정하면 도면
    # 축척마다 같은 밀집이 다른 그림이 된다). 색·반경은 웹 화면(heatmap.js)과 같다.
    HEAT = a.map == "heat"
    blob = lut_rgb = lut_a = None
    if HEAT:
        heat_r = max(6, int(HEAT_R_M / max(1e-9, m_per_px) * s * HEAT_SCALE))
        blob = heat_blob(heat_r)
        lut_rgb, lut_a = heat_lut()
        # heat_legend 가 까는 판은 색띠까지만 덮어서, 아래 "낮음/높음" 글자가
        # 흰 도면 위로 나가 안 읽힌다. 먼저 넉넉한 받침을 깔고 그 위에 그린다.
        cv2.rectangle(left0, (14, 14), (14 + 150 + 16, 80), (28, 28, 32), -1)
        heat_legend(left0, 22, 38)          # 좌상단 — 우상단은 도형 범례가 쓴다
        print(f"[demo] 히트맵 판 — 반경 {HEAT_R_M}m = {heat_r}px")

    out_path = ROOT / a.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.mp4")
    vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), a.fps, (OW, OH))

    grabs = {c["cam_id"]: Grab(c["rtsp"]) for c in picked}
    for g in grabs.values():
        g.start()
    print("[demo] RTSP 연결 대기…")
    tw = time.time()
    while time.time() - tw < 15 and not all(g.ok for g in grabs.values()):
        time.sleep(0.5)
    print("[demo] 연결:", {k: v.ok for k, v in grabs.items()})

    sess, t_alarm = None, time.time()
    if a.session:
        try:
            sess = api(f"/api/session/start?floor={a.floor}",
                       {"origin": [(x0 + x1) / 2, (y0 + y1) / 2]})
            print(f"[demo] 경보 세션 시작 — {sess['session_id']}")
        except Exception as e:                       # noqa: BLE001
            print(f"[demo] 세션 시작 실패(지표 없이 진행): {e}")

    f1 = lambda v, d=1: "—" if v is None else f"{v:.{d}f}"     # noqa: E731
    peak = {"v": 0.0}
    area_avg = (sum(bn_area.values()) / len(bn_area)) if bn_area else 0.0
    ex_base: dict[str, int] = {}

    def cbs_note(ss, bns):
        cur = max((b.get("density") or 0.0 for b in bns), default=0.0)
        peak["v"] = max(peak["v"], cur)
        c = ss.get("cbs_total") or 0.0
        if c <= 0:
            return f"임계 초과 없음 · 최대 {peak['v']:.2f}명/m²"
        return f"누적 {c:.1f} · 최대 {peak['v']:.2f}명/m²"

    def bar_panel(el, ss, objs, bns, ex_now):
        bar = np.full((BAR_H, OW, 3), 22, np.uint8)
        T = []
        m, sec = int(el // 60), int(el % 60)
        clk = f"{m}:{sec:02d}"
        T += [("경보 후 경과 시간", (26, 10), 22, (175, 175, 180), False),
              (clk, (22, 40), 72, (255, 255, 255), True),
              ("분:초", (26 + int(text_w(clk, 72, True)) + 10, 82), 18,
               (145, 145, 150), False)]
        zms = ss.get("zone_metrics") or []
        n_zones = ss.get("zones_total") or len(zms)
        done = [z for z in zms if z.get("idr") is not None
                and z.get("evacuation_start_at") is not None]
        iv = (sum(z["idr"] for z in done) / len(done)) if done else None
        first = (min(z["evacuation_start_at"] for z in done) - float(
            ss.get("alarm_ts") or t_alarm)) if done else None
        ex_tot = sum(ex_now.values())
        x = int(OW * 0.15)
        for name, val, sub in (
                ("SEI", f1(ss.get("sei")),
                 (f"출구 {len(ex_now)}곳 · 통과 {ex_tot}명" if ex_now else "—")),
                ("EPFI", f1(ss.get("epfi_avg")), f"재실 {len(objs)}명 기준"),
                ("CBS", f1(ss.get("cbs_total"), 2), cbs_note(ss, bns)),
                ("IDR", f1(iv, 2),
                 (f"개시 {first:.0f}s · {len(done)}/{n_zones} 구역" if iv is not None
                  else f"미개시 · 0/{n_zones} 구역"))):
            col = ACCENT[name]
            ko = KO[name]
            T += [(ko, (x, 10), 28, col, True),
                  (name, (x + int(text_w(ko, 28, True)) + 8, 18), 17,
                   (150, 150, 155), False),
                  (val, (x, 44), 58, (245, 245, 248), True),
                  (sub, (x, 110), 16, (145, 145, 150), False)]
            if name == "IDR" and iv is not None:
                T.append(("m/s", (x + int(text_w(val, 58, True)) + 8, 76), 18,
                          (155, 155, 160), False))
            if name == a.metric:
                cv2.rectangle(bar, (x - 13, 2), (x + int(OW * 0.138), 134), col, 2)
            x += int(OW * 0.158)
        cxx = int(OW * 0.785)
        cv2.line(bar, (cxx - 24, 12), (cxx - 24, BAR_H - 14), (70, 70, 70), 1)
        _et = f"{ex_tot}명"
        T += [("카운팅", (cxx, 10), 20, (170, 170, 175), False),
              ("재실", (cxx, 44), 22, (170, 170, 175), False),
              (f"{len(objs)}명", (cxx + 52, 36), 38, (245, 245, 248), True),
              ("출구 통과", (cxx, 92), 22, (170, 170, 175), False),
              (_et, (cxx + 96, 84), 38, EXIT_C, True)]
        if len(ex_now) > 1:
            # 숫자 폭만큼 밀어 놓는다 — 고정 x 로 두면 세 자리가 되는 순간 겹친다
            T.append((" · ".join(f"{k} {v}" for k, v in sorted(ex_now.items())),
                      (cxx + 96 + int(text_w(_et, 38, True)) + 16, 96), 17,
                      (150, 150, 155), False))
        puts(bar, T)
        return bar

    def grid_2x2(objs, spd):
        """우측 — **2×2 고정**. 카메라가 셋이면 넷째 칸은 요약 카드."""
        right = np.full((PANE_H, RIGHT_W, 3), 18, np.uint8)
        cw, ch = RIGHT_W // 2, PANE_H // 2
        for i in range(4):
            gy, gx = divmod(i, 2)
            cell = np.full((ch, cw, 3), 24, np.uint8)
            T = []
            if i < len(a.cams):
                cid = a.cams[i]
                fr = grabs[cid].frame
                if fr is not None:
                    cell = cover(fr, cw, ch)
                else:
                    T.append((f"{cid} — 연결 없음", (18, ch // 2), 22,
                              (120, 120, 120), False))
                n = sum(1 for o in objs if o.get("cam_id") == cid)
                ovc = cell.copy()
                cv2.rectangle(ovc, (0, ch - 40), (cw, ch), (0, 0, 0), -1)
                cv2.addWeighted(ovc, 0.45, cell, 0.55, 0, cell)
                T += [(cid, (12, ch - 33), 22, (235, 235, 240), True),
                      (f"{n}명", (cw - 74, ch - 33), 22, DOT, True)]
            else:                       # 넷째 칸 — 요약
                nn = f"{len(objs)}"
                T += [(fname, (22, 26), 30, (235, 235, 240), True),
                      ("실시간 추적", (22, 66), 20, (150, 150, 155), False),
                      (nn, (22, 104), 62, DOT, True),
                      ("명", (22 + int(text_w(nn, 62, True)) + 10, 140), 22,
                       (170, 170, 175), False),
                      ("평균 속도", (22, 190), 20, (150, 150, 155), False),
                      ((f"{np.mean(spd):.2f} m/s" if spd else "—"), (22, 218), 34,
                       (235, 235, 240), True),
                      (" · ".join(a.cams), (22, ch - 34), 18, (130, 130, 135), False)]
            puts(cell, T)
            cv2.rectangle(cell, (0, 0), (cw - 1, ch - 1), (45, 45, 45), 1)
            right[gy * ch:(gy + 1) * ch, gx * cw:(gx + 1) * cw] = cell
        return right

    # ── 복원 궤적(선택) — 루프를 여러 바퀴 겹쳐 사람별로 되살린 것
    LOOP = None
    if a.loop_track:
        LOOP = json.loads((ROOT / a.loop_track).read_text(encoding="utf-8"))
        n = sum(len(v["people"]) for v in LOOP["cams"].values())
        print(f"[demo] 복원 궤적 — "
              + " · ".join(f"{c} {len(v['people'])}명"
                           for c, v in sorted(LOOP["cams"].items()))
              + f" (합 {n}명, 주기 {LOOP['period']}s)")

    def loop_at(t):
        """복원 궤적에서 지금 그릴 사람들 — 위상으로 찾아 선형 보간."""
        P = LOOP["period"]
        out = []
        for cam, v in LOOP["cams"].items():
            if cam not in grabs:
                continue
            ph = (t - v["ref"] + a.loop_shift) % P
            for i, pp in enumerate(v["people"]):
                pa = pp["path"]
                if not (pa[0][0] <= ph <= pa[-1][0]):
                    continue
                k = 0
                for j in range(len(pa) - 1):
                    if pa[j][0] <= ph <= pa[j + 1][0]:
                        k = j
                        break
                else:
                    continue
                q1, q2 = pa[k], pa[k + 1]
                u = (ph - q1[0]) / max(1e-6, q2[0] - q1[0])
                out.append({"gid": f"{cam}~{i}", "cam_id": cam,
                            "x": q1[1] + (q2[1] - q1[1]) * u,
                            "y": q1[2] + (q2[2] - q1[2]) * u,
                            "trail": [(r[1], r[2]) for r in pa[:k + 1]]})
        return out

    feed = StateFeed(a.floor)
    feed.start()
    print("[demo] 상태 SSE 연결…")
    tw = time.time()
    while time.time() - tw < 10 and len(feed.snaps) < 2:
        time.sleep(0.2)
    print(f"[demo] 스냅샷 {len(feed.snaps)}개 확보 · 보간 지연 {a.lag}s")

    # 자취 — **그려진 위치**를 프레임마다 쌓는다(스냅샷 5Hz 를 쌓으면 자취도 각진다)
    trails: dict = defaultdict(lambda: deque(maxlen=max(4, int(TRAIL_SEC * a.fps))))
    bar_cache, bar_sec = None, [-1]
    n_intro = int(a.intro * a.fps)
    n_main = int(a.sec * a.fps)
    t_run0 = time.time()
    try:
        # ── ① 제목 — 정지 화면이라 **한 번만 그려** 재사용한다
        intro_right = np.full((PANE_H, RIGHT_W, 3), 18, np.uint8)
        puts(intro_right, [
            (a.title or f"{fname} 실시간 운영 화면", (40, int(PANE_H * 0.36)), 44,
             (255, 255, 255), True),
            (" · ".join(a.cams) + f"  ({len(a.cams)}채널)",
             (40, int(PANE_H * 0.36) + 70), 28, DOT, True),
            ("4대 지표 실시간 산출", (40, int(PANE_H * 0.36) + 160), 30,
             (235, 235, 240), True),
            ("반응 신속도 · 경로 준수도 · 대피 혼잡도 · 출구 활용도",
             (40, int(PANE_H * 0.36) + 204), 22, (185, 185, 190), False)])
        intro_frame = np.full((OH, OW, 3), 20, np.uint8)
        intro_frame[:PANE_H, :LEFT_W] = left0
        intro_frame[:PANE_H, LEFT_W:] = intro_right
        intro_frame[PANE_H:] = bar_panel(0.0, {}, [], [], {})
        cv2.line(intro_frame, (LEFT_W, 0), (LEFT_W, PANE_H), (60, 60, 60), 1)
        for k in range(n_intro):
            due = t_run0 + k / a.fps
            if (w := due - time.time()) > 0:
                time.sleep(w)
            vw.write(intro_frame)

        # ── ② 본편
        if a.sync_loop:
            # 되감기 = 추적 중인 사람이 **전부 사라지는 순간**. 3명 이상 잡히다가
            # 0~1명으로 떨어지면 그 직후가 새 바퀴의 시작이다. **인트로 뒤**에
            # 기다려야 한다 — 앞에서 기다리면 인트로 길이만큼 바퀴를 까먹는다.
            print("[demo] 원본 루프 되감기 대기…")
            seen_many, t_w = False, time.time()
            while time.time() - t_w < 40:
                try:
                    n = len(api(f"/api/map/state?floor={a.floor}", timeout=5)
                            .get("objects") or [])
                except Exception:                    # noqa: BLE001
                    n = 0
                if n >= 3:
                    seen_many = True
                elif seen_many and n <= 1:
                    print(f"[demo] 되감기 감지 ({time.time() - t_w:.1f}s 대기)")
                    break
                time.sleep(0.2)
            else:
                print("[demo] 되감기를 못 잡았다 — 그냥 간다")
        t_base = time.time()
        for k in range(n_main):
            due = t_base + k / a.fps
            if (w := due - time.time()) > 0:
                time.sleep(w)
            # **매 프레임 보간**한다. 스냅샷을 그대로 홀드하면 초당 5번만 움직여
            # 끊겨 보인다 — 웹 UI(currentInterp)·demo_viz 와 같은 방식이다.
            _now = time.time()
            objs, st = feed.at(_now - a.lag)
            objs = [o for o in objs if o.get("cam_id") in grabs]
            draw = loop_at(_now) if LOOP else objs
            ss = st.get("session") or {}
            bns = st.get("bottlenecks") or []
            ex_now = {}
            for e in (st.get("exits") or []):
                v = e.get("out_count") or 0
                ex_base.setdefault(e["id"], v)
                ex_now[e["id"]] = max(0, v - ex_base[e["id"]])

            left = left0.copy()
            if HEAT:
                # 밀집을 보여주는 판 — **자취는 뺀다**. 자취선과 밀도장이 겹치면
                # 어느 쪽이 "몰려 있다" 인지 안 읽힌다.
                left = heat_overlay(left, [MP(o["x"], o["y"]) for o in draw],
                                    blob, lut_rgb, lut_a)
                for o in draw:                        # 누가 몇 명인지는 남겨 둔다
                    p = MPQ(o["x"], o["y"])
                    cv2.circle(left, p, 4 * K, (255, 255, 255), -1, cv2.LINE_AA, SH)
                    cv2.circle(left, p, 4 * K, (30, 30, 30), 1, cv2.LINE_AA, SH)
            else:
                if LOOP:
                    # 복원 궤적은 **자기 자취를 통째로** 갖고 있다 — 나타난 순간부터
                    # 지금까지를 다 그린다. id 가 유지된다는 게 눈에 보여야 한다.
                    ov = left.copy()
                    for o in draw:
                        tr = [MPQ(*q) for q in o["trail"]]
                        if len(tr) >= 2:
                            cv2.polylines(ov, [np.array(tr, np.int32)], False,
                                          DOT, 2, cv2.LINE_AA, SH)
                    cv2.addWeighted(ov, 0.45, left, 0.55, 0, left)
                else:
                    alive = set()
                    for o in objs:
                        g = o["gid"]
                        alive.add(g)
                        trails[g].append(MPQ(o["x"], o["y"]))
                    for g in [q for q in trails if q not in alive]:
                        trails[g].popleft()           # 사라진 사람 자취는 서서히 지운다
                        if not trails[g]:
                            del trails[g]
                    ov = left.copy()
                    for tr in trails.values():
                        if len(tr) >= 2:
                            cv2.polylines(ov, [np.array(tr, np.int32)], False,
                                          DOT, 2, cv2.LINE_AA, SH)
                    cv2.addWeighted(ov, 0.45, left, 0.55, 0, left)
                for o in draw:
                    p = MPQ(o["x"], o["y"])
                    cv2.circle(left, p, 8 * K, DOT, -1, cv2.LINE_AA, SH)
                    cv2.circle(left, p, 8 * K, (20, 20, 20), 1, cv2.LINE_AA, SH)
            spd = [o["speed_mps"] for o in objs if o.get("speed_mps") is not None]
            shown = draw

            # 경과 시계는 **영상 위치**로 그린다. 세션의 elapsed_sec(벽시계)를
            # 쓰면 렌더가 조금만 밀려도 시계만 앞서가 영상이 빨리 감긴 것처럼 보인다.
            el = a.intro + k / a.fps
            if bar_cache is None or int(el) != bar_sec[0]:
                bar_cache = bar_panel(el, ss, shown, bns, ex_now)
                bar_sec[0] = int(el)
            frame = np.full((OH, OW, 3), 20, np.uint8)
            frame[:PANE_H, :LEFT_W] = left
            frame[:PANE_H, LEFT_W:] = grid_2x2(shown, spd)
            frame[PANE_H:] = bar_cache
            cv2.line(frame, (LEFT_W, 0), (LEFT_W, PANE_H), (60, 60, 60), 1)
            vw.write(frame)
            if k % max(1, int(a.fps * 15)) == 0:
                drift = (time.time() - t_base) - k / a.fps
                print(f"  {k / a.fps:5.0f}s / {a.sec:.0f}s · 재실 {len(draw)} · "
                      f"CBS {f1(ss.get('cbs_total'), 2)} · 지연 {drift:+.1f}s",
                      flush=True)
    finally:
        vw.release()
        for g in grabs.values():
            g.stop()
        feed._stop = True
        _tot = feed.n_interp + feed.n_hold
        if _tot:
            print(f"[demo] 보간 성립 {feed.n_interp / _tot * 100:.1f}% "
                  f"({feed.n_interp}/{_tot} 프레임) — 낮으면 --lag 을 키운다")
        if sess:
            try:
                r = api(f"/api/session/stop?floor={a.floor}", {})
                print(f"[demo] 세션 종료 — SEI {r.get('sei')} · "
                      f"EPFI {r.get('epfi_avg')} · CBS {r.get('cbs_total')}")
            except Exception as e:                   # noqa: BLE001
                print(f"[demo] 세션 종료 실패: {e}")

    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22",
                        "-movflags", "+faststart", str(out_path)], check=True)
        tmp.unlink(missing_ok=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        tmp.replace(out_path)
    print(f"[demo] 완료 → {out_path} ({OW}x{OH} @ {a.fps:.0f}fps · "
          f"{out_path.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
