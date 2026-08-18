#!/usr/bin/env bash
# RF-DETR(base) 검출기 셋업 — 모델 다운로드부터 TRT fp16 엔진까지 한 번에.
# 결과: external/weights/trt/rfdetr_base_fp16.engine  (이후 --detector rfdetr 로 사용)
#
# 설계: rfdetr 라이브러리는 boosttrack 환경(torch 2.9+cu130 / TRT 10.16)과 충돌하므로,
#   ① 엔진 빌드(ONNX export)만 격리 venv(third_party/.venv-rfdetr)에서 수행하고
#   ② 실제 추론은 본 환경 + 엔진 파일 + src/rfdetr_trt.py 만으로 동작(라이브러리 불필요).
#   ③ 엔진은 추론과 동일한 TRT 10.16(boosttrack)로 빌드해 버전 정합.
#
# 사용: bash tools/setup_rfdetr.sh
set -euo pipefail
cd "$(dirname "$0")/.."

BOOST_PY="${BOOST_PY:-$HOME/miniconda3/envs/boosttrack/bin/python}"
VENV=third_party/.venv-rfdetr
ONNX=external/weights/onnx/rfdetr-base.onnx
ENGINE=external/weights/trt/rfdetr_base_fp16.engine
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

mkdir -p third_party external/weights/onnx external/weights/trt

# ONNX가 이미 있으면(예: bash tools/fetch_assets.sh --onnx 로 HF에서 받은 경우)
# rfdetr venv/export를 통째로 건너뛰고 3)엔진 빌드로 직행한다.
if [ -f "$ONNX" ]; then
  echo "[setup_rfdetr] ONNX 존재 → venv/export 생략 ($ONNX)"
fi

# 1) 격리 venv + rfdetr(엔진 빌드용, ONNX 없을 때만) ---------------------------
if [ ! -f "$ONNX" ] && [ ! -x "$VENV/bin/python" ]; then
  echo "[setup_rfdetr] 격리 venv 생성 + rfdetr 설치…"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -U pip -q
  "$VENV/bin/pip" install -q rfdetr onnx onnxsim onnxruntime opencv-python-headless
fi

# 2) COCO 사전학습 가중치 자동 다운로드 + ONNX export (fp32, 정규 경로) --------
if [ ! -f "$ONNX" ]; then
  echo "[setup_rfdetr] RF-DETR base 가중치 다운로드 + ONNX export…"
  "$VENV/bin/python" - "$ONNX" <<'PY'
import os, sys, shutil
from rfdetr import RFDETRBase
outdir = "external/weights/onnx/_rfdetr_export_tmp"
RFDETRBase().export(output_dir=outdir, format="onnx", opset_version=17, fp16=False, batch_size=1)
src = os.path.join(outdir, "rfdetr-base.onnx")
shutil.move(src, sys.argv[1]); shutil.rmtree(outdir, ignore_errors=True)
print("ONNX:", sys.argv[1])
PY
fi

# 3) TRT fp16 엔진 빌드 (boosttrack 본 환경 = 추론과 동일 TRT 버전) -----------
echo "[setup_rfdetr] TRT fp16 엔진 빌드(boosttrack TRT)…"
"$BOOST_PY" -c "from src.build_trt import build_engine; build_engine('$ONNX', '$ENGINE', fp16=True)"

echo "[setup_rfdetr] 완료 -> $ENGINE"
echo "사용:  \$PY src/inference_gpu.py -i <video> -o <out> --detector rfdetr --det_thresh 0.15 --no_ecc"
