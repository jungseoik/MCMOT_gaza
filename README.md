# BoostTrack++: 실시간 객체 추적 및 속도 분석
[BoostTrack 공식 저장소](https://github.com/vukasin-stanojevic/BoostTrack)

BoostTrack++ 다중 객체 추적(MOT) 기술을 활용하여 실시간으로 비디오에서 객체를 감지하고 추적하는 시스템입니다. ROI(관심 영역)를 설정하여 해당 영역 내에서 이동하는 객체의 속도를 계산하고, 결과를 시각적으로 표시합니다.

## 환경 설정

> 🧱 **순정 우분투(NVIDIA 드라이버만)에서 처음부터 올리나요?** →
> **[docs/설치-맨서버-부트스트랩.md](docs/설치-맨서버-부트스트랩.md)** 의 "0단계: 시스템 준비"
> (conda·docker·nvidia-container-toolkit·ffmpeg·node/pm2·CAD 스택)를 복붙으로 갖춘 뒤 아래로.
> 대용량 자산(가중치·ONNX·CAD)은 `bash tools/fetch_assets.sh` 로 일괄 다운로드
> (HF `backseollgi/MCMOT`; 현재 비공개라 `HF_TOKEN` 필요).

```bash
# Conda 가상환경 생성
$ conda create -n boosttrack python=3.12 -y
$ conda activate boosttrack

# 종속성 설치
$ pip install -r requirements.txt

# yolox 설치 (torch 빌드 의존성으로 별도 설치)
$ bash install_yolox.sh
```

<br>

## 모델 가중치 다운로드

> 🚚 **다른 GPU 서버로 이관하나요?** → **[docs/이관가이드-다른-GPU-서버로.md](docs/이관가이드-다른-GPU-서버로.md)**
> 현재 엔진·성능 기준은 전부 **Blackwell(sm_120)** 에서 나온 것. 다른 아키텍처(예: RTX 5000 Ada)로
> 옮기면 **TRT 엔진 재빌드 + 한계 처리량 재측정 + `GPU_DEVICES` 실인덱스 확인**이 필수다.

**가중치는 `import assets` 시점에 HuggingFace에서 자동 다운로드된다** (`assets/__init__.py`).
추론/추적 코드가 `assets`를 import하면 아래 파일이 없을 때 `external/weights/`로 내려받는다.
**두 레포 모두 공개**라 **토큰 불필요**(clone 후 첫 실행에 자동 확보).

| 모델 종류 | HF repo (공개) | 저장 경로 |
|-----------|--------------------------|----------------------------|
| ReID 모델 | `backseollgi/mot20_sbs_S50.pth` | `./external/weights/mot20_sbs_S50.pth` |
| ByteTrack 탐지 모델 | `backseollgi/bytetrack_x_mot20.tar` | `./external/weights/bytetrack_x_mot20.tar` |

> ⚠️ `assets/__init__.py`는 다운로드 실패를 조용히 넘긴다(`except` 로 print만). 오프라인·레포
> 접근 불가 시 파일 없이 진행하다 로드 단계에서 깨지므로, 첫 실행 후 위 두 파일이 실제로
> `external/weights/`에 있는지 확인. 수동 다운로드: `hf download backseollgi/mot20_sbs_S50.pth
> mot20_sbs_S50.pth --local-dir external/weights` (tar도 동일).

<br>

## 비디오 추론

### 기본 추론 (PyTorch)
```bash
$ python -m src --input video.mp4 --output output.mp4
```

### 고속 추론 (TRT + GPU 최적화, x20배)
```bash
# 1. TRT 엔진 빌드 (GPU별 최초 1회)
$ python -m src.build_trt --fp16

# 2. 추론 실행
$ python -m src.inference_gpu -i video.mp4 -o output.mp4
```

### 검출기 투트랙 (YOLOX ↔ RF-DETR)
검출기만 갈아끼울 수 있다(트래커 BoostTrack++·ReID 백엔드는 동일). RF-DETR은
파인튜닝 없이 상용 사전학습(COCO)을 TRT fp16으로 쓰며, 고소·어안 등 어려운 화각에서
YOLOX보다 잘 잡는다. 자세한 근거·설계는 [docs/reports/RF-DETR-TRT-변환-사용법](docs/reports/RF-DETR-TRT-변환-사용법.md).
```bash
# RF-DETR 엔진 준비(모델 다운로드→ONNX→TRT fp16, 1회). HF ONNX가 있으면 fetch만으로도 됨:
$ bash tools/fetch_assets.sh --onnx   # (선택) HF에서 rfdetr-base.onnx 받기
$ bash tools/setup_rfdetr.sh          # 엔진 빌드 → external/weights/trt/rfdetr_base_fp16.engine

# 검출기 선택 실행
$ python -m src.inference_gpu -i video.mp4 -o out.mp4 --detector rfdetr --det_thresh 0.15 --no_ecc
```

### 공통 옵션
| 인자 | 기본값 | 설명 |
|------|--------|------|
| `--input`, `-i` | (필수) | 입력 비디오 경로 |
| `--output`, `-o` | (필수) | 출력 비디오 경로 |
| `--detector` | yolox | 검출기 선택: `yolox` \| `rfdetr` (투트랙) |
| `--det_thresh` | 0.4 | 탐지 신뢰도 임계값 (RF-DETR 권장 0.15) |
| `--no_reid` | false | ReID 외형 특징 비활성화 |
| `--no_ecc` | false | 카메라 모션 보정 비활성화 (고정 CCTV·RF-DETR 권장) |

### Python API
```python
# 기본
from src.inference import BoostTrackInference
tracker = BoostTrackInference(det_thresh=0.4)
result = tracker.run("input.mp4", "output.mp4")

# 고속 (검출기 투트랙 — "yolox"(기본) | "rfdetr")
from src.inference_gpu import BoostTrackGPUInference
tracker = BoostTrackGPUInference(detector="yolox")            # 또는 detector="rfdetr"
result = tracker.run("input.mp4", "output.mp4")
```

<br>

## 속도 비교 (RTX PRO 6000 Blackwell, 646프레임)

| 모드 | FPS | Speedup | 원본 대비 정확도 |
|------|-----|---------|-----------|
| PyTorch (Original) | 0.88 | - | - |
| TRT FP16 | 0.93 | x1.06 | 탐지 99.9%, 추적 98.9% |
| **TRT FP16 + Optimized** | **17.6** | **x20** | **탐지 99.9%, 추적 98.9%** |

최적화 상세 내용과 정확도 분석: [docs/optimization-report.md](docs/optimization-report.md)

<br>

## 벤치마크
원본 PyTorch / TRT FP32 / TRT FP16 / TRT+GPU 4개 variant의 속도와 정확도를 비교합니다.
```bash
$ python -m src.benchmark -i assets/sample1.mp4 -n 100
```

<br>

## 실시간 추적 웹 UI (FastAPI)

**비디오 파일 또는 RTSP 스트림**을 입력받아 TRT 추적 결과를 실시간으로 보여주고,
객체별 **속도(km/h)·밀도·체류·가속도** 등을 우측 대시보드로 표출하는 독립형 웹 UI입니다.
코어 파이프라인을 **재사용만** 하므로 위의 워크플로에는 영향이 없습니다.

주요 기능:
- **입력**: 파일 업로드 / **RTSP 라이브**(정지 버튼, 새 소스 시 기존 자동 정지, 샘플 스트림 바로가기)
- **기본 시각화(다운로드)**: 파일 전용 — ID+박스만 그려 추론 후 결과를 **H.264로 저장·다운로드**(RTSP에선 비표시)
- **인·아웃 카운팅**: 출입구에 선(2점)+안쪽 지정 → 통과 in/out 집계 + 재실(=in−out) 실시간, 음수면 경보(폐쇄공간 재실 추정)
- **속도 보정 4모드**: 없음(px/s) · 보정선(2점) · ROI 실측(4점 호모그래피, 원근보정) · **Depth 자동**(사람키 기준, 미리보기 확인)
- **2D 맵 뷰**: 영상↔맵 토글, 사람을 top-down 평면에 점+방향벡터로 표시
- 파일은 완료 후 결과를 무한 루프 재생(대시보드 프레임 동기)

```bash
$ pip install -r webui/requirements.txt   # 웹 전용 의존성 (1회)
# Depth 자동 모드는 별도 da3 env 필요 (DA3_PYTHON), docs/webui-dev/07 참고
$ python -m webui                          # http://localhost:8000
```

- **사용 가이드(스크린샷 + 화면별 동작 + 지표 계산)**: [docs/guide/](docs/guide/)
- 사용법(설치/실행/튜닝): [docs/webui.md](docs/webui.md)
- **개발 문서(무엇으로·어떻게 만들었는지, 처음부터 재현 가이드 포함)**:
  [docs/webui-dev/](docs/webui-dev/) — 이 폴더의 문서만 따라가면 동일하게 다시
  구현할 수 있도록 아키텍처·백엔드·스트리밍·속도지표·프론트·함정기록을 정리했습니다.

<br>

## 멀티카메라 2D맵 시스템 (system/, 포트 8900)

여러 대의 CCTV(RTSP)를 등록해 **공통 2D 평면도 위에서 사람을 실시간 추적**하고,
경보 세션을 통해 **4대 정량지표(IDR·EPFI·CBS·SEI)** 를 산출하는 독립 서버입니다.

```bash
# 사전: pm2 설치 (1회)
npm install -g pm2

# system/ 추가 의존성 (1회)
pip install -r requirements.txt   # Pillow 포함

# 실행 (pm2 상시 기동)
pm2 start tools/run_system_server.sh --name macs-system
# → http://<host>:8900/   (맵 설정 → 카메라 등록·매핑 → 운영 뷰 → 경보 세션)

# 재시작 / 로그
pm2 restart macs-system
pm2 logs macs-system
```

환경변수: `SITE_ID`(기본 default) · `SITE_ROOT`(기본 data/sites) · `GPU_DEVICES` ·
`INGEST_BACKEND`(기본 ffmpeg / deepstream 전환) → 상세: `.env.example` ·
DeepStream 인제스트: [system/ingest_ds/README.md](system/ingest_ds/README.md) ·
실행 옵션: [system/README.md](system/README.md)
→ 사용 가이드(스크린샷): [docs/guide/멀티카메라-시스템/](docs/guide/멀티카메라-시스템/)

> **⚠️ 기존 단일채널 웹 UI(webui/server.py, :8000)와 동시 기동 금지** — 전역 설정 충돌.
> 각각 별도 포트로 독립 실행하고, 단일채널 UI 좌측 레일 맵 아이콘이 :8900을 엽니다.

<br>

## MOT 데이터셋 평가 (⚠️ 미포함 / WIP)

> **현재 저장소에는 평가 글루 코드(`src/eval_common.py`, `src/eval_torch.py`,
> `src/eval_trt.py`, `src/eval_trt_opt.py`)가 포함되어 있지 않습니다.** `main.py`는
> 이 모듈들을 import하므로 **그대로 실행하면 `ModuleNotFoundError`로 즉시 실패**합니다.
> 아래는 평가 진입점(`main.py`)이 제공하기로 설계된 인터페이스 명세이며, 실제 동작에는
> `src/eval_*.py` 구현과 MOT GT(`results/gt/…`, 일부 동봉)·TRT 엔진이 필요합니다.
> 평가 채점 엔진 자체(TrackEval)는 `external/TrackEval/`에 포함돼 있습니다.

`main.py`는 MOT17/MOT20 벤치마크 평가의 진입점으로, 백엔드를 `--engine` 인자로 선택하도록 설계돼 있습니다.

| 엔진 | 설명 | 검출기 | ReID 전처리 |
|------|------|--------|------------|
| `torch` | PyTorch FP16 (원본 main 로직) | PyTorch | PyTorch |
| `trt` | TRT 기본 | TensorRT | PyTorch |
| `trt_opt` | TRT mixed precision + GPU 최적화 | TensorRT FP16 | GPU 버퍼 (~17.6 FPS) |

한 번 실행하면 **추론 + 후처리 + TrackEval 메트릭 산출 + 통합 JSON 저장**이 모두 끝납니다.

```bash
# PyTorch 기준선 (val_half에서 평가)
$ python main.py --dataset mot20 --engine torch    --exp_name BTPP_torch

# TRT 기본
$ python main.py --dataset mot20 --engine trt      --exp_name BTPP_trt

# TRT 최적화 (가장 빠름)
$ python main.py --dataset mot20 --engine trt_opt  --exp_name BTPP_trt_opt
```

각 실행이 끝나면:
- 콘솔에 timing 요약 + HOTA/MOTA/IDF1 등 벤치마크 점수 출력
- `results/trackers/<benchmark>-val/<exp_name>_results.json` 에 timing + metrics 통합 저장

**`--test_dataset` 사용 시 GT가 없으므로 TrackEval 자동 평가가 건너뛰어집니다.** `--no_eval`로 평가만 끌 수도 있습니다.

> 위 명령을 실행하려면 먼저 `src/eval_*.py` 4개 모듈을 구현/복원해야 합니다(미포함).
> 상세 가이드 문서(`docs/eval-pipeline.md`)도 아직 작성되지 않았습니다.

<br>

## 프로젝트 구조

```
src/                           # 추론 + 평가 모듈
  inference.py                 # PyTorch 기본 추론 (영상)
  inference_trt.py             # TRT 추론 (영상)
  inference_gpu.py             # TRT + GPU 최적화 추론 (영상, x20)
  build_trt.py                 # ONNX export + TRT 엔진 빌드
  benchmark.py                 # 속도/정확도 비교 벤치마크
  # ── 아래 4개는 미포함(WIP). main.py가 import하므로 구현 전엔 평가 명령 실행 불가 ──
  # eval_common.py             # MOT 평가 공통 유틸 (타이밍, 결과 저장, 후처리)
  # eval_torch.py              # MOT 평가 — PyTorch 백엔드
  # eval_trt.py                # MOT 평가 — TRT 기본 백엔드
  # eval_trt_opt.py            # MOT 평가 — TRT FP16 + GPU 최적화 백엔드
webui/                         # 단일채널 실시간 추적 웹 UI (FastAPI, :8000)
  server.py                    # FastAPI 앱 (upload/stream/status/result)
  index.html                   # 프론트엔드
  __main__.py                  # python -m webui 진입점
system/                        # 멀티카메라 2D맵 시스템 (FastAPI, :8900)
  api/server.py                # 실서버 진입점 (uvicorn system.api.server:app)
  api/mock_server.py           # 프론트 개발용 mock (GPU 불필요)
  config/                      # pydantic 스키마 + JSON 영속화 (SiteStore)
  ingest/                      # ffmpeg-NVDEC 카메라 워커·FrameQueue·재접속 워치독
  ingest_ds/                   # DeepStream zero-copy 인제스트 (INGEST_BACKEND=deepstream)
  tracking/                    # 공유 TRT 검출·ReID + 카메라별 BoostTrack
  spatial/                     # 호모그래피 맵 투영·polygon/통과선/polyline 기하
  metrics/                     # MetricsEngine — 4대 지표 세션 산출
  README.md                    # 실행·환경변수·pm2·모듈 소유 정보
docs/
  optimization-report.md       # 최적화 상세 보고서
  webui.md                     # 실시간 추적 웹 UI 사용법
  webui-dev/                   # 웹 UI 개발 문서(재현 가이드 포함)
main.py                        # MOT 평가 진입점 (--engine 분기, src/eval_* 미포함 → WIP)
tracker/                       # 핵심 추적 알고리즘 (변경 없음)
external/                      # 외부 모듈 (변경 없음)
```

<br>

## 기타 기능

### `check_roi.py`: ROI 설정 GUI 툴
- `data/videos` 폴더의 비디오 첫 프레임에서 마우스로 ROI 설정
- `n` 키로 ROI 좌표 출력

### 결과 저장
- 추적 비디오: `--output`/`-o`로 지정한 경로에 저장(예: `python -m src -i in.mp4 -o out.mp4`).
  Python API에서는 `run(input, output)`의 두 번째 인자로 지정.
- 속도/밀도 등 지표는 추론 코어가 파일로 남기지 않으며, 실시간 지표는 웹 UI 대시보드에서 표출.
