# RF-DETR(base) TensorRT fp16 변환·사용법 (검출기 비교 실험)

현장 exit 카메라(고소·급경사·어안·문앞 초크포인트) 화각에서 기존 YOLOX-MOT20 검출기가
사람을 자주 놓치는 문제를 확인하고, **파인튜닝 없이** 상용 사전학습 검출기
[**RF-DETR base**](https://github.com/roboflow/rf-detr)(Roboflow, ICLR 2026, Apache-2.0)를
TensorRT fp16으로 변환해 비교·사용하기 위한 절차. **재사용 목적 문서.**

> 원칙: 서드파티 모델·가중치·엔진·venv는 전부 `third_party/`(‌**gitignore**‌)에 두고 레포 코드와
> 분리한다. 산출 시각화는 `field/`(gitignore)에 저장. 이 문서만 레포에 남긴다.

## 왜 이렇게 하나 (설계)

- **격리 venv**: RF-DETR(`rfdetr`)은 최신 torch/transformers를 끌어와 `boosttrack` 콘다환경
  (torch 2.9+cu130 + TensorRT 10.16, 라이브 파이프라인용)과 충돌 위험이 큼 →
  `third_party/.venv-rfdetr`(별도 torch/TRT)로 격리.
- **2단계 분리**: ① 검출(격리 venv, RF-DETR TRT) → 프레임별 person dets를 `.npz`로 저장,
  ② 추적(`boosttrack` 환경, BoostTrack++ + FastReID)에서 dets를 읽어 ID 오버레이.
  → 의존성 충돌 없이 "검출기만 교체, 트래커 백엔드는 동일" 비교가 됨.

## 0. 사전 준비 (한 번)

```bash
cd ~/seoik/MCMOT_gaza
mkdir -p third_party && cd third_party      # (.gitignore에 third_party/ 등록됨)
git clone --depth 1 https://github.com/roboflow/rf-detr.git

# 격리 venv + 설치 (tensorrt/onnx 포함)
python3 -m venv .venv-rfdetr
./.venv-rfdetr/bin/pip install -U pip
./.venv-rfdetr/bin/pip install "rfdetr[tensorrt]" onnx onnxsim onnxruntime opencv-python-headless
```

## 1. ONNX export (venv)

```bash
CUDA_VISIBLE_DEVICES=0 third_party/.venv-rfdetr/bin/python - <<'PY'
from rfdetr import RFDETRBase
m = RFDETRBase()                       # COCO 사전학습 weight 자동 다운로드(~/.roboflow)
m.export(output_dir="third_party/rfdetr_export",
         format="onnx", opset_version=17, fp16=True, batch_size=1)
PY
# -> third_party/rfdetr_export/rfdetr-base.onnx  (입력 1x3x560x560, 출력 dets[1,300,4]·labels[1,300,91])
```

## 2. TensorRT fp16 엔진 빌드 (venv, trtexec 불필요 — polygraphy)

```bash
CUDA_VISIBLE_DEVICES=0 third_party/.venv-rfdetr/bin/python third_party/rfdetr_build_trt.py \
  third_party/rfdetr_export/rfdetr-base.onnx
# 내부: rfdetr.export._tensorrt.build_engine(onnx, fp16=True)
# -> third_party/rfdetr_export/rfdetr-base.trt  (약 107MB, fp16, 560x560 고정)
```

> ⚠️ 엔진은 **빌드한 GPU/TRT 버전 전용**. 현 장비는 Blackwell(sm_120)·venv TRT 11.2에서 빌드.
> 다른 GPU로 옮기면 1·2단계를 그 장비에서 다시 수행.

## 3. 검출 → dets 저장 (venv, RF-DETR TRT)

```bash
CUDA_VISIBLE_DEVICES=0 third_party/.venv-rfdetr/bin/python third_party/rfdetr_trt_detect.py \
  third_party/rfdetr_export/rfdetr-base.trt          # [--only exit]
# person(class=1)만, conf>0.1 → third_party/dets/rfdetr_trt/<name>.npz
# rfdetr 공식 전/후처리(benchmark.TRTInference·post_process, sync_mode=True → pycuda 불필요) 재사용
```

## 4. 추적·오버레이 (boosttrack 환경, BoostTrack++ 동일 백엔드)

```bash
PY=~/miniconda3/envs/boosttrack/bin/python
CUDA_VISIBLE_DEVICES=0 $PY third_party/dets_to_boosttrack.py \
  third_party/dets/rfdetr_trt rfdetr \
  --det-thresh 0.15 --outroot field/infer/cmp2/cfgB_det015_eccOFF   # --ecc 로 ECC on
# -> field/infer/cmp2/<cfg>/rfdetr/<name>_track.mp4
```

## 5. 3분할 비교(YOLOX│YOLO26x│RF-DETR) + 몽타주

```bash
$PY third_party/make_compare3.py field/infer/cmp2/<cfg> --width 640
bash tools/montage.sh field/infer/cmp2/<cfg>
```

## 측정 결과 (이 장비, GPU0)

| 검출기 | 파라미터 | 입력 | 엔진(fp16) | 속도(엔진 실행-only) | exit 화각 |
|---|---|---|---|---|---|
| YOLOX-X (현행) | ~99M | 896×1600 | 191.9MB TRT | **179 fps** (5.6ms) | 사람 자주 놓침 |
| **RF-DETR base** | **32.2M** | 560×560 | **107MB TRT** | **197 fps** (5.08ms) | **3/3 정확·오검출 0** |

→ RF-DETR base(TRT fp16)가 **더 작고(32M) 더 빠르며(197fps) 이 화각에서 더 정확**.
파인튜닝 없이 상용 그대로. (end-to-end 파이썬 루프+IO+후처리 포함 시 ~47fps — 배포 시 배치/비동기로 개선 여지.)

## 스크립트 (전부 `third_party/`, gitignore)

| 파일 | 용도 |
|---|---|
| `rfdetr_build_trt.py` | ONNX→TRT fp16 엔진 빌드 |
| `rfdetr_trt_detect.py` | RF-DETR TRT 검출 → dets npz |
| `dump_ultra.py` | ultralytics(YOLO26x 등) 검출 → dets npz |
| `dets_to_boosttrack.py` | dets npz → BoostTrack++ 오버레이 (`--det-thresh`,`--ecc`,`--outroot`) |
| `yolox_boosttrack.py` | YOLOX 전 파이프라인 오버레이 (config 지정) |
| `make_compare3.py` | 3분할 비교 영상 |

## 주의
- **파인튜닝 안 함**(정책). 전부 COCO/공개 사전학습 그대로 비교.
- 실 CCTV(개인정보)라 `field/`는 git 제외. 서드파티 대용량도 `third_party/` git 제외.
