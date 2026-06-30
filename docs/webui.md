# 실시간 추적 웹 UI (`webui`)

**비디오 파일 또는 RTSP 스트림**을 입력받아 TRT 추적 결과를 실시간으로 보여주고,
객체별 속도(km/h)·밀도·체류·가속도를 대시보드로 표출하는 독립형 웹 UI입니다.
파일은 완료 후 결과를 무한 루프 재생합니다.

핵심 코어 파이프라인(`src/`)과 분리되어 있고 기존 코드를 **재사용만** 합니다 —
README 워크플로(`python -m src.inference_gpu` 등)는 바뀌지 않습니다.

> 이 문서는 **사용법**입니다. 내부 구현/설계(속도 공식, 보정, RTSP, 맵, Depth,
> 재현 가이드)는 **[webui-dev/](webui-dev/)** 를 보세요.

## 0. 사용 흐름

1. **입력**: 파일 업로드 **또는** RTSP 주소 "연결"(샘플 스트림 바로가기 제공). RTSP는 정지 버튼으로 종료.
2. **세팅**: 분석 종류 선택
   - **기본 시각화(다운로드)**: 파일 전용 — ID+박스만, 결과 H.264 저장·다운로드 (RTSP에선 안 보임)
   - **속도/밀도**: ROI(4점) + 속도 보정(없음/보정선 2점/ROI 실측 4점/**Depth 자동**/**지도 정합 N점**) + **정렬 방향(선택, 2점)** — 그리면 방향성 정렬도 표출(opt-in)
     - **지도 정합(N점)**: 실제 지도 이미지 업로드 → CCTV↔지도 N점 대응(호모그래피) + 축척(2점+실거리) → 사람을 **지도 위에** 표시(km/h). 정렬방향·in/out 통과선을 선택 애드온으로 함께. ([webui-dev/14](webui-dev/14-map-registration.md))
   - **인·아웃 카운팅**: 선 2점 + 안쪽 1점 클릭 (선분/무한선 토글)
3. **분석**:
   - 속도/밀도 → 영상(추적+속도) + 대시보드, 상단 **영상/맵 토글**(top-down 점+방향벡터). 정렬 방향을 그렸으면 **정렬도 카드** + 맵 정렬색·기준 벡터
   - 카운팅 → 영상(라인+IN방향) + **IN/OUT/재실** 대시보드(음수면 경보)

보정·속도 공식은 [webui-dev/08](webui-dev/08-speed-and-calibration.md),
RTSP는 [09](webui-dev/09-rtsp-live.md), 맵은 [10](webui-dev/10-map-view.md),
인·아웃 카운팅은 [11](webui-dev/11-in-out-counting.md),
기본 시각화(다운로드)는 [12](webui-dev/12-basic-viz-download.md), Depth는 [07](webui-dev/07-depth-mode.md),
방향성 정렬도는 [13](webui-dev/13-alignment.md), 지도 정합은 [14](webui-dev/14-map-registration.md).

---

## 1. 설치

코어 환경이 먼저 준비돼 있어야 합니다 (최상위 `README.md` 참조). 그 위에
웹 전용 의존성만 추가로 설치합니다.

```bash
pip install -r webui/requirements.txt    # fastapi, uvicorn, python-multipart
```

TRT 엔진도 빌드돼 있어야 합니다.

```bash
python -m src.build_trt --fp16
```

## 2. 실행

```bash
python -m webui                  # http://localhost:8000
python -m webui --port 9000      # 포트 변경
python -m webui --host 0.0.0.0   # 외부 접속 허용 (기본값)
python -m webui --reload         # 개발용 자동 리로드
```

브라우저에서 접속 → 비디오 드롭/업로드 → 분석 설정(ROI·보정·모드) → **`측정 시작 →`** 클릭.

---

## 3. 동작 원리

### 3.1 하이브리드 표시 (라이브 → 루프)

| 단계 | 소스 | 표시 |
|------|------|------|
| **처리 중** | 추론 워커가 프레임마다 생산 | MJPEG 라이브 스트림 (≈추론 속도) |
| **완료 후** | 처리 중 만들어 둔 JPEG 프레임 | 같은 `<img>`에서 source fps로 무한 루프 |

화면에는 단 하나의 `<img>` 요소만 씁니다. 처리 중에는 라이브 프레임을,
완료 후에는 동일 스트림이 결과를 루프 재생하도록 **서버 한 엔드포인트가
두 단계를 이어서** 내보냅니다.

### 3.2 왜 `<video>`가 아니라 MJPEG인가

OpenCV `VideoWriter`는 `mp4v`(MPEG-4 Part 2) 코덱으로 저장하는데, 브라우저
`<video>`는 이 코덱을 디코딩하지 못합니다(브라우저는 H.264/avc1만 재생).
그래서 `<video>`로 띄우면 첫 프레임에서 멈춥니다.

MJPEG 방식은 서버가 프레임을 **JPEG로** 내보내고 브라우저는 `<img>`로
받기만 하므로, 영상 코덱에 의존하지 않습니다. ffmpeg/H.264 트랜스코딩이
필요 없습니다.

