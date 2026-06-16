#!/usr/bin/env python3
"""작업 중단/정리(stop & cleanup) 동작 통합 테스트.

검은 화면 버그 수정과 그 연관 동작을 살아있는 서버에 대해 검증한다.
검증 항목:
  T1  백스톱: 파일 작업 처리 중 새 소스가 들어오면 옛 작업이 'stopped'로
      회수되고(=모델 락 해제), 새 작업이 막힘 없이 'processing'에 진입한다.
      (= "change source / 새로고침 후 같은 영상 재업로드 → 검은 화면" 수정)
  T2  파일 작업도 /stop 으로 즉시 중단된다(이전엔 불가). 중단 시 결과는
      다운로드 불가(/result 409).
  T3  완료된 작업의 replay 버퍼가 새 소스 진입 시 비워진다(메모리 해제).

사전 조건: webui 서버가 떠 있어야 함  ->  `python -m webui`
실행:
  python -m webui.tests.test_stop_recovery
  # 또는 경로/주소 지정
  BASE_URL=http://localhost:8000 \
  LONG_VIDEO=assets/sample1.mp4 SHORT_VIDEO=/tmp/clip_short.mp4 \
  python webui/tests/test_stop_recovery.py
"""
import os
import sys
import time

import requests

BASE = os.environ.get("BASE_URL", "http://localhost:8000")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LONG_VIDEO = os.environ.get("LONG_VIDEO", os.path.join(ROOT, "assets", "sample1.mp4"))
SHORT_VIDEO = os.environ.get("SHORT_VIDEO", "/tmp/clip_short.mp4")

_fails = []


def check(cond, msg):
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {msg}")
    if not cond:
        _fails.append(msg)
    return cond


def upload(path):
    with open(path, "rb") as f:
        r = requests.post(f"{BASE}/upload",
                          files={"file": (os.path.basename(path), f, "video/mp4")},
                          timeout=30)
    r.raise_for_status()
    return r.json()["job_id"]


def start_basic(job_id):
    r = requests.post(f"{BASE}/start/{job_id}", json={"basic": True}, timeout=30)
    r.raise_for_status()
    return r.json()


def stop(job_id):
    requests.post(f"{BASE}/stop/{job_id}", timeout=30)


def status(job_id):
    return requests.get(f"{BASE}/status/{job_id}", timeout=30).json()["status"]


def metrics_total(job_id):
    return requests.get(f"{BASE}/metrics_all/{job_id}", timeout=30).json()["total"]


def result_code(job_id):
    return requests.get(f"{BASE}/result/{job_id}", timeout=30).status_code


def wait_for(job_id, targets, timeout=40.0, poll=0.2):
    """Poll /status until it hits one of `targets` (set) or times out."""
    t0 = time.monotonic()
    last = None
    while time.monotonic() - t0 < timeout:
        last = status(job_id)
        if last in targets:
            return last, time.monotonic() - t0
        time.sleep(poll)
    return last, time.monotonic() - t0


def t1_backstop():
    print("T1  backstop: 처리 중 새 소스 진입 -> 옛 작업 회수 + 새 작업 진입")
    a = upload(LONG_VIDEO)
    start_basic(a)
    st, _ = wait_for(a, {"processing"}, timeout=20)
    check(st == "processing", f"작업 A가 processing 진입 (status={st})")

    # 새 소스 업로드 = change-source / 새로고침 후 재업로드 시나리오의 서버 측 트리거.
    # /upload 가 _stop_others() 를 호출해 A 를 회수해야 한다.
    b = upload(LONG_VIDEO)
    st_a, dt_a = wait_for(a, {"stopped"}, timeout=10)
    check(st_a == "stopped",
          f"새 소스 진입으로 A가 stopped 회수 (status={st_a}, {dt_a:.1f}s)")

    # 락이 풀렸으니 B 는 막힘 없이 처리에 들어가야 한다(= 검은 화면 없음).
    start_basic(b)
    st_b, dt_b = wait_for(b, {"processing", "done"}, timeout=10)
    check(st_b in ("processing", "done"),
          f"새 작업 B가 막힘 없이 진입 (status={st_b}, {dt_b:.1f}s) -> 검은 화면 없음")
    stop(b)
    wait_for(b, {"stopped", "done"}, timeout=10)


def t2_file_stop():
    print("T2  파일 작업 /stop 즉시 중단 + 결과 다운로드 차단")
    c = upload(LONG_VIDEO)
    start_basic(c)
    st, _ = wait_for(c, {"processing"}, timeout=20)
    check(st == "processing", f"작업 C가 processing 진입 (status={st})")

    stop(c)
    st_c, dt_c = wait_for(c, {"stopped"}, timeout=10)
    check(st_c == "stopped", f"/stop 으로 파일 작업 중단 (status={st_c}, {dt_c:.1f}s)")
    check(dt_c < 5.0, f"중단이 신속함 ({dt_c:.1f}s < 5s)")

    code = result_code(c)
    check(code == 409, f"중단된 작업은 다운로드 불가 (/result -> {code}, expect 409)")


def t3_replay_release():
    print("T3  완료 작업의 replay 버퍼 메모리 해제")
    if not os.path.exists(SHORT_VIDEO):
        check(False, f"짧은 클립 없음: {SHORT_VIDEO} (ffmpeg 로 25프레임 클립 생성 필요)")
        return
    d = upload(SHORT_VIDEO)
    start_basic(d)
    st, _ = wait_for(d, {"done"}, timeout=30)
    check(st == "done", f"짧은 작업 D가 done 도달 (status={st})")
    n0 = metrics_total(d)
    check(n0 > 0, f"완료 직후 replay 프레임 보유 (total={n0})")

    # 새 소스 진입 -> _stop_others 가 done 작업의 replay 버퍼를 비워야 한다.
    upload(SHORT_VIDEO)
    n1 = metrics_total(d)
    check(n1 == 0, f"새 소스 진입 후 D의 replay 해제 (total={n1}, expect 0)")


def main():
    print(f"server : {BASE}")
    print(f"long   : {LONG_VIDEO}")
    print(f"short  : {SHORT_VIDEO}\n")
    try:
        requests.get(f"{BASE}/", timeout=5)
    except Exception as e:
        print(f"서버에 연결할 수 없습니다 ({BASE}): {e}")
        print("먼저 `python -m webui` 로 서버를 띄우세요.")
        sys.exit(2)

    t1_backstop()
    t2_file_stop()
    t3_replay_release()

    print()
    if _fails:
        print(f"=== 실패 {len(_fails)}건 ===")
        for m in _fails:
            print(f"  - {m}")
        sys.exit(1)
    print("=== 전체 통과 ===")


if __name__ == "__main__":
    main()
