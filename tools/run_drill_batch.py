#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""리허설 시나리오를 연속으로 훈련 실행해 세션을 녹화한다.

UI(⑤ 리허설 [준비] → ③ 운영 뷰 [🎬 리허설 훈련 시작])와 **같은 순서**로 호출한다.
  1) /api/vsource/standby   카메라 부착 (파일 모드는 첫 프레임 정지)
  2) /api/vsource/start     t=0 재생 시작
  3) /api/drill/start       경보 세션 — t_alarm 은 서버가 filesrc 가상시각으로 잡는다
  4) 재생이 끝나면 서버가 자동으로 세션을 닫는다(_on_rehearsal_done)

    python tools/run_drill_batch.py --prefix "대피경로" --dry
    python tools/run_drill_batch.py --prefix "대피경로"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8900"
PKG = "pkg:aihub-drill-1f3f"

# 각본 이름 — 실행 로그에만 쓴다. 세션 라벨은 "대피경로 NN" 으로 짧게 간다
# (④ 리플레이 목록이 한 줄로 읽혀야 한다 — 긴 설명은 여기 표와 보고서에 있다).
NAMES = {
    "scenario_01": "IDR 권장경로 2명+우측 우회 2명",
    "scenario_02": "IDR 매우 느린 속도",
    "scenario_03": "IDR 2초 멈췄다 이동",
    "scenario_04": "EPFI 2명 완전 빙 돌아",
    "scenario_05": "EPFI 2명 시작점 복귀 후 재진입",
    "scenario_06": "EPFI 1명 빙빙 돌기",
    "scenario_07": "CBS 2열 진입(밀도 아님)",
    "scenario_08": "CBS 문 근처 밀집 2회(밀도)",
    "scenario_09": "CBS 5명+중간 5명 합류(밀도 아님)",
    "scenario_10": "SEI 7:3 분산",
    "scenario_11": "SEI 7:3 + 2명 재진입(7+2=9)",
    "scenario_12": "1층 출구 나가기",
    "scenario_13": "1층 출구→흡연장",
    "scenario_14": "1층 반반 나눠 나가기",
}
ORIGINS = {"floor4": [[775.0, 229.0]], "floor5": [[1040.0, 1040.0]]}


def req(path, body=None, method=None, timeout=180):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return json.load(f)


def session_live(floor: str) -> bool:
    """그 층에 진행 중인 평가 세션이 있나. /api/session 은 없으면 404 를 준다."""
    try:
        req(f"/api/session?floor={floor}", timeout=10)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise
    except Exception:
        return True          # 알 수 없으면 아직 도는 것으로 본다(성급한 다음 실행 방지)


def wait_idle(floors, timeout=400):
    """훈련이 끝나 그 층들의 세션이 모두 닫힐 때까지.

    /api/status 에는 드릴 상태가 없다 — 없는 키를 보면 즉시 idle 로 오판해
    다음 시나리오가 409(세션 진행 중)로 막힌다. 층별 /api/session 으로 본다.
    """
    t0 = time.time()
    seen_live = False
    while time.time() - t0 < timeout:
        live = any(session_live(f) for f in floors)
        if live:
            seen_live = True
        elif seen_live:
            return True                      # 떴다가 닫혔다 = 정상 종료
        time.sleep(2)
    return False


def force_stop():
    """남아 있는 드릴·송출을 내린다. 이미 없으면 조용히 넘어간다."""
    for path in ("/api/drill/stop", "/api/vsource/stop"):
        try:
            req(path, {})
        except Exception:
            pass


def run_one(scen: str, prefix: str, dry: bool) -> str | None:
    sid = f"{PKG}:{scen}"
    label = f"{prefix} {scen[-2:]}"
    if dry:
        print(f"  [dry] {sid}  →  {label} · {NAMES.get(scen, '')}")
        return None
    st = req("/api/vsource/standby", {"scenario_id": sid})
    floors = st.get("floors") or []
    time.sleep(3)
    pl = req("/api/vsource/start", {"scenario_id": sid})
    # 경보는 **클립 t=0**(= 응답의 alarm_at)에 건다. 예전처럼 start 뒤 sleep 하고
    # 서버가 가상시각으로 잡게 두면, 파일 재생이 NODROP 으로 벽시계보다 1.6~2.3배
    # 빨라 클립이 이미 0.9~8.0s 흘러간 뒤에 경보가 걸린다(실측 14건). 그러면
    #   · 그리드 영상이 도면보다 그만큼 뒤처지고
    #   · 개시 지연이 시나리오마다 다른 기준에서 재져 서로 비교가 안 된다.
    t_alarm = pl.get("alarm_at")
    fo = {f: ORIGINS[f] for f in floors if f in ORIGINS}
    if not fo:
        print(f"  ! {scen}: 경보 원점 없는 층 {floors} — 건너뜀")
        req("/api/vsource/stop", {}) if True else None
        return None
    body = {"floor_origins": fo, "floors": floors, "label": label}
    if t_alarm:
        body["t_alarm"] = float(t_alarm)      # 클립 0프레임 = 경보 (v1 녹화와 동일)
    d = req("/api/drill/start", body)
    sess = d.get("session_id") or d.get("id")
    ok = wait_idle(floors)
    if not ok:
        force_stop()                        # 타임아웃이면 강제로 닫고 다음으로
    print(f"  {'ok  ' if ok else 'TIMEOUT'} {scen}  {label} · {NAMES.get(scen, '')}"
          f"  → {sess}", flush=True)
    time.sleep(3)
    return sess


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="대피경로", help="세션 라벨 앞머리")
    ap.add_argument("--only", help="쉼표로 시나리오 지정 (예: scenario_01,scenario_02)")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    scens = ([s.strip() for s in a.only.split(",")] if a.only
             else [f"scenario_{i:02d}" for i in range(1, 15)])
    print(f"[batch] {len(scens)}건 · 라벨 앞머리 {a.prefix!r}")
    done = []
    for i, s in enumerate(scens, 1):
        print(f"[{i}/{len(scens)}] {s}", flush=True)
        try:
            if not a.dry:
                force_stop(); time.sleep(2)   # 앞 실행 잔여물 정리
            r = run_one(s, a.prefix, a.dry)
            if r:
                done.append(r)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            body = ""
            if isinstance(e, urllib.error.HTTPError):
                try: body = e.read().decode()[:200]
                except Exception: pass
            print(f"  FAIL {s}: {e} {body}", flush=True)
    print(f"\n[batch] 완료 — 세션 {len(done)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
