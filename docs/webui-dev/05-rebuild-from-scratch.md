# 05 · 처음부터 다시 만들기 (단계별 가이드)

코어 추론 파이프라인(`src/inference_gpu.py`, TRT 엔진)이 이미 동작한다는 전제에서,
`webui/`를 빈 상태부터 재현하는 순서. 각 단계 끝에 **검증 방법**을 둔다.

## 0. 전제

- `python -m src.build_trt --fp16` 완료(엔진 존재), `python -m src.inference_gpu` 동작
- 모델 가중치 다운로드 완료(`external/weights/`)

## 1. 의존성

```bash
# webui/requirements.txt
fastapi>=0.110
uvicorn[standard]>=0.29
python-multipart>=0.0.9
```
```bash
pip install -r webui/requirements.txt
```

## 2. 코어에 프레임 제너레이터 추가 (`src/inference_gpu.py`)

`run()`의 프레임 루프를 제너레이터 `stream()`으로 추출하고, `run()`이 그것을 소비하게
리팩터(동작 보존). `stream(input, reset=True, draw=True)`가 프레임마다
`{index,total,fps,width,height,frame,targets}`를 yield. `draw=False`면 박스 미그림(webui용).

**검증**: `for i,item in enumerate(model.stream('assets/sample1.mp4')):` 앞 5프레임 키 확인.
`python -m src.inference_gpu -i ... -o ...`가 여전히 동일 동작이면 OK.

## 3. 속도 모듈 (`webui/speed.py`)

`SpeedEstimator`(슬라이딩 윈도우 속도, ROI 필터, ppm 보정, 누적/가속/체류/밀도/레벨)와
`annotate()`(ROI+박스+속도라벨). 상세 산식은 [03](03-speed-and-metrics.md).

**검증**: 가짜 targets로 등속 객체를 먹여 km/h가 기대값인지(예: 5px/f·25fps÷50ppm×3.6=9.0)
단위 테스트. metrics 값이 순수 float/int인지 확인(JSON 직렬화).

## 4. FastAPI 서버 (`webui/server.py`)

- 시작 시 `BoostTrackGPUInference()` 1회 로드, `/static` 마운트
- `Job` 모델 + `_jobs` 레지스트리 + `_model_lock`
- 엔드포인트: `/`, `/upload`(첫프레임 반환), `/start/{id}`, `/status/{id}`,
  `/stream/{id}`(async, 라이브→루프), `/metrics_all/{id}`, `/result/{id}`
- 워커 스레드: `model.stream(draw=False)` → `SpeedEstimator` → `annotate` →
  mp4 write + JPEG(`replay_frames`/큐) + `metrics`/`replay_metrics`
- 스트림 페이싱은 **async + asyncio.sleep + 고정 스케줄**, 프레임은 **다운스케일+JPEG**

상세는 [02](02-backend-and-streaming.md). 진입점:
```python
# webui/__main__.py
import uvicorn; uvicorn.run("webui.server:app", host="0.0.0.0", port=8000)
```

**검증**(서버 띄운 뒤):
```bash
JOB=$(curl -s -F file=@assets/sample1.mp4 localhost:8000/upload | jq -r .job_id)
curl -s -X POST localhost:8000/start/$JOB -H 'Content-Type: application/json' \
  -d '{"roi":[[0,0],[960,0],[960,540],[0,540]],"pixels_per_meter":50}'
curl -s localhost:8000/status/$JOB | jq .metrics    # count/avg/density 등 변동 확인
curl -s localhost:8000/metrics_all/$JOB | jq '.total'  # 프레임 수만큼 저장됐는지
```

## 5. 디자인 자산 (`webui/static/`)

디자인 시스템 CSS와 폰트를 복사:
```
static/colors_and_type.css   # 색/타입 토큰 + @font-face
static/app.css               # 레이아웃/컴포넌트 클래스
static/fonts/*.ttf           # Pretendard
```
`app.mount("/static", StaticFiles(directory=BASE/"static"))`.

**검증**: `curl -s -o /dev/null -w '%{http_code}' localhost:8000/static/app.css` → 200.

## 6. 프론트 (`webui/index.html`)

- `<link>`로 `/static/colors_and_type.css`, `/static/app.css`
- `.hidden{display:none!important}` **직접 정의**(필수)
- 셸: `.app`(grid 64px/56px/100vh) + `.rail` + `.topbar` + `.stage`
- 3화면 컨테이너(`#scrUpload .empty`, `#scrSetup .setup`, `#scrRun`= `.workspace`+`.dash`)
- JS: 업로드→캔버스 ROI/보정→`/start`→`/stream` 표시 + `/status` 폴링→`renderDash`
- 완료 시 `/metrics_all`로 대시보드 동기 재생

상세는 [04](04-frontend.md).

**검증**(헤드리스 스크린샷 권장):
```bash
pip install playwright && python -m playwright install chromium
# chromium에 libasound.so.2 없으면: conda install -c conda-forge alsa-lib 후
# LD_LIBRARY_PATH에 그 lib 경로 추가 (06 참고)
```
playwright로 업로드→세팅→시작→완료까지 구동해 각 화면 스크린샷. 세 화면이 따로 보이고
(동시 표출 X) 좌/우가 한 페이지에 들어가면 OK.

## 7. 마무리

- `webui/.gitignore`에 `_data/`
- `webui/README.md`(빠른 시작) + `docs/webui.md`(사용법) + 본 폴더(개발 상세)
- 루트 `README.md`에 섹션 + 본 폴더 링크

## 재현 체크리스트

- [ ] `python -m src.inference_gpu` 기존 동작 그대로(run 회귀 없음)
- [ ] `/upload`가 첫 프레임 dataURL 반환
- [ ] `/start` 후 `/status.metrics`가 프레임마다 변동
- [ ] 라이브 MJPEG가 추론 속도로 흐름
- [ ] 완료 후 영상이 source fps로 무한 루프(끊김/멈춤 없음)
- [ ] 대시보드가 완료 후에도 영상과 함께 흐름(멈추지 않음)
- [ ] 세 화면 배타적 표시, 가로 오버플로 없음
- [ ] 보정 시 km/h, 미보정 시 px/s
