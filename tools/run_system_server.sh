#!/bin/bash
# MACS 멀티카메라 시스템 서버 (pm2 등록용) — system/README.md 참조
#
# INGEST_BACKEND (기본 ffmpeg — 미지정 시 기존 동작과 동일):
#   ffmpeg      카메라별 ffmpeg-NVDEC 디코드 + 호스트 직렬 TRT (기존 경로, ~4ch@5fps)
#   deepstream  GPU별 DS 워커 컨테이너(zero-copy 배치 추론, 16ch@5fps/GPU)
#               사전조건: macs-deepstream:9.0 이미지 + external/weights/trt_ds/ 엔진
#               (system/ingest_ds/README.md). GPU_DEVICES 기본값이 갈림에 주의 —
#               ffmpeg는 "0,1"(NVDEC 라운드로빈), deepstream은 "1"(GPU 전유 전제).
#   예) INGEST_BACKEND=deepstream GPU_DEVICES=1 pm2 restart macs-system --update-env
# 롤백: INGEST_BACKEND 제거(또는 =ffmpeg)로 재기동 — docs/architecture/04 참조
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
export SITE_ID="${SITE_ID:-default}"

CMD="conda run --no-capture-output -n boosttrack \
  uvicorn system.api.server:app --host 0.0.0.0 --port 8900"

# deepstream 모드는 docker.sock 접근이 필요한데, pm2 데몬이 docker 그룹 없이
# 떠 있으면 자식도 그룹을 못 받아 "permission denied … docker.sock"로 죽는다.
# /etc/group 멤버십이 있으면 sg로 docker 그룹을 보충해 실행한다 (환경변수 유지).
if [ "${INGEST_BACKEND:-ffmpeg}" = "deepstream" ] \
   && ! id -nG | grep -qw docker \
   && getent group docker | grep -qw "$(id -un)"; then
  exec sg docker -c "$CMD"
fi
exec $CMD
