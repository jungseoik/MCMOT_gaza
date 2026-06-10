# BoostTrack++: 실시간 객체 추적 및 속도 분석
[BoostTrack 공식 저장소](https://github.com/vukasin-stanojevic/BoostTrack)

BoostTrack++ 다중 객체 추적(MOT) 기술을 활용하여 실시간으로 비디오에서 객체를 감지하고 추적하는 시스템입니다. ROI(관심 영역)를 설정하여 해당 영역 내에서 이동하는 객체의 속도를 계산하고, 결과를 시각적으로 표시합니다.

## 환경 설정
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

| 모델 종류 | 파일명 | 저장 경로 |
|-----------|--------------------------|----------------------------|
| ReID 모델 | `mot20_sbs_S50.pth` | `./external/weights/mot20_sbs_S50.pth` |
| ByteTrack 탐지 모델 | `bytetrack_x_mot20.tar` | `./external/weights/bytetrack_x_mot20.tar` |

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

### 공통 옵션
| 인자 | 기본값 | 설명 |
|------|--------|------|
| `--input`, `-i` | (필수) | 입력 비디오 경로 |
| `--output`, `-o` | (필수) | 출력 비디오 경로 |
| `--det_thresh` | 0.4 | 탐지 신뢰도 임계값 |
| `--no_reid` | false | ReID 외형 특징 비활성화 |
| `--no_ecc` | false | 카메라 모션 보정 비활성화 |

### Python API
```python
# 기본
from src.inference import BoostTrackInference
tracker = BoostTrackInference(det_thresh=0.4)
result = tracker.run("input.mp4", "output.mp4")

# 고속
from src.inference_gpu import BoostTrackGPUInference
tracker = BoostTrackGPUInference()
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

## MOT 데이터셋 평가

`main.py`로 MOT17/MOT20 벤치마크 평가를 수행합니다. 백엔드는 `--engine` 인자로 선택합니다.

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

상세한 사용법, 주의사항(필요한 weights/엔진), 시간/메트릭 해석:
[docs/eval-pipeline.md](docs/eval-pipeline.md)

<br>

## 프로젝트 구조

```
src/                           # 추론 + 평가 모듈
  inference.py                 # PyTorch 기본 추론 (영상)
  inference_trt.py             # TRT 추론 (영상)
  inference_gpu.py             # TRT + GPU 최적화 추론 (영상, x20)
  build_trt.py                 # ONNX export + TRT 엔진 빌드
  benchmark.py                 # 속도/정확도 비교 벤치마크
  eval_common.py               # MOT 평가 공통 유틸 (타이밍, 결과 저장, 후처리)
  eval_torch.py                # MOT 평가 — PyTorch 백엔드
  eval_trt.py                  # MOT 평가 — TRT 기본 백엔드
  eval_trt_opt.py              # MOT 평가 — TRT FP16 + GPU 최적화 백엔드
webui/                         # 실시간 추적 웹 UI (독립 모듈, FastAPI)
  server.py                    # FastAPI 앱 (upload/stream/status/result)
  index.html                   # 프론트엔드
  __main__.py                  # python -m webui 진입점
docs/
  optimization-report.md       # 최적화 상세 보고서
  eval-pipeline.md             # MOT 평가 파이프라인 가이드
  webui.md                     # 실시간 추적 웹 UI 사용법
  webui-dev/                   # 웹 UI 개발 문서(재현 가이드 포함)
main.py                        # MOT 평가 진입점 (--engine 분기)
tracker/                       # 핵심 추적 알고리즘 (변경 없음)
external/                      # 외부 모듈 (변경 없음)
```

<br>

## 기타 기능

### `check_roi.py`: ROI 설정 GUI 툴
- `data/videos` 폴더의 비디오 첫 프레임에서 마우스로 ROI 설정
- `n` 키로 ROI 좌표 출력

### 결과 저장
- 추적 비디오: `data/output/output_video.mp4`
- 속도 로그: `data/output/speed_log.txt`
