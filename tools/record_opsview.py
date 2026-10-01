#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""운영 뷰(:8900) 화면 녹화 — 리허설을 돌리면서 브라우저를 그대로 담는다.

`results/rehearsal_sessions/scenario_15_full.mp4`(2026-08-28, CJ 10F) 를 같은 방식으로
다시 뽑기 위한 도구다. 그때는 사람이 UI 를 눌러 가며 화면 녹화를 걸었는데, 그러면
같은 그림을 다시 만들 수 없다 — 어떤 토글이 켜져 있었는지가 기록에 안 남는다.

    python tools/record_opsview.py --scenario pkg:cj-rehearsal:scenario_15_full \
           --floor floor10 --origin 935,870 --sec 30 --off zones --on uniColor \
           --out results/rehearsal_sessions/_rec

흐름은 run_drill_batch.py 와 같다(standby → start → drill/start). 다른 점은 그
사이에 브라우저를 띄워 토글을 맞추고, 재생되는 동안 화면을 녹화한다는 것뿐이다.
이 서버엔 디스플레이가 없어 Playwright 의 내장 녹화를 쓴다(xvfb 불필요).
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8900"


def req(path: str, body=None, timeout=60):
    r = urllib.request.Request(
        BASE + path,
        data=(json.dumps(body).encode() if body is not None else None),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--floor", required=True)
    ap.add_argument("--origin", required=True, help="경보 발생원 맵좌표 'x,y'")
    ap.add_argument("--sec", type=float, default=0,
                    help="녹화 길이(초). 0 이면 재생이 끝날 때까지")
    ap.add_argument("--off", default="", help="끌 요소 레이어(콤마) — 예 zones")
    ap.add_argument("--on", default="", help="켤 토글 id 접두(콤마) — 예 uniColor")
    ap.add_argument("--zoom", type=float, default=1.0,
                    help="맵 확대 배수. 기본 맞춤(1.0)은 도면이 작아 사람이 잘 안 "
                         "보인다 — 캔버스 중앙을 기준으로 휠을 굴린 것과 같다")
    ap.add_argument("--dens", default="",
                    help="우측 지표 표출 밀도 — full(기본) · card(2×2 카드) · sum(요약)")
    ap.add_argument("--label", default="")
    ap.add_argument("--out", default="results/rehearsal_sessions/_rec")
    ap.add_argument("--name", default="")
    a = ap.parse_args()

    ox, oy = (float(v) for v in a.origin.split(","))
    outdir = ROOT / a.out
    outdir.mkdir(parents=True, exist_ok=True)
    name = a.name or (a.scenario.split(":")[-1] + ".mp4")

    from playwright.sync_api import sync_playwright

    print(f"[rec] standby {a.scenario}")
    st = req("/api/vsource/standby", {"scenario_id": a.scenario})
    floors = st.get("floors") or [a.floor]
    time.sleep(3)

    with sync_playwright() as p:
        br = p.chromium.launch(headless=True, args=["--force-device-scale-factor=1"])
        ctx = br.new_context(viewport={"width": 1600, "height": 900},
                             record_video_dir=str(outdir / "_raw"),
                             record_video_size={"width": 1600, "height": 900})
        t_ctx = time.time()          # 녹화가 시작되는 시점 ≈ 컨텍스트 생성
        pg = ctx.new_page()
        # networkidle 은 SSE 가 계속 열려 있어 **끝나지 않는다**
        pg.goto(f"{BASE}/?floor={a.floor}", wait_until="domcontentloaded")
        # 로그인 게이트(간이·프론트 전용, 아무 입력이나 통과)를 먼저 넘는다 —
        # 안 넘기면 #loginGate 가 클릭을 전부 가로챈다.
        try:
            pg.wait_for_selector("#lgForm", timeout=5000)
            pg.evaluate("document.getElementById('lgForm')"
                        ".dispatchEvent(new Event('submit',{cancelable:true}))")
            pg.wait_for_selector("#loginGate", state="detached", timeout=10000)
            print("[rec] 로그인 게이트 통과")
        except Exception:                      # noqa: BLE001
            pass
        pg.wait_for_selector('[data-view="live"]', timeout=30000)
        # 탭 전환이 한 번에 안 먹을 때가 있다 — 초기 렌더가 끝나기 전에 누르면
        # 핸들러가 아직 안 붙어 있다. **바뀔 때까지** 다시 누른다.
        for _ in range(8):
            pg.click('[data-view="live"]')
            pg.wait_for_timeout(700)
            if pg.evaluate("() => { const v = document.getElementById('viewLive');"
                           " return v && !v.classList.contains('hidden'); }"):
                break
        else:
            raise SystemExit("운영 뷰로 전환되지 않았습니다")
        pg.wait_for_selector("#liveCv", state="visible", timeout=30000)
        # **층 전환은 셀렉터로 해야 한다.** `?floor=` 쿼리만으로는 운영 뷰가
        # 17F 에 머물러 있었다(실측 — 도면·카메라·세션이 전부 17F 였다).
        pg.select_option("#floorSel", a.floor)
        pg.wait_for_timeout(2500)
        cur = pg.eval_on_selector("#floorSel", "e => e.value")
        if cur != a.floor:
            raise SystemExit(f"층 전환 실패: {cur!r} (원한 층 {a.floor!r})")
        print(f"[rec] 층 {a.floor} 로 전환")
        pg.wait_for_timeout(1200)

        # 토글 — **지금 상태를 읽고** 원하는 상태가 아닐 때만 누른다.
        # 무조건 누르면 기본값이 바뀌었을 때 반대로 뒤집힌다.
        for key in [k for k in a.off.split(",") if k.strip()]:
            sel = f'[data-layer="{key}"]'
            if pg.locator(sel).first.evaluate("e => e.classList.contains('on')"):
                pg.click(sel)
            print(f"[rec] 요소 {key} → 끔")
        for key in [k for k in a.on.split(",") if k.strip()]:
            sel = f"#{key}Toggle"
            if not pg.locator(sel).first.evaluate("e => e.classList.contains('on')"):
                pg.click(sel)
            print(f"[rec] {key} → 켬")
        if a.zoom and abs(a.zoom - 1.0) > 1e-6:
            # 실제 휠 이벤트를 쏜다 — 캔버스 내부 상태(s·ox·oy)를 밖에서 못 만지고,
            # 사람이 굴린 것과 같은 경로라야 어긋날 일이 없다. 한 칸이 ×1.15.
            box = pg.locator("#liveCv").bounding_box()
            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2
            pg.mouse.move(cx, cy)
            n = int(round(math.log(a.zoom) / math.log(1.15)))
            for _ in range(abs(n)):
                pg.mouse.wheel(0, -120 if n > 0 else 120)
                pg.wait_for_timeout(70)
            print(f"[rec] 맵 확대 ×{1.15 ** n:.2f} (휠 {n}칸, 캔버스 중앙 기준)")
            pg.wait_for_timeout(500)
        if a.dens:
            sel = f'[data-dens="{a.dens}"]'
            if not pg.locator(sel).first.evaluate("e => e.classList.contains('on')"):
                pg.click(sel)
            print(f"[rec] 지표 표출 {a.dens}")
        pg.wait_for_timeout(600)

        print("[rec] 재생 시작")
        t_play = time.time()
        pl = req("/api/vsource/start", {"scenario_id": a.scenario})
        t_alarm = pl.get("alarm_at")
        body = {"floor_origins": {a.floor: [[ox, oy]]}, "floors": floors,
                "label": a.label or a.scenario.split(":")[-1]}
        if t_alarm:
            body["t_alarm"] = float(t_alarm)   # 클립 0프레임 = 경보
        d = req("/api/drill/start", body)
        sess = d.get("session_id") or d.get("id")
        print(f"[rec] 세션 {sess}")

        t0 = time.time()
        try:
            while True:
                el = time.time() - t0
                if a.sec and el >= a.sec:
                    break
                if not a.sec and el > 5.0:
                    # 키 이름은 **running** 이다. playing/active 를 보다가 둘 다
                    # 없어서 첫 바퀴에 바로 끝났다(1.4MB 짜리 녹화가 나왔다).
                    # 재생이 붙기 전 몇 초는 건너뛴다(el > 5).
                    if not req("/api/vsource/status").get("running"):
                        print(f"[rec] 재생 종료 감지 ({el:.0f}s)")
                        break
                if int(el) % 30 == 0:
                    print(f"  {el:5.0f}s", flush=True)
                pg.wait_for_timeout(1000)
        finally:
            print("[rec] 정지")
            for path in ("/api/drill/stop", "/api/vsource/stop"):
                try:
                    req(path, {})
                except Exception:              # noqa: BLE001
                    pass
            pg.wait_for_timeout(1200)
            ctx.close(); br.close()            # close 해야 영상 파일이 써진다

    raw = sorted((outdir / "_raw").glob("*.webm"), key=lambda q: q.stat().st_mtime)
    if not raw:
        print("녹화 파일이 없습니다"); return 1
    src, dst = raw[-1], outdir / name
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23",
                        "-movflags", "+faststart", str(dst)], check=True)
        src.unlink(missing_ok=True)
    else:
        dst = dst.with_suffix(".webm"); src.replace(dst)
    # 사이드카 — **그리드 합성에 쓸 싱크 기준**이다. 영상만 남기면 "클립 t=0 이
    # 녹화 몇 초 지점인지" 를 나중에 알 수 없어 눈대중으로 맞추게 된다.
    side = dst.with_suffix(".json")
    side.write_text(json.dumps({
        "video": str(dst.relative_to(ROOT)), "session_id": sess,
        "scenario": a.scenario, "floor": a.floor, "origin": [ox, oy],
        "off": a.off, "on": a.on, "dens": a.dens, "zoom": a.zoom,
        "clip_t0_at_rec_sec": round(t_play - t_ctx, 2),
        "rec_sec": round(time.time() - t_ctx, 2),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[rec] 완료 → {dst} ({dst.stat().st_size / 1e6:.1f} MB) · 세션 {sess}")
    print(f"[rec] 클립 t=0 은 녹화 {t_play - t_ctx:.2f}s 지점 → {side.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
