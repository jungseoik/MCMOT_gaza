# 09 · RTSP 라이브 모드

파일 업로드 대신 **RTSP/라이브 스트림**을 받아 실시간 분석하는 모드. 파일 모드를
갈아엎지 않고 **분기로 추가**했다 — 추적·속도·대시보드·맵·UI 셸은 그대로 재사용.

## 핵심 아이디어

모델 최대 처리속도(≈16fps)가 소스 fps(25~30)보다 느리므로, **밀린 프레임은 버리고
최신 프레임만** 처리한다(real-time). 녹화·반복재생 없이 라이브만, **정지 버튼**으로 종료.

```
RTSP URL → 리더 스레드(항상 최신 프레임 보유) → 메인이 최신 프레임만 추론 →
           오버레이 + 대시보드/맵 (라이브) → /stop 또는 연결 끊김 시 종료
```

## 흐름 / 엔드포인트

1. `POST /rtsp {url}` — `cv2.VideoCapture(url)`로 열고 첫 프레임 grab → `{job_id, w, h,
   first_frame, live:true}`. (ROI/보정/Depth 세팅을 첫 프레임에 그대로 적용 가능)
2. `POST /start/{id}` — 보정 모드 설정 후 워커 기동(파일과 동일 엔드포인트, `job.live`로 분기).
3. `POST /stop/{id}` — `job.stop=True` → 워커 루프 종료 → status `stopped`.
4. `GET /status/{id}` — `live:true`, status는 `processing`↔`stopped`. `total=0`(무한).
5. `GET /stream/{id}` — 라이브 MJPEG(완료 후 루프 단계 없음).

## 코드 요점

**프레임 획득 (`src/inference_gpu.py` `stream(live=True)`):** 리더 스레드가 `cap.read()`로
항상 최신 프레임을 보유(`buffersize=1`), 메인 제너레이터는 새 프레임(seq 증가)만 골라 처리.

```python
def _reader():
    try:
        while not latest["stop"]:
            ok, fr = cap.read()
            if not ok: latest["stop"] = True; break
            latest["frame"] = fr; latest["seq"] += 1
    finally:
        cap.release()         # ★ 리더가 cap을 소유·해제 (release 경합 segfault 방지)
```

**라이브 워커 (`webui/server.py` `_worker_live`):** mp4/replay 없이 프레임마다
`SpeedEstimator.update(time.monotonic(), targets)` → 오버레이 → 라이브 큐 push + `job.metrics`.
속도 dt가 **실제 시계**라 프레임 스킵에도 정확(→ [08](08-speed-and-calibration.md)).

**잡 자동 정지 (`_stop_others`):** 라이브 잡은 스스로 끝나지 않고, 파일 잡도 영상 끝까지
모델 lock을 쥔다. 뷰어가 탭만 닫거나 새로고침하면 워커가 백그라운드로 계속 돌며 lock을
독점 → 다음 잡이 영원히 큐에 막혀 **검은 화면**이 된다. 그래서 단일 사용자 백스톱으로,
**새 소스/잡 시작(`/upload`·`/rtsp`·`/start`) 시 다른 모든 실행 중 잡(파일·라이브 무관)을
정지**시켜 lock을 풀고, 완료(`done`)된 잡의 replay 버퍼도 비워 메모리를 회수한다.

```python
def _stop_others(keep_id=None):
    for jid, j in list(_jobs.items()):
        if jid == keep_id:
            continue
        if j.status in ("queued", "processing"):
            j.stop = True               # 파일/라이브 모두 즉시 중단
        elif j.status == "done":
            j.replay_frames = []        # 완료 잡의 replay JPEG/지표 해제
            j.replay_metrics = []
```

> 함수명이 이전 `_stop_other_live`(라이브 전용)에서 `_stop_others`로 바뀌었고, `j.live`
> 게이트가 제거되어 **파일 잡도** 회수된다. 프론트는 `beforeunload`→`sendBeacon('/stop')`
> 와 `change source`→`/stop`으로 GPU를 즉시 반납하고, 못 잡아도 위 백스톱이 다음 소스
> 진입 때 정리한다. 중단된 잡은 `stopped` 상태가 되고 부분 결과물/replay는 폐기되어
> `/result`가 409를 반환한다. (테스트: `webui/tests/test_stop_recovery.py`)

## 해결한 함정 (검증 중 발견)

- **segfault**: 종료 시 메인이 `cap.release()` 하는데 리더가 아직 `cap.read()` 중 →
  use-after-free. → 리더가 cap 소유·해제하도록 변경.
- **0프레임 처리**: 라이브 루프 초기 `last_seq=-1`에서 리더가 첫 프레임 채우기 전
  `frame=None`이면 즉시 break. → `last_seq=0`으로 시작해 실프레임까지 대기.
- **lock 독점**: 위 자동 정지로 해결.

## 한계 (정직하게)

- **재연결 없음**: 스트림이 끊기면 잡이 `stopped`로 끝남(자동 재연결 미구현).
- **뷰어 종료 ≠ 잡 종료**: 프론트 `beforeunload`→`sendBeacon('/stop')`으로 새로고침·탭
  종료 시 GPU를 즉시 반납한다. sendBeacon이 유실돼도 다음 소스 진입 때 `_stop_others`
  백스톱이 정리하므로 lock이 영구 점유되지 않는다.
- **단일 잡 직렬**: 모델 lock으로 한 번에 하나. 동시 다채널은 트래커 인스턴스 분리·GPU 분산 필요.
- RTSP 일반 이슈(TCP/UDP·지연·코덱)는 cv2 기본 동작에 의존.

## 검증

실제 스트림(`rtsp://…:8554/sample1`)으로: 연속 처리(proc 계속 증가)·50~61명 추적·정지·
새 잡 시작 시 기존 자동정지 확인. (px/s 정상값 — 실시간 소스라 dt 정확)
