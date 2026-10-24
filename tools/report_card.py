#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""리포트 카드 클립 — 발표 영상 끝에 붙이는 "결과 화면" 짧은 영상을 운영 UI(:8900)에서 그대로 떠 온다.

녹화된 건물 훈련 세션을 ④ 리플레이 탭에서 열고, **지정한 시점(경보 후 N초)** 의 지표 화면을
위에서 아래로 천천히 훑어 내려가는(스윽) 10~14초 mp4 를 만든다. 새로 디자인한 카드가 아니라
우리 UI 를 Playwright 로 캡처한 것이라, 영상 속 숫자 = 리플레이에서 같은 시점에 보이는 숫자다.

    # 16층 단독 — 영상이 경보 후 490초에서 끝나므로 그 시점 패널
    python tools/report_card.py --sid sess-1792779535311 --cut 490 \\
           --out results/demo/report_16f.mp4

    # 17층+1층 — 끝까지 보여 준 영상이면 세션 종료 리포트(건물 롤업)
    python tools/report_card.py --sid sess-1792775691397 --cut end --out report_17f1f.mp4

    # 좌우 2분할 합성 영상(3840 폭) 뒤에 붙일 때
    python tools/report_card.py --sid ... --cut end --size 3840x1102 --fit pad --out ...

무엇을 캡처하나 — `--mode auto` 의 선택 기준
-------------------------------------------
(a) **panel** — ④ 리플레이 우측 지표 패널(`#rpSide`)을 '재생 시점값' 으로 둔 상태.
    재생 커서를 경보 후 `--cut` 초로 옮기면 4대 지표 카드(SEI·EPFI·CBS·IDR, 시간 곡선 +
    커서선)·출구별 통과 인원(SEI 표)이 **그 시점 값** 으로 바뀐다. 영상이 세션 끝보다 먼저
    잘리는 경우(예: 16층 490초) 리포트 모달은 세션 최종값이라 영상 마지막 장면과 숫자가
    어긋난다 → 이때는 반드시 (a). 층이 여럿이면 층마다 한 구간씩 돌려 보여 준다
    (드릴 패널은 선택한 층 값만 보이기 때문). 밀도는 [그래프] — 객체 목록(수백 행)은
    숨고 지표 곡선·병목 그래프가 남아 발표용으로 읽기 좋다.
    주의: 패널의 'IDR 구역별' 표와 'CBS 병목' 그래프는 UI 에서도 세션 최종 결과 기준이다.
    그래서 cut 시점 타임라인 값과 최종값이 다르면(cut 이후 개시된 구역·cut 이후 쌓인 병목)
    그 표는 팬 범위에서 빼고 경고를 찍는다 — 화면에 보이는 숫자는 전부 cut 시점 값이 되게.
(b) **report** — [📋 리포트] 모달(`Session.openDrillReport`, 건물 롤업). 해석 문장·4대 지표
    카드·출구별 막대·층별 상세 표까지 한 장에 있어 결말 화면으로 가장 좋지만, 값이 세션
    **최종값** 이다. 그래서 cut 이 세션 끝(전 층 마지막 프레임)과 같을 때만 쓴다.
`auto` = cut < 세션 끝 → panel, 아니면 report. `both` 는 패널(들) 다음 리포트.

화면 구성
---------
상단바(브랜드 로고)는 살리고 탭 버튼·사이트 칩·벽시계는 숨긴 뒤 그 자리에
"리포트 · <라벨> · 경보 후 mm:ss" 제목을 넣는다(우리 상단바 스타일 그대로). 좌측 세션 목록은
다른 훈련 이름이 보여 헷갈리므로 숨긴다(캡처 브라우저 안에서만 — webui 코드는 안 건드림).
뷰포트는 출력 크기 / `--ui-scale`(기본 1.5) 로 잡아 글자를 1.5배로 키운다.

영상: 0.6초 검정에서 페이드 인 → 맨 위 정지 → ease-in-out 으로 스크롤 → 끝 정지(~2초).
스크롤 구간만 프레임마다 스크롤 위치를 바꿔 해당 영역을 캡처하고(나머지는 정지 화면 재사용)
ffmpeg 로 H.264 yuv420p 인코딩한다.

