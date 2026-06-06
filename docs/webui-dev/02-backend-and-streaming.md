# 02 · 백엔드 & 스트리밍

`webui/server.py` 한 파일. FastAPI 앱 + 인메모리 Job + 워커 스레드.

## 엔드포인트

| 메서드 | 경로 | 설명 | 반환 |
|--------|------|------|------|
| GET | `/` | 단일 페이지 | `index.html` (매 요청 read → 수정 시 재시작 불필요) |
| POST | `/upload` | 비디오 저장 + 첫 프레임 | `{job_id, width, height, first_frame(dataURL)}` |
| POST | `/start/{id}` | ROI·보정 받고 워커 기동 | `{job_id, roi, pixels_per_meter}` |
| GET | `/status/{id}` | 진행률 + 최신 지표 | `{status, processed, total, fps, error, metrics}` |
| GET | `/stream/{id}` | MJPEG 라이브→루프 | `multipart/x-mixed-replace` |
| GET | `/metrics_all/{id}` | 프레임별 지표 전체 | `{fps, total, frames:[metrics,...]}` |
| GET | `/result/{id}` | 결과 mp4(원본 해상도) | `video/mp4` (VLC 등 재생/다운로드용) |
| GET | `/static/*` | 디자인 자산 | `StaticFiles` 마운트 |

## Job 모델 (인메모리)

```python
class Job:
    id, input_path, output_path
    queue            # 라이브 MJPEG용 JPEG 큐 (maxsize=128, 가득 차면 오래된 것 드롭)
    replay_frames[]  # 모든 JPEG (루프 재생용)
    replay_metrics[] # 프레임별 metrics (재생 동기화용)
    status           # uploaded|queued|processing|done|error
    processed,total,fps,error
    roi, ppm         # /start에서 설정
    metrics          # 최신 프레임 지표 스냅샷
```

- `_jobs: dict[str, Job]` 전역 레지스트리. (멀티프로세스 아님 → uvicorn 워커 1개 가정)
- 모델/TRT 엔진은 `@app.on_event("startup")`에서 **1회 로드**(`_model`).

## 동시성 — 왜 직렬화하나

`BoostTrack` 트래커는 **영상별 가변 상태**(`trackers`, `frame_count`, ID 카운터)를
들고 있어 한 인스턴스를 두 잡이 동시에 돌리면 상태가 섞인다. 그래서:

```python
_model_lock = threading.Lock()
def _worker(job):
    with _model_lock:        # 잡 직렬: 한 번에 하나만 추론
        job.status = "processing"
        for item in _model.stream(job.input_path, draw=False):
            ...
```

GPU 추론은 블로킹이라 워커는 `threading.Thread(daemon=True)`로 띄운다(C/CUDA 구간에서
GIL 해제됨). 동시 다중 사용자가 필요하면 트래커 인스턴스를 잡마다 분리하고 GPU 2장에
분산하면 되지만, 현재는 단순·안전 우선으로 직렬.

## 워커 한 프레임 처리

```python
for item in _model.stream(job.input_path, draw=False):
    if est is None:                      # 첫 프레임에 lazy init
        job.fps = item["fps"] or 25.0
        est = SpeedEstimator(job.fps, job.ppm, job.roi,
                             frame_size=(item["width"], item["height"]))
        writer = cv2.VideoWriter(..., fourcc("mp4v"), job.fps, (w,h))
    present = est.update(item["index"], item["targets"])   # 객체별 속도
    frame   = annotate(item["frame"], item["targets"], present, est)  # 오버레이
    writer.write(frame)                  # 결과 mp4 (원본 해상도)
    jpg = _encode(frame)                 # 다운스케일 + JPEG
    job.replay_frames.append(jpg); _push(job, jpg)   # 루프용 + 라이브용
    m = est.metrics(present)
    job.metrics = m; job.replay_metrics.append(m)
    job.processed = item["index"]
```

## MJPEG 스트리밍 — 라이브 → 루프 (핵심)

`/stream/{id}`는 **async 제너레이터** 하나로 두 단계를 이어서 보낸다.

```python
async def gen():
    # Phase 1: 처리 중 — 큐에서 프레임을 받아 흘림 (블로킹 get은 스레드로)
    if job.status in ("uploaded","queued","processing"):
        while True:
            data = await asyncio.to_thread(job.queue.get)
            if data is None: break            # 워커가 끝에 sentinel push
            yield boundary + data + b"\r\n"
    # Phase 2: 완료 — 미리 인코딩된 프레임을 source fps로 무한 루프
    if job.status == "done" and job.replay_frames:
        delay = 1.0/(job.fps or 25)
        next_t = time.monotonic(); i = 0
        while True:
            yield boundary + job.replay_frames[i] + b"\r\n"
            i = (i+1) % len(job.replay_frames)
            next_t += delay
            s = next_t - time.monotonic()
            await asyncio.sleep(s) if s>0 else (next_t := time.monotonic())
```

**왜 async + `asyncio.sleep`인가**: 동기 제너레이터 + `time.sleep`을 `StreamingResponse`
스레드풀에서 돌리면 프레임당 페이싱이 깨져 재생이 4fps로 떨어졌다. async + `asyncio.sleep`
+ **고정 스케줄**(`next_t += delay`)로 전송 시간을 흡수해 source fps를 유지한다. (06 참고)

**왜 재인코딩 안 하나**: 루프는 처리 중 만들어 둔 `replay_frames`(JPEG)를 그대로 순환한다.
매 루프 mp4를 다시 디코딩/인코딩하지 않아 CPU를 거의 안 쓴다.

## 스트림 화질/속도 튜닝

```python
STREAM_MAX_WIDTH = 854   # 이보다 넓은 프레임은 다운스케일(스트림 전용)
JPEG_QUALITY     = 72
```

프레임 JPEG이 크면 **전송이 병목**이 되어 fps가 떨어진다(측정: 173KB→~20fps,
108KB→25fps). 저장되는 mp4는 이 설정과 무관하게 **원본 해상도** 유지.

## 라이브 큐 드롭 정책

`_push()`는 논블로킹. 뷰어가 느려 큐(128)가 차면 **가장 오래된 프레임을 버린다**
(라이브는 최신성이 중요). `replay_frames`와 mp4에는 모든 프레임이 남아 손실 없음.
