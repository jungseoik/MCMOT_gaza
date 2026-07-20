#!/bin/bash
# b32 dynamic YOLOX 엔진 빌드 (P11) — 컨테이너(macs-deepstream:9.0) 안에서 실행.
# opt=16 / opt=32 두 변형을 빌드해 배치 지연 프로파일 비교 후 하나를 채택한다.
# 호스트에서:
#   docker run --rm --gpus device=1 -v "$PWD:/workspace" -w /workspace \
#       macs-deepstream:9.0 bash docs/reports/bench/build_b32_engines.sh
set -ex
TRTEXEC=/usr/src/tensorrt/bin/trtexec
ONNX=external/weights/trt/yolox_mot20_dynamic.onnx
OUT=external/weights/trt_ds

$TRTEXEC --onnx=$ONNX \
  --saveEngine=$OUT/yolox_mot20_fp16_dyn_b32o16.engine --fp16 \
  --minShapes=images:1x3x896x1600 --optShapes=images:16x3x896x1600 \
  --maxShapes=images:32x3x896x1600 \
  --memPoolSize=workspace:8192M \
  > docs/reports/bench/trtexec_build_b32o16.log 2>&1

$TRTEXEC --onnx=$ONNX \
  --saveEngine=$OUT/yolox_mot20_fp16_dyn_b32o32.engine --fp16 \
  --minShapes=images:1x3x896x1600 --optShapes=images:32x3x896x1600 \
  --maxShapes=images:32x3x896x1600 \
  --memPoolSize=workspace:8192M \
  > docs/reports/bench/trtexec_build_b32o32.log 2>&1

echo BUILD_DONE
