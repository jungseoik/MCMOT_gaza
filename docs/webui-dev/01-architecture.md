# 01 · 아키텍처

## 설계 원칙

1. **코어 재사용, 비침투**: 추적/추론 로직(`tracker/`, `src/`)은 건드리지 않고
   `src.inference_gpu.BoostTrackGPUInference`를 **그대로 사용**한다. 코어에 가한
   유일한 변경은 `stream()` 제너레이터 추가(아래 참고)로, 기존 `run()`/README
   워크플로 동작은 동일하다.
2. **자립형(self-contained)**: 외부 런타임 의존(React CDN, 빌드 스텝, DB, 메시지
   큐)을 두지 않는다. 순수 FastAPI + vanilla JS + 인메모리 상태.
3. **휘발성 OK**: 결과·지표는 프로세스 메모리에만 둔다. 영속화가 필요하면 그때
   붙인다(현재 불필요).

## 기술 스택과 선택 이유

| 영역 | 선택 | 이유 |
|------|------|------|
| 웹 서버 | **FastAPI + uvicorn** | async 스트리밍(`StreamingResponse`)·파일 업로드가 간결, 표준 |
| 실시간 영상 | **MJPEG** (`multipart/x-mixed-replace`) | 브라우저가 JPEG만 디코딩 → 영상 코덱 의존 0. `<img>` 하나로 표시. (mp4v는 브라우저 재생 불가 — 06 참고) |
| 프론트 | **vanilla HTML/CSS/JS** | 빌드/CDN 불필요, 오프라인 동작. 디자인은 외부 시안의 CSS만 이식 |
| 추론 | **기존 TRT 파이프라인** | `BoostTrackGPUInference` 재사용 |
| 속도/지표 | **자체 `SpeedEstimator`** | 슬라이딩 윈도우 px/s + km/h 보정으로 속도/지표 산출 |
| 동시성 | **스레드 + 락** | GPU 추론은 블로킹 → 워커 스레드. 트래커가 영상별 상태를 가져 잡은 직렬화 |

## 컴포넌트 구성도

```
브라우저 (index.html, vanilla JS)
  업로드 → ROI/보정 → 분석(영상 + 대시보드)
        │  POST /upload         │ GET /stream/{id}  (MJPEG)
        │  POST /start/{id}     │ GET /status/{id}  (폴링, 지표)
        │                       │ GET /metrics_all/{id} (재생 동기화)
        ▼                       ▼
FastAPI (webui/server.py)
  Job 레지스트리(_jobs)  ──  워커 스레드(_worker, _model_lock으로 직렬)
        │
        ├── BoostTrackGPUInference.stream(draw=False)   # src/inference_gpu.py
        │        └─ 프레임별 {frame, targets, ...} yield
        ├── SpeedEstimator.update(targets)              # webui/speed.py
        │        └─ 객체별 속도/체류 + 집계 지표
        ├── annotate(frame, ...)                        # 박스·속도라벨·ROI 그리기
        ├── VideoWriter → _data/outputs/{id}.mp4        # 결과 저장(원본 해상도)
        ├── JPEG 인코딩 → job.replay_frames[]           # 루프 재생용
        └── job.metrics / job.replay_metrics[]          # 대시보드(실시간/재생)
```

## 데이터 흐름 (한 잡의 일생)

1. **업로드** (`POST /upload`): 비디오 저장 + 첫 프레임을 base64 JPEG로 반환
   (클라이언트가 ROI/보정선을 그릴 캔버스 배경). 추론은 아직 시작 안 함.
2. **시작** (`POST /start/{id}`): `{roi, pixels_per_meter}`를 받아 워커 스레드 기동.
3. **워커 루프**: `model.stream()`이 프레임을 내놓을 때마다
   - `SpeedEstimator.update()` → 객체별 속도/체류 + 집계
   - `annotate()` → 박스·ID·`km/h`·ROI를 프레임에 그림
   - 결과 mp4에 write + JPEG 인코딩하여 ① 라이브 큐 push ② `replay_frames` 보관
   - `metrics`를 `job.metrics`(최신)와 `replay_metrics[]`(프레임별)에 저장
4. **라이브 표시**: `GET /stream/{id}`가 큐의 JPEG를 MJPEG로 흘림. 동시에
   `GET /status/{id}`를 600ms 폴링해 대시보드 갱신.
5. **완료**: 스트림은 `replay_frames`를 source fps로 **무한 루프**. 대시보드는
   `/metrics_all`로 프레임별 지표를 받아 **영상과 같은 인덱스로 동기 재생**.

## 입력 소스 — 파일 vs RTSP 라이브

- **파일 업로드**(`/upload`): 유한 영상 → 모든 프레임 처리 → mp4 저장 → 완료 후 결과를
  source fps로 무한 루프 재생 + 대시보드 프레임 동기 재생.
- **RTSP 라이브**(`/rtsp`): 무한 소스 → 최신 프레임만 처리(밀린 건 드롭) → 녹화·재생 없이
  라이브만, `/stop`으로 종료. 새 잡 시작 시 기존 라이브 잡 자동 정지(lock 해제).
  상세 → **[09-rtsp-live.md](09-rtsp-live.md)**.

두 경로 모두 `stream()`(파일/`live=True`)을 거쳐 추적·`SpeedEstimator`·`annotate`·
대시보드·맵을 동일하게 재사용한다.

## 화면(프론트 상태) 3단계

- **업로드/RTSP** → **ROI/보정 세팅** → **분석**. 라우터 없이 JS로 `.hidden` 토글.
- 좌측 네비 레일 + 상단바(PIA 로고)는 항상 표시(셸). 본문만 화면별로 교체.
- 분석 화면은 **영상 ↔ 2D 맵** 토글 지원 → **[10-map-view.md](10-map-view.md)**.

## 코어에 가한 단 하나의 변경

`src/inference_gpu.py`의 `stream()` 제너레이터:

```python
def stream(self, input_video, reset=True, draw=True):
    # ... 프레임마다 ...
    yield {"index","total","fps","width","height","frame","targets"}
```

- `draw=True`(기본): 프레임에 기본 ID 박스를 그려서 yield → `run()`이 사용(기존 동작 유지)
- `draw=False`: 원본 프레임 + `targets`만 yield → webui가 속도 라벨/ROI를 직접 그림

`run()`은 이 제너레이터를 소비하도록 리팩터되어 동작이 동일하다(중복 루프 없음).
