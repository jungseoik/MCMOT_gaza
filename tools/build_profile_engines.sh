#!/usr/bin/env bash
# 추론 프로파일(model_zoo.py)이 쓰는 TRT 엔진 일괄 빌드.
#
#   bash tools/build_profile_engines.sh            # 호스트(conda) 엔진만
#   bash tools/build_profile_engines.sh --ds       # DS 컨테이너 엔진도 함께
#
# 원천 ONNX는 tools/fetch_assets.sh --onnx 로 받는다(HF backseollgi/MCMOT).
# 엔진은 GPU 아키텍처·TRT 버전마다 다시 구워야 한다 — 호스트(conda TRT)와
# DS 컨테이너(TRT 버전 다름)는 서로의 엔진을 못 읽으므로 디렉토리를 나눈다:
#   external/weights/trt/     호스트  (webui:8000 · :8900 미리보기 · ffmpeg 백엔드)
#   external/weights/trt_ds/  컨테이너(:8900 DeepStream 백엔드 — 배치 추론)
set -euo pipefail
cd "$(dirname "$0")/.."

DS=0
[ "${1:-}" = "--ds" ] && DS=1
ONNX_DIR="external/weights/onnx"
IMAGE="${IMAGE:-macs-deepstream:9.0}"
GPU="${GPU:-1}"

for f in yolo26l_v6.3.onnx clipreid_person.onnx; do
  [ -f "$ONNX_DIR/$f" ] || { echo "ONNX 없음: $ONNX_DIR/$f — bash tools/fetch_assets.sh --onnx"; exit 1; }
done

build() {   # build <실행앞단> <출력디렉토리>
  local RUN="$1" OUT="$2"
  $RUN tools/build_trt_engine.py --onnx "$ONNX_DIR/yolo26l_v6.3.onnx" \
    --engine "$OUT/yolo26l_v6.3_fp16_b16.engine" --input images \
    --min 1x3x640x640 --opt 8x3x640x640 --max 16x3x640x640 --fp16
  $RUN tools/build_trt_engine.py --onnx "$ONNX_DIR/clipreid_person.onnx" \
    --engine "$OUT/clipreid_person_fp16_b256.engine" --input images \
    --min 1x3x256x128 --opt 32x3x256x128 --max 256x3x256x128 --fp16
}

echo "== 호스트 엔진 (external/weights/trt) =="
build "python" "external/weights/trt"

if [ "$DS" -eq 1 ]; then
  echo "== DS 컨테이너 엔진 (external/weights/trt_ds) =="
  build "docker run --rm --gpus device=${GPU} -v $PWD:/workspace -w /workspace ${IMAGE} python3" \
        "external/weights/trt_ds"
fi

echo "완료 — :8900 [① 설정 → 추론 모델]에서 프로파일을 고를 수 있습니다."