> 트레이드오프: 재생 내내 서버가 스트리밍하므로 (a) 탐색바/스크럽이 없고
> (b) 지속 대역폭을 씁니다. 시킹 가능한 진짜 비디오/다운로드가 필요하면
> `conda install -c conda-forge ffmpeg` 후 H.264로 트랜스코딩해
> `<video>`로 서빙하는 방식으로 확장할 수 있습니다.

### 3.3 프레임 파이프라인

```
업로드 → 워커 스레드 (model.stream())
            ├─ writer.write(frame)            # 원본 해상도 mp4 저장 (다운로드용)
            ├─ resize → JPEG 인코딩
            │     ├─ job.replay_frames.append() # 전 프레임 보관 (루프 재생용)
            │     └─ live queue.put()            # 라이브 뷰 (뒤처지면 드롭)
            └─ ...
```

- **재인코딩 0회**: 루프 재생은 처리 중 만들어 둔 JPEG 리스트를 그대로
  순환합니다. 매 루프마다 mp4를 다시 디코딩/인코딩하지 않습니다.
- **라이브 큐 드롭**: 라이브 뷰어가 뒤처지면 가장 오래된 프레임을 버립니다
  (mp4와 `replay_frames`에는 모든 프레임이 남아 있어 손실 없음).

### 3.4 페이싱 (정확한 fps)

스트림 엔드포인트는 **async 제너레이터**이고, 블로킹 호출(`queue.get`)은
스레드로 넘기며, 재생 페이싱은 `asyncio.sleep`으로 합니다. 고정 스케줄
(`next_t += 1/fps`)을 써서 프레임당 전송 시간이 더해지지 않고 흡수되도록 해
**source fps를 정확히** 유지합니다.

> 동기 제너레이터 + `time.sleep`을 스레드풀에서 돌리면 프레임당 페이싱이
> 깨져 재생이 크게 느려집니다. async + `asyncio.sleep`이 정석입니다.

### 3.5 스트림 화질/속도 튜닝

`webui/server.py` 상단 상수로 조절합니다.

```python
STREAM_MAX_WIDTH = 854   # 이보다 넓은 프레임은 다운스케일 (스트림 전용)
JPEG_QUALITY = 72
```

프레임 JPEG이 너무 크면 전송이 병목이 되어 fps가 떨어집니다. 측정 예시
(960×540, 단일 로컬 뷰어):

| 설정 | 프레임 크기 | 재생 fps |
|------|------------|---------|
| 960폭 / q80 | ~173 KB | ~20 fps |
| 854폭 / q72 | ~108 KB | **25 fps** |
| 480폭 / q50 | ~31 KB | 25 fps (여유 큼) |

원격(인터넷) 송출이면 폭/품질을 더 낮춰 대역폭을 줄이세요. **저장되는
mp4는 이 설정과 무관하게 원본 해상도**를 유지합니다.

---

## 4. HTTP 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET`  | `/` | UI 페이지 |
| `POST` | `/upload` | 비디오 파일 업로드 → `{job_id, 첫프레임}` |
| `POST` | `/rtsp` | RTSP 소스 열기 → `{job_id, 첫프레임, live}` |
| `POST` | `/start/{job_id}` | ROI·보정·모드 설정 후 추론 시작 |
| `POST` | `/stop/{job_id}` | 라이브 잡 정지 |
| `POST` | `/prepare_depth/{job_id}` | (Depth) 깊이 분석 + 미리보기 생성 |
| `GET`  | `/depthvis/{job_id}` | Depth 컬러 미리보기(PNG) |
| `GET`  | `/status/{job_id}` | 진행률/상태/지표 JSON |
| `GET`  | `/stream/{job_id}` | MJPEG (라이브 → 파일은 완료 시 루프) |
| `GET`  | `/metrics_all/{job_id}` | 프레임별 지표(파일 재생 동기화) |
| `GET`  | `/result/{job_id}?download=1` | 완성 mp4(기본 시각화 모드는 H.264로 트랜스코딩). `done` 상태에서만 제공(아니면 409) |

---

## 5. 동작 제약 (현재 버전)

- **잡 직렬 처리**: 트래커가 영상별 상태를 가지므로 동시 1건만 추론합니다
  (`_model_lock`). 모델/엔진은 서버 시작 시 1회 로드. GPU가 2장이므로,
  트래커 인스턴스를 분리하면 동시 다중 처리로 확장 가능합니다.
- **단일 뷰어 가정**: 라이브 큐가 단일 소비자라, 같은 job을 여러 탭에서
  동시에 보는 라이브 단계는 지원하지 않습니다 (완료 후 루프는 다중 접속 가능).
- **메모리**: `replay_frames`가 job별로 모든 JPEG을 메모리에 보관합니다
  (예: 646프레임 × ~108KB ≈ 70MB). 업로드가 누적되면 메모리도 누적됩니다.

---

## 6. 코어 코드 변경점

이 UI를 위해 코어에서 바뀐 것은 **`src/inference_gpu.py`에 `stream()`
제너레이터를 추가**하고 `run()`이 이를 재사용하도록 리팩터한 것뿐입니다.
`run()`의 외부 동작과 README 워크플로는 동일합니다.

```python
for item in model.stream(video_path):
    # item = {"index", "total", "fps", "width", "height", "frame"(BGR)}
    ...
```
