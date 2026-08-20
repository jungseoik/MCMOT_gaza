#!/usr/bin/env python3
"""사용 가이드용 화면 캡처 — 도면 편집기(:8910) · 멀티카메라 시스템(:8900).

가이드 개정 시 화면이 바뀌므로 스크린샷은 **매 버전 새로 캡처**한다. 이 스크립트가
그 절차를 고정한다(같은 화면·같은 순서·같은 파일명 → 원고의 @img 참조가 유지됨).

캡처는 **기본 시드 설정**(17F 도면 · 구역 z3~z5 · 병목 b1·b2 · 출구 e1·e3 ·
경로 r1·r2)을 그대로 담는다. 설정을 바꾸지 않으며, 평가 세션만 예시용으로
시작·종료한다(세션 파일 1건이 생성됨).

사용:
  conda run -n boosttrack python tools/capture_manual_shots.py --out docs/manual/v0.0.2/img
  conda run -n boosttrack python tools/capture_manual_shots.py --out <dir> --skip-session
  conda run -n boosttrack python tools/capture_manual_shots.py --out <dir> --only 8900

사전 조건:
  - :8900 (macs-system) · :8910 (evac-editor) 기동 상태
  - 카메라가 최소 1대 매핑·활성 상태 (대응점 화면 캡처용)
  - playwright + chromium 설치
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

MAIN = "http://127.0.0.1:8900"
EDITOR = "http://127.0.0.1:8910"


def api(path: str, method="GET", body=None):
    r = urllib.request.Request(
        MAIN + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"} if body else {})
    with urllib.request.urlopen(r, timeout=90) as resp:
        return json.load(resp)


class Shooter:
    def __init__(self, page, out: Path):
        self.pg, self.out, self.n, self.fail = page, out, 0, []

    def shot(self, name: str, sel: str | None = None, note: str = ""):
        try:
            target = self.pg.locator(sel).first if sel else self.pg
            target.screenshot(path=str(self.out / f"{name}.png"))
            self.n += 1
            print(f"  ✔ {name}.png  {note}")
        except Exception as e:
            self.fail.append(name)
            print(f"  ✗ {name}: {type(e).__name__}")


def capture_editor(pw, out: Path) -> Shooter:
    print("▶ 도면 편집기 (:8910)")
    b = pw.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1760, "height": 1000}, device_scale_factor=2)
    s = Shooter(pg, out)
    pg.goto(EDITOR + "/")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(2500)

    s.shot("01_editor_start", note="초기 화면(파일 열기)")
    s.shot("02_editor_steps", "#steps", "작업 단계 표시")
    s.shot("03_editor_hud", "#hud", "화면 안내")
    for bid, name, note in (("#m-open", "04_editor_open", "문 열기 터치업"),
                            ("#m-exit", "05_editor_exit", "Exit 지정"),
                            ("#m-del", "06_editor_del", "요소 삭제")):
        try:
            pg.locator(bid).click(); pg.wait_for_timeout(600)
        except Exception:
            pass
        s.shot(name, note=note)
    try:
        pg.locator("#verifyBtn").scroll_into_view_if_needed(); pg.wait_for_timeout(400)
    except Exception:
        pass
    s.shot("07_editor_verify", note="경로 검증·열지도")
    b.close()
    return s


def capture_main(pw, out: Path, do_session: bool) -> Shooter:
    print("▶ 멀티카메라 시스템 (:8900)")
    b = pw.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1760, "height": 1000}, device_scale_factor=2)
    s = Shooter(pg, out)

    def enter():
        pg.goto(MAIN + "/")
        pg.wait_for_load_state("networkidle")
        if pg.locator("#lgForm").count():
            return True
        return False

    def login():
        if pg.locator("#lgForm").count():
            pg.locator("#lgForm button").first.click()
            pg.wait_for_timeout(1800)

    def tab(label: str):
        pg.get_by_text(label, exact=False).first.click()
        pg.wait_for_timeout(1500)

    has_gate = enter()
    if has_gate:
        s.shot("10_login", note="접속 화면")
    login()

    # ① 맵 설정
    tab("① 맵 설정")
    s.shot("11_map_overview", note="① 맵 설정 전체")
    s.shot("12_floor_select", "header, .topbar, nav", "층 선택")
    for name, label, note in (("13_scale", "축척 2점", "축척 지정"),
                              ("14_zone", "구역 IDR", "구역 드로잉"),
                              ("15_bottleneck", "병목 CBS", "병목 드로잉"),
                              ("16_exit", "출입구 SEI", "출입구 드로잉"),
                              ("17_route", "피난경로 EPFI", "경로 드로잉"),
                              ("18_graph", "그래프 IDR", "공간 그래프")):
        try:
            pg.get_by_text(label, exact=False).first.click(); pg.wait_for_timeout(700)
        except Exception:
            pass
        s.shot(name, note=note)
    try:
        pg.locator("#drawCancel").click(); pg.wait_for_timeout(400)
        pg.locator("#msTabSet").click(); pg.wait_for_timeout(600)
    except Exception:
        pass
    s.shot("19_thresholds", "aside.side", "판정 임계값")
    try:
        pg.locator("#msTabHelp").click(); pg.wait_for_timeout(600)
    except Exception:
        pass
    s.shot("20_metric_help", "aside.side", "지표 설명")
    try:
        pg.locator("#msTabSet").click(); pg.wait_for_timeout(400)
    except Exception:
        pass

    # ② 카메라 등록·매핑
    tab("② 카메라 등록·매핑")
    s.shot("21_cams_overview", note="② 카메라 등록·매핑 전체")
    s.shot("22_cam_list", "aside.side.left", "카메라 목록")
    try:
        pg.locator("#camBulkOpen").click(); pg.wait_for_timeout(700)
        pg.locator("#bulkText").fill(
            "1층 로비,rtsp://172.29.0.11:554/trackID=1&streamID=2\n"
            "투썸 앞,rtsp://172.29.0.11:554/trackID=2&streamID=2\n"
            "뚜레쥬르 앞,rtsp://172.29.0.11:554/trackID=3&streamID=2")
        s.shot("23_bulk_paste", "#bulkModal .modalbox", "일괄 등록 — 붙여넣기")
        pg.locator("#bulkParse").click(); pg.wait_for_timeout(800)
        s.shot("24_bulk_table", "#bulkModal .modalbox", "일괄 등록 — 확인·편집")
        pg.locator("#bulkClose").click(); pg.wait_for_timeout(500)
    except Exception as e:
        print(f"  ✗ 일괄 등록 모달: {type(e).__name__}")
    try:
        pg.locator(".camrow").first.click(); pg.wait_for_timeout(3000)
        s.shot("25_mapping", note="대응점 지정")
        pg.get_by_text("유효 ROI", exact=False).first.click(); pg.wait_for_timeout(600)
        s.shot("26_roi", note="유효 ROI")
        pg.get_by_text("대응점", exact=False).first.click(); pg.wait_for_timeout(400)
    except Exception as e:
        print(f"  ✗ 매핑 화면: {type(e).__name__} — 매핑된 카메라가 있어야 캡처됨")

    # 전 카메라 커버리지·공간그래프 오버레이 (② 매핑 상태 한눈에 보기)
    try:
        for label in ("커버리지", "공간그래프"):
            btn = pg.get_by_text(label, exact=False).first
            if btn.count():
                btn.click(); pg.wait_for_timeout(500)
        s.shot("27_coverage_overlay", note="전 카메라 커버리지 오버레이")
    except Exception as e:
        print(f"  ✗ 27_coverage_overlay: {type(e).__name__}")

    # ③ 운영 뷰
    tab("③ 운영 뷰")
    pg.wait_for_timeout(3000)
    s.shot("30_live", note="③ 운영 뷰 전체")
    s.shot("31_live_map", "#liveCv", "평면도 표시 영역")
    s.shot("33_metric_rt", "#liveSide", "실시간 지표 패널")
    # 건물 훈련 패널(세션 시작 전 — 층별 경보 발생원 현황·건물 전체 경보 버튼)
    s.shot("35_building_drill_panel", note="건물 훈련 패널(층별 경보 발생원 현황)")

    if do_session:
        try:
            api("/api/session")           # 이미 진행 중이면 그대로 사용
        except Exception:
            api("/api/session/start", "POST", {"origin": [723, 1070]})
            print("  · 예시 평가 세션 시작")
        enter(); login(); tab("③ 운영 뷰")
        time.sleep(45)                    # 지표가 채워질 시간
        pg.wait_for_timeout(1500)
        try:
            pg.locator("#pmSess").click(); pg.wait_for_timeout(1500)
        except Exception:
            pass
        s.shot("34_metric_session", "#liveSide", "세션 중 4대 지표")
        s.shot("35_session_running", note="평가 세션 진행 중")
        res = api("/api/session/stop", "POST")
        print(f"  · 세션 종료: {res['session_id']}")
        pg.wait_for_timeout(3000)
        s.shot("36_session_result", note="세션 종료 결과")
        try:
            s.shot("37_result_modal", "#resultModal .modalbox", "평가 결과 상세")
        except Exception:
            pass

    # ④ 리플레이 (다층 사이트는 기본이 '건물 훈련' 모드)
    tab("④ 리플레이")
    pg.wait_for_timeout(2500)
    s.shot("40_replay", note="④ 리플레이 (건물 훈련 모드)")
    # 건물 훈련: 이력 선택 → 층별 재생 + 롤업 리포트
    try:
        if pg.locator("#rpModeDrill").count():
            pg.locator("#rpModeDrill").click(); pg.wait_for_timeout(1200)
        pg.locator(".rpsess").first.click(); pg.wait_for_timeout(3500)
        s.shot("42_replay_drill", note="건물 훈련 재생 — 층 선택·건물 지표")
        if pg.locator("#rpReport").count():
            pg.locator("#rpReport").click(); pg.wait_for_timeout(900)
            s.shot("43_drill_report", note="건물 훈련 롤업 리포트")
            if pg.locator("#resClose").count():
                pg.locator("#resClose").click(); pg.wait_for_timeout(400)
    except Exception as e:
        print(f"  ✗ 42/43 건물 훈련: {type(e).__name__} — 저장된 건물 훈련이 있어야 캡처됨")
    # 개별 층 세션 재생
    try:
        if pg.locator("#rpModeSess").count():
            pg.locator("#rpModeSess").click(); pg.wait_for_timeout(1200)
        pg.locator(".rpsess").first.click(); pg.wait_for_timeout(2500)
        s.shot("41_replay_play", note="세션 재생")
    except Exception:
        print("  ✗ 41_replay_play: 재생할 개별 층 세션이 없음")
    b.close()
    return s


def main() -> int:
    ap = argparse.ArgumentParser(description="가이드 스크린샷 캡처")
    ap.add_argument("--out", required=True, type=Path, help="출력 img 디렉토리")
    ap.add_argument("--only", choices=["8900", "8910"], help="한쪽만 캡처")
    ap.add_argument("--skip-session", action="store_true",
                    help="평가 세션 예시 캡처를 건너뜀(세션 파일 생성 없음)")
    a = ap.parse_args()

    out = a.out if a.out.is_absolute() else Path.cwd() / a.out
    out.mkdir(parents=True, exist_ok=True)
    print(f"출력: {out}\n")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright가 필요합니다: pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 2

    total, fails = 0, []
    with sync_playwright() as pw:
        if a.only != "8900":
            s = capture_editor(pw, out); total += s.n; fails += s.fail
        if a.only != "8910":
            s = capture_main(pw, out, not a.skip_session)
            total += s.n; fails += s.fail

    print(f"\n총 {total}장 캡처")
    if fails:
        print(f"실패 {len(fails)}건: {', '.join(fails)}")
        print("원고의 해당 @img 블록은 빌드 시 경고와 함께 건너뜁니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
