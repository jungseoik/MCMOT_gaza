#!/usr/bin/env python3
"""층 라이브 시각화 — 좌: 도면(해당 카메라 구역 줌) / 우: 카메라 그리드 / 하: 4대지표.

    python tools/floor_live_viz.py --floor default --cams cam01 cam02 cam03 \
           --sec 180 --out results/floor_viz/17F.mp4

무엇을 보여주나
---------------
웹 UI(:8900) 의 운영 뷰와 **같은 값**을 영상으로 남긴다. 지표는 직접 계산하지
않고 서버의 `/api/map/state` 를 그대로 받아 그린다 — 따로 계산하면 화면과
영상이 어긋나서 근거로 못 쓴다.

  좌 : 도면. 지정 카메라들의 대응점(map_pts) 을 감싸는 영역으로 **줌** 한다.
       구역·병목·출구·경로 + 추적점(카메라별 색) + 2초 궤적.
  우 : 카메라 그리드. RTSP 원본에 서버가 보내준 객체의 발끝점을 되투영해 표시.
  하 : 4대지표(SEI·EPFI·CBS·IDR) + 인원·평균속도·출구통과.

경보 세션(--session) 을 켜면 녹화 시작과 함께 세션을 시작하고 끝나면 종료한다 —
4대지표는 세션이 있어야 산출된다.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = "http://localhost:8900"
CAMCOL = [(80, 220, 255), (120, 255, 140), (255, 160, 90), (240, 130, 255),
          (255, 235, 100), (150, 180, 255)]


def api(path: str, body=None, timeout=30):
    req = urllib.request.Request(
        BASE + path,
        data=(json.dumps(body).encode() if body is not None else None),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class Grab(threading.Thread):
    """RTSP 1채널 최신 프레임 유지 — 읽는 쪽이 느려도 밀리지 않게 최신만 남긴다."""

    def __init__(self, url: str):
        super().__init__(daemon=True)
        self.url, self.frame, self.ok = url, None, False
        self._stop = False

    def run(self):
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        while not self._stop:
            ok, fr = cap.read()
            if not ok:
                time.sleep(0.2)
                cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
                continue
            self.frame, self.ok = fr, True
        cap.release()

    def stop(self):
        self._stop = True


def fit(img, w, h):
    """비율 유지 축소 + 레터박스."""
    ih, iw = img.shape[:2]
    s = min(w / iw, h / ih)
    r = cv2.resize(img, (max(1, int(iw * s)), max(1, int(ih * s))))
    out = np.zeros((h, w, 3), np.uint8)
    y, x = (h - r.shape[0]) // 2, (w - r.shape[1]) // 2
    out[y:y + r.shape[0], x:x + r.shape[1]] = r
    return out, s, x, y


# 한글 — cv2.putText 는 한글을 못 그린다(??? 로 나온다). 웹 UI 와 같은 Pretendard 로
# PIL 렌더링한다. 폰트가 없으면 cv2 로 떨어뜨린다(영문·숫자는 그대로 나온다).
_FONT_DIR = ROOT / "webui" / "static" / "fonts"
_font_cache: dict = {}


def _font(px: int, bold: bool):
    key = (px, bold)
    if key not in _font_cache:
        from PIL import ImageFont
        f = _FONT_DIR / ("Pretendard-Bold.ttf" if bold else "Pretendard-Regular.ttf")
        try:
            _font_cache[key] = ImageFont.truetype(str(f), px)
        except Exception:
            _font_cache[key] = None
    return _font_cache[key]


def put(img, text, org, scale=0.5, col=(235, 235, 235), th=1):
    """BGR 이미지에 텍스트 — 검은 외곽선 + 본문. scale 은 cv2 관례를 따른다."""
    px = max(9, int(round(scale * 34)))
    fnt = _font(px, th >= 2)
    if fnt is None:
        cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), th + 2, cv2.LINE_AA)
        cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, col, th, cv2.LINE_AA)
        return
    from PIL import Image, ImageDraw
    pim = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pim)
    xy = (org[0], org[1] - px)                       # cv2 는 baseline, PIL 은 top 기준
    rgb = (col[2], col[1], col[0])
    d.text(xy, text, font=fnt, fill=(0, 0, 0), stroke_width=2, stroke_fill=(0, 0, 0))
    d.text(xy, text, font=fnt, fill=rgb)
    img[:, :, :] = cv2.cvtColor(np.array(pim), cv2.COLOR_RGB2BGR)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--floor", default="default")
    ap.add_argument("--cams", nargs="+", required=True)
    ap.add_argument("--sec", type=float, default=180.0)
    ap.add_argument("--fps", type=float, default=5.0, help="출력 영상 fps")
    ap.add_argument("--out", default="results/floor_viz/live.mp4")
    ap.add_argument("--session", action="store_true", default=True,
                    help="녹화 동안 경보 세션을 켠다 (4대지표 산출)")
    ap.add_argument("--no-session", dest="session", action="store_false")
    ap.add_argument("--pad", type=float, default=0.12, help="줌 영역 여백 비율")
    a = ap.parse_args()

    site = api(f"/api/site?floor={a.floor}")
    _cl = api("/api/cameras")
    cams = {c["cam_id"]: c for c in (_cl["cameras"] if isinstance(_cl, dict) else _cl)}
    picked = [cams[c] for c in a.cams if c in cams]
    if len(picked) != len(a.cams):
        raise SystemExit(f"카메라 없음: {set(a.cams) - set(cams)}")

    # ── 줌 영역: 지정 카메라들의 맵 대응점을 감싸는 사각형 + 여백
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
    # 패널 비율(1:1)에 맞춰 짧은 쪽을 넓힌다 — 레터박스 검은 띠를 없애고 주변 맥락도 함께 보인다
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    side = max(x1 - x0, y1 - y0) / 2
    x0, x1, y0, y1 = cx - side, cx + side, cy - side, cy + side

    mp = ROOT / "data" / "sites" / "default" / ((site.get("map") or {}).get("image") or "map.png")
    base = cv2.imread(str(mp))
    if base is None:
        raise SystemExit(f"도면 이미지 없음: {mp}")
    H, W = base.shape[:2]
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(W, int(x1)), min(H, int(y1))
    crop = base[y0:y1, x0:x1].copy()
    print(f"[viz] 층 {a.floor} · 카메라 {a.cams} · 줌 {x1-x0}x{y1-y0}px (전체 {W}x{H})")

    # ── 레이아웃: 좌 도면 / 우 그리드 / 하 지표
    PANE_H, LEFT_W, RIGHT_W, BAR_H = 720, 720, 900, 132
    OW, OH = LEFT_W + RIGHT_W, PANE_H + BAR_H
    out_path = ROOT / a.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), a.fps, (OW, OH))

    grabs = {c["cam_id"]: Grab(c["rtsp"]) for c in picked}
    for g in grabs.values(): g.start()
    print("[viz] RTSP 연결 대기…")
    t_wait = time.time()
    while time.time() - t_wait < 15 and not all(g.ok for g in grabs.values()):
        time.sleep(0.5)
    print("[viz] 연결:", {k: v.ok for k, v in grabs.items()})

    sess = None
    if a.session:
        try:
            sess = api(f"/api/session/start?floor={a.floor}", {"origin": [(x0+x1)/2, (y0+y1)/2]})
            print(f"[viz] 경보 세션 시작 — {sess['session_id']}")
        except Exception as e:
            print(f"[viz] 세션 시작 실패(지표 없이 진행): {e}")

    trails = defaultdict(lambda: deque(maxlen=int(2 * a.fps)))
    ex_base = None            # 출구 카운트는 서버 누적치라 녹화 시작값을 빼서 구간 증분으로 쓴다
    cols = {c: CAMCOL[i % len(CAMCOL)] for i, c in enumerate(a.cams)}
    n_frames = int(a.sec * a.fps)
    t0 = time.time()
    try:
        for k in range(n_frames):
            due = t0 + k / a.fps
            if (w := due - time.time()) > 0: time.sleep(w)
            try:
                st = api(f"/api/map/state?floor={a.floor}", timeout=5)
            except Exception:
                st = {}
            objs = [o for o in (st.get("objects") or []) if o.get("cam_id") in cols]

            # ── 좌: 도면
            m = crop.copy()
            sx = LEFT_W / m.shape[1]; sy = PANE_H / m.shape[0]; s = min(sx, sy)
            def MP(x, y):
                return int((x - x0) * s), int((y - y0) * s)
            mm = cv2.resize(m, (int(m.shape[1]*s), int(m.shape[0]*s)))
            left = np.zeros((PANE_H, LEFT_W, 3), np.uint8)
            left[:mm.shape[0], :mm.shape[1]] = mm
            for z in (site.get("zones") or []):
                pp = np.array([MP(*p) for p in z["polygon"]], np.int32)
                cv2.polylines(left, [pp], True, (90, 200, 255), 2)
            for bn in (site.get("bottlenecks") or []):
                pp = np.array([MP(*p) for p in bn["polygon"]], np.int32)
                cv2.polylines(left, [pp], True, (80, 140, 255), 2)
            for r in (site.get("routes") or []):
                pp = [MP(*p) for p in (r.get("points") or [])]
                for i in range(len(pp)-1): cv2.line(left, pp[i], pp[i+1], (120, 255, 160), 2)
            for e in (site.get("exits") or []):
                ln = e.get("line") or []
                if len(ln) >= 2: cv2.line(left, MP(*ln[0]), MP(*ln[1]), (60, 90, 255), 3)
            for o in objs:
                p = MP(o["x"], o["y"]); c = cols[o["cam_id"]]
                trails[f'{o["cam_id"]}:{o["id"]}'].append(p)
                tr = list(trails[f'{o["cam_id"]}:{o["id"]}'])
                for i in range(len(tr)-1): cv2.line(left, tr[i], tr[i+1], c, 1)
                cv2.circle(left, p, 5, c, -1); cv2.circle(left, p, 5, (20,20,20), 1)
                if o.get("speed_mps") is not None:
                    put(left, f'{o["speed_mps"]:.1f}', (p[0]+8, p[1]-6), .38, c)
            fname = next((f.get("name") for f in (site.get("floors") or [])
                          if f.get("id") == a.floor), a.floor)
            put(left, f"{fname} 도면 — {' · '.join(a.cams)} 구역", (12, 26), .58, (240,240,240), 2)
            for i,(lb,c) in enumerate([("구역",(90,200,255)),("병목",(80,140,255)),
                                       ("경로",(120,255,160)),("출구",(60,90,255))]):
                x = 12 + i*66
                cv2.rectangle(left, (x, 40), (x+11, 50), c, -1)
                put(left, lb, (x+16, 50), .40, (190,190,190))

            # ── 우: 카메라 그리드
            right = np.zeros((PANE_H, RIGHT_W, 3), np.uint8)
            n = len(a.cams); cw, ch = RIGHT_W // 2, PANE_H // ((n + 1) // 2)
            for i, cid in enumerate(a.cams):
                fr = grabs[cid].frame
                cell = np.zeros((ch, cw, 3), np.uint8)
                if fr is not None:
                    img, sc, ox, oy = fit(fr, cw, ch)
                    cell = img
                    for o in objs:
                        if o["cam_id"] != cid: continue
                        cv2.circle(cell, (0,0), 0, (0,0,0), 1)
                    put(cell, cid, (8, 20), .5, cols[cid])
                    put(cell, f"{sum(1 for o in objs if o['cam_id']==cid)}명", (8, 40), .45, cols[cid])
                else:
                    put(cell, f"{cid} — 연결 없음", (10, ch//2), .5, (120,120,120))
                cv2.rectangle(cell, (0,0), (cw-1, ch-1), cols[cid], 2)
                ry, rx = (i // 2) * ch, (i % 2) * cw
                right[ry:ry+ch, rx:rx+cw] = cell

            # ── 하: 지표
            bar = np.full((BAR_H, OW, 3), 18, np.uint8)
            ss = st.get("session") or {}
            spd = [o["speed_mps"] for o in objs if o.get("speed_mps") is not None]
            zs = st.get("zones") or []
            started = sum(1 for z in zs if z.get("status") == "started")
            ex_now = sum((e.get("out_count") or 0) for e in (st.get("exits") or []))
            if ex_base is None: ex_base = ex_now
            ex_tot = max(0, ex_now - ex_base)
            f1 = lambda v, d=1: "—" if v is None else f"{v:.{d}f}"
            put(bar, "4대 지표", (16, 26), .52, (150,150,150))
            for i,(lab,val,col) in enumerate([
                    ("SEI 출구효율", f1(ss.get("sei"), 0), (120,255,190)),
                    ("EPFI 경로충실", f1(ss.get("epfi_avg"), 0), (120,220,255)),
                    ("CBS 병목누적", f1(ss.get("cbs_total"), 2), (120,170,255)),
                    ("IDR 개시구역", f"{started}/{len(zs)}", (200,180,255))]):
                x = 16 + i*210
                put(bar, lab, (x, 52), .42, (160,160,160))
                put(bar, val, (x, 84), .82, col, 2)
            put(bar, "실시간", (880, 26), .52, (150,150,150))
            for i,(lab,val) in enumerate([
                    ("추적 인원", f"{len(objs)}명"),
                    ("평균 속도", f"{np.mean(spd):.2f} m/s" if spd else "—"),
                    ("출구 통과", f"{ex_tot}명"),
                    ("경과", f"{k/a.fps:.0f}s / {a.sec:.0f}s")]):
                x = 880 + i*190
                put(bar, lab, (x, 52), .42, (160,160,160))
                put(bar, val, (x, 84), .62, (235,235,235), 2)

            frame = np.zeros((OH, OW, 3), np.uint8)
            frame[:PANE_H, :LEFT_W] = left
            frame[:PANE_H, LEFT_W:] = right
            frame[PANE_H:] = bar
            cv2.line(frame, (LEFT_W, 0), (LEFT_W, PANE_H), (60,60,60), 1)
            vw.write(frame)
            if k % int(a.fps * 15) == 0:
                print(f"  {k/a.fps:5.0f}s / {a.sec:.0f}s · 객체 {len(objs)} · SEI {f1(ss.get('sei'),0)}", flush=True)
    finally:
        vw.release()
        for g in grabs.values(): g.stop()
        if sess:
            try:
                r = api(f"/api/session/stop?floor={a.floor}", {})
                print(f"[viz] 세션 종료 — SEI {r.get('sei')} · EPFI {r.get('epfi_avg')} · CBS {r.get('cbs_total')}")
            except Exception as e:
                print(f"[viz] 세션 종료 실패: {e}")
    print(f"[viz] 저장 → {out_path} ({OW}x{OH} @ {a.fps:.0f}fps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