서버 부하·안전
--------------
브라우저 1개, GET 과 `POST /api/drill/<sid>/replay`(리플레이 탭이 원래 하는 호출)만 쓴다.
리포트 모드에서 모달이 자동으로 거는 ID 재구성(`POST /api/drill/<sid>/journey`, ReID — 무겁다)은
기본으로 막고 그 섹션(사람별 지표)을 숨긴다. `--with-persons` 로 켤 수 있다.
캡처 중 실수로라도 녹화를 건드리지 않도록 /api/vsource/* · /api/drill/start|stop ·
/api/session/* 의 GET 아닌 요청은 브라우저 단에서 막는다.
"""
from __future__ import annotations

import argparse
import io
import math
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

BASE = "http://127.0.0.1:8900"

# 캡처 브라우저 안에서만 적용하는 표시 조정 — webui 코드는 그대로 둔다.
CSS_COMMON = """
.topbar .viewnav, .topbar #siteChip, .topbar #clock, .topbar #floorSelWrap { display:none !important; }
#viewReplay .side.left { display:none !important; }
#rcTitle { display:flex; align-items:baseline; gap:12px; margin-left:8px; min-width:0;
  font-size:17px; font-weight:700; color:var(--text-1); white-space:nowrap; overflow:hidden; }
#rcTitle .rc-k { color:var(--accent, #30DCFB); letter-spacing:.02em; }
#rcTitle .rc-dot { color:var(--text-3); font-weight:400; }
#rcTitle .rc-lab { overflow:hidden; text-overflow:ellipsis; }
#rcTitle .rc-t { font-family:var(--font-num); color:var(--text-1); }
#rcTitle .rc-sub { font-size:13px; font-weight:400; color:var(--text-3); }
/* 리포트 모달: 내보내기 버튼은 결과 화면에 필요 없다. 제목이 든 상단바는 가리지 않게
   모달을 상단바 아래에서 시작시킨다 */
