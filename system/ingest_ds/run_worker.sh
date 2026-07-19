#!/usr/bin/env bash
# DeepStream 인제스트+추론 워커 docker 실행 래퍼.
#
# 사용:
#   system/ingest_ds/run_worker.sh --cams system/ingest_ds/configs/cams_4ch.json \
#       [--batch-size 8] [--zmq-bind tcp://*:5701] [worker.py 인자 그대로 전달...]
#
# 환경변수:
#   GPU    사용할 호스트 GPU 번호 (기본 1 — GPU0은 타 프로젝트 사용 중)
#   IMAGE  도커 이미지 (기본 macs-deepstream:9.0)
#
# --network host: mediamtx RTSP(127.0.0.1)와 ZMQ(호스트 bridge) 공유
# 레포 전체를 /workspace로 마운트 — 엔진(external/weights/trt_ds/)·설정 포함
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GPU="${GPU:-1}"
IMAGE="${IMAGE:-macs-deepstream:9.0}"

exec docker run --rm --network host --gpus "device=${GPU}" \
  -v "${REPO}:/workspace" -w /workspace \
  -e PYTHONUNBUFFERED=1 \
  "${IMAGE}" python3 -m system.ingest_ds.worker "$@"
