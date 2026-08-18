# 검출기 투트랙 (YOLOX ↔ RF-DETR) · RF-DETR TRT 변환·사용법

현장 exit 카메라(고소·급경사·어안·문앞 초크포인트) 화각에서 기존 YOLOX-MOT20이 사람을
자주 놓치는 문제 → **파인튜닝 없이** 상용 사전학습 [**RF-DETR base**](https://github.com/roboflow/rf-detr)
(Roboflow, ICLR 2026, Apache-2.0)를 TensorRT fp16으로 얹어 **검출기를 갈아끼울 수 있게** 통합했다.
BoostTrack++ 트래커·ReID 백엔드는 그대로, **검출기만 YOLOX ↔ RF-DETR** 로 교체된다.

## 한 줄 사용 (통합 완료)

```bash
PY=~/miniconda3/envs/boosttrack/bin/python

# 1) RF-DETR 엔진 준비 — 모델 다운로드부터 TRT 엔진까지 1회 (아래 "셋업" 참고)
bash tools/setup_rfdetr.sh

# 2) 추론 — 검출기만 바꿔서 실행 (트래커·ReID 동일)
$PY src/inference_gpu.py -i in.mp4 -o out.mp4                         # 기존: YOLOX (기본)
$PY src/inference_gpu.py -i in.mp4 -o out.mp4 --detector rfdetr \
      --det_thresh 0.15 --no_ecc                                     # 신규: RF-DETR
```

코드에서 직접:
```python
from src.inference_gpu import BoostTrackGPUInference
bt = BoostTrackGPUInference(detector="rfdetr", det_thresh=0.15, use_ecc=False)  # 또는 "yolox"
bt.run("in.mp4", "out.mp4")
```

## 왜 이렇게 하나 (설계) — **추론에 rfdetr 라이브러리 불필요**

> 질문(“서드파티 전부 가져오거나 라이브러리를 받아야 하냐?”)에 대한 답: **아니다.**
> 추론에는 **TRT 엔진 파일 + `src/rfdetr_trt.py`(자체 전/후처리 ~70줄)** 만 있으면 된다.

- `rfdetr` 라이브러리는 최신 torch/transformers를 끌어와 `boosttrack` 환경(torch 2.9+cu130 +
  TensorRT 10.16, 라이브 파이프라인용)과 **충돌**한다 → 본 환경에 설치하지 않는다.
- 대신 **① 엔진 빌드(ONNX export)만 격리 venv**(`third_party/.venv-rfdetr`)에서 1회 수행하고,
  **② 실제 추론은 본 환경 + 엔진 + `src/rfdetr_trt.py`** 로 동작(라이브러리 의존 0).
- 엔진은 **추론과 동일한 TRT 10.16(boosttrack)** 로 빌드해 버전 정합(엔진은 빌드 GPU/TRT 전용).
- 전/후처리는 RF-DETR 공식(`rfdetr.export._onnx`/`benchmark.post_process`)과 동일하게 복제:
  전처리 = RGB→to_tensor→resize(560, bilinear, antialias=False)→ImageNet 정규화 /
  후처리 = sigmoid→top-k(300)→cxcywh→xyxy→×(W,H,W,H)→person(class 1) 필터.

## 셋업 (`tools/setup_rfdetr.sh`) — 모델 다운로드부터 전부

한 번 실행하면 아래를 자동 수행한다.
1. 격리 venv 생성 + `rfdetr` 설치(엔진 빌드 전용, 1회)
2. **RF-DETR base COCO 가중치 자동 다운로드** + ONNX export(fp32) → `external/weights/onnx/rfdetr-base.onnx`
3. **TRT fp16 엔진 빌드**(boosttrack TRT 10.16) → `external/weights/trt/rfdetr_base_fp16.engine`

> `external/weights/`·`third_party/`·`field/`는 전부 **gitignore**. 엔진·가중치·venv·영상은
> git에 올리지 않고, 위 스크립트로 각 장비에서 재생성한다(다른 GPU면 그 장비에서 다시 실행).

## 코드 통합 지점 (레포 반영됨)

| 파일 | 변경 |
|---|---|
| `src/rfdetr_trt.py` (신규) | `RFDETRTRTDetector` — 자체 TRT 추론(전/후처리 포함), `detect_frame(frame)->(dets,ref)` |
| `src/inference_trt.py` | `TRTDetector`(YOLOX)에 동일 인터페이스 `detect_frame` + `input_size` 추가 |
| `src/inference_gpu.py` | `BoostTrackGPUInference(detector="yolox"\|"rfdetr", rfdetr_engine=...)` 투트랙, `_emit`가 `detect_frame` 사용. CLI `--detector` |
| `src/build_trt.py` | 기존 `build_engine(onnx, engine, fp16)` 재사용(ONNX→TRT) |
| `tools/setup_rfdetr.sh` (신규) | 모델 다운로드→ONNX→엔진 1커맨드 |

두 검출기 공통 계약: `detect_frame(bgr) -> (dets[N,5] , scale_ref_tensor)`.
`BoostTrack.update(dets, scale_ref, frame, tag)`가 `scale_ref` shape로 좌표 스케일을 복원
(YOLOX=letterbox ratio, RF-DETR=원본좌표라 ref=(1,3,H,W)→scale=1).

## 측정 결과 (이 장비, GPU0 · TRT fp16)

| 검출기 | 파라미터 | 입력 | TRT 엔진 | 속도(엔진 실행-only) | exit 화각 |
|---|---|---|---|---|---|
| YOLOX-X (기존) | ~99M | 896×1600 | 191.9MB | 179 fps (5.6ms) | 사람 자주 놓침 |
| **RF-DETR base** | **32.2M** | 560×560 | **58MB** | **197 fps** (5.08ms) | **3/3 정확·오검출 0** |

→ RF-DETR base(TRT fp16)가 **더 작고 · 더 빠르며 · 이 화각에서 더 정확**. 파인튜닝 없이 상용 그대로.

## 참고
- **파인튜닝 안 함**(정책). 전부 COCO/공개 사전학습 그대로.
- 실 CCTV(개인정보)라 `field/` git 제외. 검출기 비교 실험 스크립트(3분할 등)는 `third_party/`.
- RF-DETR 권장 설정: `--det_thresh 0.15 --no_ecc`(저신뢰 검출 살림 + 고정 CCTV라 ECC 불필요).