#resultModal .resfoot { display:none !important; }
#resultModal { top:52px !important; }
*::-webkit-scrollbar { width:0 !important; height:0 !important; }
"""

# 녹화·송출을 건드리는 요청은 GET 이 아니면 막는다(안전망).
GUARD_PREFIXES = ("/api/vsource/", "/api/drill/start", "/api/drill/stop", "/api/session/")

# 리플레이 응답(JSON)을 페이지에 남겨 두는 훅 — 프레임 시각으로 커서 인덱스를 정확히 잡는다.
FETCH_HOOK = """(() => {
  const of = window.fetch;
  window.fetch = async function (u) {
    const r = await of.apply(this, arguments);
    try {
      if (/\\/api\\/drill\\/[^/]+\\/replay/.test(String(u)))
        r.clone().json().then((j) => { window.__rcReplay = j; }).catch(() => {});
    } catch (e) {}
    return r;
  };
})();"""


def fmt_mmss(s: float) -> str:
    s = max(0, int(round(s)))
    return f"{s // 60}:{s % 60:02d}"


def ease(x: float) -> float:
    """ease-in-out(코사인) — 시작·끝이 부드럽게."""
    x = min(1.0, max(0.0, x))
    return 0.5 - 0.5 * math.cos(math.pi * x)


def parse_size(s: str) -> tuple[int, int]:
    w, h = s.lower().split("x")
    return int(w), int(h)


class Capture:
    """한 브라우저로 :8900 리플레이 탭을 조작하고 화면을 PIL 이미지로 떠 온다."""

    def __init__(self, pw, vw: int, vh: int, scale: float, log=print, persons=False):
        self.log = log
        self.persons = persons
        self.b = pw.chromium.launch()
        self.ctx = self.b.new_context(viewport={"width": vw, "height": vh},
                                      device_scale_factor=scale, locale="ko-KR")
        self.pg = self.ctx.new_page()
        self.pg.route("**/api/**", self._guard)
        self.pg.add_init_script(FETCH_HOOK)
        self.scale = scale

    def _guard(self, route):
        r = route.request
        path = r.url.split("8900", 1)[-1]
        if r.method != "GET" and path.startswith(GUARD_PREFIXES):
            self.log(f"  [guard] 차단: {r.method} {path}")
            return route.abort()
        # 리포트 모달이 열리며 자동으로 도는 ID 재구성(ReID, 무겁다)은 기본 차단 —
        # 서버 부하도 크고, 바쁠 때는 '재구성 실패' 문구가 그대로 찍힌다.
        if not self.persons and "/journey" in path and path.startswith("/api/drill/"):
            return route.abort()
        return route.continue_()

    def close(self):
        try:
            self.b.close()
        except Exception:
            pass

    # ---------------------------------------------------------------- 진입
    def open_replay(self):
        pg = self.pg
        pg.goto(BASE + "/", wait_until="domcontentloaded")
        pg.wait_for_selector("#lgForm")
        pg.evaluate("document.getElementById('lgForm').dispatchEvent("
                    "new Event('submit',{cancelable:true}))")
        pg.wait_for_selector("#loginGate", state="detached")
        pg.add_style_tag(content=CSS_COMMON)
        # 앱 초기화(사이트·층 로드) 전에 누르면 init 이 끝나며 ① 맵 설정으로 되돌아간다.
        # 리플레이 뷰가 '연속으로' 유지되고 목록이 찰 때까지 다시 누른다.
        vis = "!document.getElementById('viewReplay').classList.contains('hidden')"
        ok = 0
        for _ in range(80):
            if not pg.evaluate(vis):
                ok = 0
                pg.evaluate("document.querySelector('[data-view=\"replay\"]').click()")
            elif pg.evaluate("!!document.querySelector('#rpSessList .rpsess')"):
                ok += 1
                if ok >= 4:
                    break
            time.sleep(0.5)
        else:
            raise RuntimeError("리플레이 탭 전환/목록 로드 실패: "
                               + pg.inner_text("#rpSessList")[:200])

    def assert_replay(self):
        if self.pg.evaluate("document.getElementById('viewReplay').classList.contains('hidden')"):
            raise RuntimeError("리플레이 뷰가 아님 — 캡처 중단")

    def resolve_sid(self, sid: str | None, label: str | None) -> tuple[str, str]:
        rows = self.pg.evaluate("""() => [...document.querySelectorAll('#rpSessList .rpsess')]
            .map((r) => [r.dataset.id, (r.querySelector('.nm')||{}).title || ''])""")
        for rid, title in rows:
            lab = title.rsplit(" · ", 1)[0] if " · " in title else ""
            if (sid and rid == sid) or (not sid and label and lab.strip() == label.strip()):
                return rid, lab
        raise SystemExit(f"세션을 못 찾음: sid={sid} label={label} — ④ 리플레이 '건물 훈련' 목록 확인")

    def load_session(self, sid: str):
        pg = self.pg
        t = time.time()
        pg.evaluate("window.__rcReplay = null")
        with pg.expect_response(lambda r: f"/api/drill/{sid}/replay" in r.url, timeout=240000):
            # 좌측 목록은 CSS 로 숨겨 두었으므로 DOM click 으로 고른다
            pg.evaluate("(id) => document.querySelector(`#rpSessList .rpsess[data-id=\"${id}\"]`).click()", sid)
        pg.wait_for_function("window.__rcReplay && window.__rcReplay.drill", timeout=60000)
        pg.wait_for_function("document.getElementById('rpFloorSel').options.length > 0")
        time.sleep(1.5)
        self.log(f"  세션 로드 {time.time() - t:.1f}s")
        info = pg.evaluate("""() => {
          const j = window.__rcReplay, at = j.drill.alarm_ts;
          const fl = {};
          Object.entries(j.frames_by_floor || {}).forEach(([f, fr]) => {
            if (fr.length) fl[f] = { n: fr.length, t0: fr[0].ts - at, t1: fr[fr.length-1].ts - at };
          });
          return { alarm_ts: at, floors: j.drill.floors || [], frames: fl, label: j.label || '' };
        }""")
        return info

    def prepare_panel(self):
        """그래프 밀도 + 재생 시점값 켜기."""
        pg = self.pg
        # 클릭은 DOM 으로 — 숨김·가림 상태 판정에 걸리지 않게
        pg.evaluate("""() => {
          const s = document.getElementById('rpSide');
          if (s.classList.contains('folded')) document.getElementById('rpFold').click();
          document.querySelector('#rpDens [data-dens="viz"]').click();
          const c = document.getElementById('rpAtCursor');
          if (!c.checked) { c.checked = true; c.dispatchEvent(new Event('change')); }
        }""")
        time.sleep(0.4)

    def seek(self, floor: str, cut: float) -> dict:
        """층 선택 + 커서를 경보 후 cut 초(이하 마지막 프레임)로. 일관성 점검 결과를 돌려준다."""
        pg = self.pg
        if pg.eval_on_selector("#rpFloorSel", "e => e.value") != floor:
            pg.select_option("#rpFloorSel", floor)   # change 이벤트 → loadDrillFloor
            time.sleep(1.0)
        res = pg.evaluate("""([floor, cut]) => {
          const j = window.__rcReplay, at = j.drill.alarm_ts;
          const fr = j.frames_by_floor[floor] || [];
          let idx = 0;
          for (let i = 0; i < fr.length; i++) { if (fr[i].ts - at <= cut + 1e-6) idx = i; else break; }
          const e = document.getElementById('rpSeek');
          e.value = String(idx);
          e.dispatchEvent(new Event('input')); e.dispatchEvent(new Event('change'));
          // 패널의 IDR 표·CBS 병목은 최종 결과 기준 — cut 시점과 같은지 점검
          const pf = (j.drill.per_floor || []).find((p) => p.floor_id === floor) || {};
          const r = pf.result || {};
          const tl = j.timeline_by_floor[floor] || [];
          const cursor = fr.length ? fr[idx].ts - fr[0].ts : 0;
          let p = null;
          if (tl.length) { const tgt = tl[0].ts + cursor;
            for (const x of tl) { if (x.ts <= tgt) p = x; else break; } }
          // IDR 구역별 표는 건물 훈련이면 전 층 구역을 함께 나열한다 → 전 층을 본다
          const zs = [].concat(...(j.drill.per_floor || []).map((q) =>
            ((q.result || {}).zone_metrics || []).map((z) => ({ ...z, _f: q.floor_id }))));
          const lateZ = zs.filter((z) => z.evacuation_start_at != null
                                         && z.evacuation_start_at - at > cut).map((z) => `${z._f}:${z.zone_id || z.id}`);
          return { idx, ts: fr.length ? fr[idx].ts - at : null,
                   cbs_cut: p ? (p.cbs_total || 0) : null, cbs_final: r.cbs_total || 0,
                   late_zones: lateZ };
        }""", [floor, cut])
        time.sleep(1.2)          # 도면·스파크 다시 그릴 시간
        return res

    def hide_base_line(self, on: bool):
        # rpBase(층별 '저장값' 한 줄)는 최종값 — cut 이 끝보다 앞이면 숨긴다
        self.pg.evaluate("(on) => { document.getElementById('rpBase').style.display = on ? 'none' : ''; }", on)

    def set_title(self, html: str):
        self.pg.evaluate("""(h) => {
          let el = document.getElementById('rcTitle');
          if (!el) { el = document.createElement('span'); el.id = 'rcTitle';
            const tb = document.querySelector('.topbar');
            tb.insertBefore(el, tb.querySelector('.sp')); }
          el.innerHTML = h; }""", html)

    def open_report(self, jy_wait: float):
        pg = self.pg
        pg.evaluate("document.getElementById('rpReport').click()")
        pg.wait_for_selector("#resultModal:not(.hidden)")
        if not self.persons:
            # 사람별 지표(ID 재구성) 섹션은 제목째 숨긴다
            pg.evaluate("""() => { const t = document.getElementById('repPersonTbl');
              if (!t) return; t.style.display = 'none';
              const h = t.previousElementSibling;
              if (h && h.classList.contains('repsec-h')) h.style.display = 'none'; }""")
            time.sleep(1.0)
            return
        t = time.time()
        while time.time() - t < jy_wait:
            txt = pg.evaluate("(document.getElementById('repPersonTbl')||{}).innerText || ''")
            if "재구성 중" not in txt:
                break
            time.sleep(1.0)
        else:
            self.log("  [warn] 사람별 지표(ID 재구성) 대기 시간 초과 — '재구성 중' 상태로 캡처")
        time.sleep(0.8)

    # ---------------------------------------------------------------- 캡처
    def shot(self, clip=None) -> Image.Image:
        kw = {"type": "png", "animations": "disabled", "caret": "hide"}
        if clip:
            kw["clip"] = clip
        return Image.open(io.BytesIO(self.pg.screenshot(**kw))).convert("RGB")

    def scroll_info(self, body_sel: str, stop_sel: str | None) -> dict:
        """스크롤 컨테이너의 위치(clip)와 팬 끝(scrollTop 최대값)."""
        return self.pg.evaluate("""([bs, ss]) => {
          const b = document.querySelector(bs); b.scrollTop = 0;
          const r = b.getBoundingClientRect();
          let end = b.scrollHeight - b.clientHeight;
          if (ss) { const s = document.querySelector(ss);
            if (s && s.offsetParent !== null) {
              const sb = s.getBoundingClientRect().bottom - r.top + 16;
              end = Math.min(end, Math.max(0, sb - b.clientHeight)); } }
          return { x: r.left, y: r.top, w: r.width, h: r.height, end: Math.max(0, end) };
        }""", [body_sel, stop_sel])

    def set_scroll(self, body_sel: str, y: float):
        self.pg.evaluate("([s, y]) => { document.querySelector(s).scrollTop = y; }", [body_sel, y])


def render_segment(cap: Capture, body_sel: str, stop_sel: str | None, dur: float,
                   fps: int, hold0: float, hold1: float, emit) -> int:
    """정지(hold0) → 스크롤 팬 → 정지(hold1). 스크롤 컨테이너 영역만 프레임마다 다시 캡처."""
    cap.assert_replay()
    si = cap.scroll_info(body_sel, stop_sel)
    time.sleep(0.2)
    base = cap.shot()
    s = cap.scale
    clip = {"x": si["x"], "y": si["y"], "width": si["w"], "height": si["h"]}
    box = (round(si["x"] * s), round(si["y"] * s))
    n_total = max(1, round(dur * fps))
    n0, n1 = round(hold0 * fps), round(hold1 * fps)
    n_pan = max(0, n_total - n0 - n1)
    if si["end"] < 4:                       # 스크롤할 것이 없으면 그냥 정지 화면
        n0, n_pan, n1 = n_total, 0, 0
    for _ in range(n0):
        emit(base)
    last = base
    for i in range(n_pan):
        y = si["end"] * ease((i + 1) / n_pan)
        cap.set_scroll(body_sel, y)
        part = cap.shot(clip)
        fr = base.copy()
        fr.paste(part, box)
        emit(fr)
        last = fr
    for _ in range(n1):
        emit(last)
    return n_total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sid", help="건물 훈련 세션 id (예 sess-1792779535311)")
    ap.add_argument("--label", help="sid 대신 세션 라벨로 찾기(정확히 일치). 제목 표기에도 쓴다")
    ap.add_argument("--floors", default="",
                    help="보여 줄 층 id(콤마, 예 floor7,floor9). 비우면 세션 참여 층 전부")
    ap.add_argument("--cut", default="end",
                    help="경보 후 몇 초 시점인가(예 490). 'end' = 세션 끝")
    ap.add_argument("--mode", default="auto", choices=["auto", "panel", "report", "both"])
    ap.add_argument("--out", required=True, help="출력 mp4 경로")
    ap.add_argument("--size", default="1920x1102", help="출력 WxH (기본 1920x1102)")
    ap.add_argument("--fit", default="ui", choices=["ui", "pad"],
                    help="ui: 출력 비율로 UI 를 그대로 렌더 · pad: 1920 폭 기준으로 렌더 후 좌우 패딩"
                         "(3840 폭 합성 영상용)")
    ap.add_argument("--ui-scale", type=float, default=0.0,
                    help="UI 확대 배율(device scale). 0 = 출력 높이 1102 기준 1.5")
    ap.add_argument("--dur", type=float, default=0.0,
                    help="클립 길이(초). 0 = 자동(1구간 12초, 구간이 늘면 최대 14초)")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--fade", type=float, default=0.6, help="검정에서 페이드 인(초)")
    ap.add_argument("--end-hold", type=float, default=2.0, help="마지막 정지(초)")
    ap.add_argument("--with-persons", action="store_true",
                    help="리포트 모드: 사람별 지표(ID 재구성) 섹션 포함. 기본은 요청을 막고 섹션을 숨김"
                         "(ReID 재구성은 무겁고 서버가 바쁘면 실패 문구가 찍힌다)")
    ap.add_argument("--jy-wait", type=float, default=90.0,
                    help="--with-persons 일 때 재구성 로딩 대기 상한(초)")
    ap.add_argument("--title", default="", help="제목 라벨 직접 지정(기본: 세션 라벨)")
    ap.add_argument("--inject-html", default="",
                    help="리포트 모드: 리포트 창 본문 맨 위에 끼워 넣을 HTML 파일(같은 클래스·스타일). "
                         "예: tools/cj1022_eval_report.py 가 만든 피난훈련 평가 보고서 섹션")
    ap.add_argument("--modal-title", default="", help="리포트 모드: 창 제목(#resTitle) 바꾸기")
    ap.add_argument("--keep-png", default="", help="디버그: 각 구간 첫·끝 프레임 PNG 저장 폴더")
    a = ap.parse_args()
    if not a.sid and not a.label:
        ap.error("--sid 또는 --label 이 필요합니다")

    W, H = parse_size(a.size)
    rw, rh = (1920, H) if (a.fit == "pad" and W > 1920) else (W, H)
    scale = a.ui_scale or round(1.5 * rh / 1102, 3)
    vw, vh = round(rw / scale), round(rh / scale)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    t_start = time.time()
    with sync_playwright() as pw:
        cap = Capture(pw, vw, vh, scale, persons=a.with_persons)
        try:
            cap.open_replay()
            sid, lab0 = cap.resolve_sid(a.sid, a.label)
            print(f"[report_card] {sid} '{lab0}' · 뷰포트 {vw}x{vh}@{scale} → {rw}x{rh}")
            info = cap.load_session(sid)
            label = a.title or a.label or lab0 or info["label"] or sid
            frames = info["frames"]
            floors = [f for f in (a.floors.split(",") if a.floors else info["floors"]) if f]
            floors = [f for f in floors if f in frames] or list(frames)
            end_s = max(frames[f]["t1"] for f in floors)
            cut = end_s if a.cut == "end" else float(a.cut)
            at_end = cut >= end_s - 0.5
            cut = min(cut, end_s)
            mode = a.mode if a.mode != "auto" else ("report" if at_end else "panel")
            if mode in ("report", "both") and not at_end:
                print(f"  [warn] 리포트 모달은 세션 최종값(경보 후 {fmt_mmss(end_s)}) — "
                      f"cut {fmt_mmss(cut)} 과 숫자가 다를 수 있음")
            fname = {o: o for o in floors}
            fname.update(cap.pg.evaluate("""(fs) => Object.fromEntries(fs.map((f) =>
                [f, (typeof App !== 'undefined' && App.floorName) ? App.floorName(f) : f]))""", floors))
            print(f"  층 {', '.join(fname[f] for f in floors)} · cut 경보 후 {fmt_mmss(cut)}"
                  f" (세션 끝 {fmt_mmss(end_s)}) · 모드 {mode}")

            segs = []                      # (kind, floor)
            if mode in ("panel", "both"):
                segs += [("panel", f) for f in floors]
            if mode in ("report", "both"):
                segs.append(("report", None))
            dur = a.dur or min(14.0, 12.0 + 1.0 * (len(segs) - 1))
            fps = a.fps

            # ffmpeg — 프레임은 rawvideo 로 흘려 보내고 페이드·패딩·인코딩은 ffmpeg 에 맡긴다
            vf = [f"fade=t=in:st=0:d={a.fade}"]
            if (rw, rh) != (W, H):
                vf.insert(0, f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x121212")
            ff = subprocess.Popen(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                 "-s", f"{rw}x{rh}", "-r", str(fps), "-i", "-", "-vf", ",".join(vf),
                 "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
                 "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
            n_emit = [0]
            prev = [None]
            png_dir = Path(a.keep_png) if a.keep_png else None
            if png_dir:
                png_dir.mkdir(parents=True, exist_ok=True)

            def emit(img: Image.Image):
                if img.size != (rw, rh):
                    img = img.resize((rw, rh), Image.LANCZOS)
                ff.stdin.write(img.tobytes())
                n_emit[0] += 1
                prev[0] = img

            # 구간 시간 배분: 마지막 구간에 end-hold, 나머지는 짧은 정지
            n = len(segs)
            per = dur / n
            when = f"경보 후 {fmt_mmss(cut)}" if not at_end else f"훈련 종료 · 경보 후 {fmt_mmss(cut)}"
            for k, (kind, fl) in enumerate(segs):
                last = (k == n - 1)
                hold0 = a.fade + 0.9 if k == 0 else 1.0
                hold1 = a.end_hold if last else 0.8
                if kind == "panel":
                    if k == 0 or segs[k - 1][0] != "panel":
                        cap.prepare_panel()
                    chk = cap.seek(fl, cut)
                    cap.hide_base_line(not at_end)
                    stop = "#rpGrpBn"
                    if round(chk["cbs_cut"] or 0, 1) != round(chk["cbs_final"], 1):   # 표시 자릿수 기준
                        print(f"  [warn] {fname[fl]}: CBS 병목 표는 최종값({chk['cbs_final']:.1f})이라 "
                              f"cut 값({chk['cbs_cut']})과 다름 → 병목 표 제외")
                        stop = "#rpGrpIdr"
                    if chk["late_zones"]:
                        print(f"  [warn] {fname[fl]}: cut 이후 개시 구역 {chk['late_zones']} → IDR 표 제외")
                        stop = "#rpGrpSei"
                    sub = f"{fname[fl]}" + (f" ({k + 1}/{n})" if n > 1 else "")
                    print(f"  구간 {k + 1}/{n}: 패널 {fname[fl]} · 프레임 idx {chk['idx']} "
                          f"(경보 후 {chk['ts']:.1f}s) · 팬 끝 {stop}")
                    body, stop_sel = "#rpBodyMetrics", stop
                else:
                    cap.open_report(a.jy_wait)
                    if a.inject_html or a.modal_title:
                        # 리포트 창에 평가 섹션을 **같은 마크업으로** 얹는다 — 창은 UI 그대로.
                        cap.pg.evaluate("""([html, ttl]) => {
                            if (html) document.getElementById('resBody')
                                .insertAdjacentHTML('afterbegin', html);
                            if (ttl) document.getElementById('resTitle').textContent = ttl;
                            // 개요 행 값 바꾸기 — 끼운 섹션의 data-rows {라벨: 값}
                            const ev = document.getElementById('cjEval');
                            const rows = ev && ev.dataset.rows ? JSON.parse(ev.dataset.rows) : {};
                            document.querySelectorAll('#resBody .resrow').forEach((r) => {
                              if (ev && ev.contains(r)) return;
                              const k = (r.querySelector('span') || {}).textContent;
                              if (k && rows[k] != null) r.querySelector('b').textContent = rows[k];
                            });
                            document.getElementById('resBody').scrollTop = 0; }""",
                            [Path(a.inject_html).read_text(encoding="utf-8") if a.inject_html else "",
                             a.modal_title])
                        time.sleep(0.6)
                    sub = ("피난훈련 평가 보고서" if a.inject_html else "건물 롤업") \
                        + (f" ({k + 1}/{n})" if n > 1 else "")
                    print(f"  구간 {k + 1}/{n}: 리포트 모달")
                    body, stop_sel = "#resBody", None
                cap.set_title(
                    f'<span class="rc-k">리포트</span><span class="rc-dot">·</span>'
                    f'<span class="rc-lab">{label}</span><span class="rc-dot">·</span>'
                    f'<span class="rc-t">{when}</span><span class="rc-sub">{sub}</span>')
                time.sleep(0.3)
                render_segment(cap, body, stop_sel, per, fps, hold0, hold1, emit)
                if png_dir and prev[0] is not None:
                    prev[0].save(png_dir / f"seg{k + 1}_end.png")
            ff.stdin.close()
            rc = ff.wait()
            if rc != 0:
                raise RuntimeError(f"ffmpeg 실패 rc={rc}")
        finally:
            cap.close()
    print(f"[report_card] → {out} · {n_emit[0]} 프레임 ({n_emit[0] / fps:.1f}s) · "
          f"소요 {time.time() - t_start:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
