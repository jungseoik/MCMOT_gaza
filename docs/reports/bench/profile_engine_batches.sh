#!/bin/bash
# TRT 엔진 배치별 지연 프로파일 (P11) — 컨테이너(macs-deepstream:9.0) 안에서 실행.
# 각 엔진에 대해 배치 1/8/16/24/32 의 GPU Compute Time(median)을 실측한다.
# 호스트에서:
#   docker run --rm --gpus device=1 -v "$PWD:/workspace" -w /workspace \
#       macs-deepstream:9.0 bash docs/reports/bench/profile_engine_batches.sh
# 출력: "PROFILE <엔진> b<배치> median=<ms> mean=<ms>" 행 — 표로 옮겨 기록한다.
set -e
TRTEXEC=/usr/src/tensorrt/bin/trtexec
ENGINES="${ENGINES:-external/weights/trt_ds/yolox_mot20_fp16_dyn_b16.engine \
external/weights/trt_ds/yolox_mot20_fp16_dyn_b32o16.engine \
external/weights/trt_ds/yolox_mot20_fp16_dyn_b32o32.engine}"

for eng in $ENGINES; do
  # 엔진 프로파일 max 초과 배치는 로드가 거부되므로 파일명으로 상한 판별
  case "$eng" in
    *b16*) BATCHES="1 8 16" ;;
    *)     BATCHES="1 8 16 24 32" ;;
  esac
  for b in $BATCHES; do
    out=$($TRTEXEC --loadEngine="$eng" --shapes=images:${b}x3x896x1600 \
          --noDataTransfers --useSpinWait --warmUp=1000 --duration=10 2>&1) || {
      echo "PROFILE $eng b$b FAILED"; continue; }
    line=$(echo "$out" | grep "GPU Compute Time:" | head -1)
    median=$(echo "$line" | sed -n 's/.*median = \([0-9.]*\) ms.*/\1/p')
    mean=$(echo "$line" | sed -n 's/.*mean = \([0-9.]*\) ms.*/\1/p')
    echo "PROFILE $eng b$b median=${median}ms mean=${mean}ms"
  done
done
