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

# FFmpeg 디코더 스레드 상한 — **코어 수가 많은 서버에서 반드시 필요하다.**
# OpenCV VideoCapture 는 FFmpeg thread_count 를 auto(=코어 수)로 연다. 이 서버는
# 128코어라 리허설 파일 9대를 열면 1,280개가 한꺼번에 생기고(실측 200 → 1,689),
# 그 뒤로 컨텍스트 스위칭에 눌려 API 가 응답하지 않았다(대기큐 23, 5분 타임아웃).
# 파일 디코드는 스레드를 늘려도 이득이 거의 없다 — 2개면 충분하다.
export OPENCV_FFMPEG_CAPTURE_OPTIONS="${OPENCV_FFMPEG_CAPTURE_OPTIONS:-threads;2}"
# OpenCV 자체 병렬화도 과하면 같은 문제를 만든다(파일 모드는 디코드가 대부분).
export OPENCV_FOR_THREADS_NUM="${OPENCV_FOR_THREADS_NUM:-4}"

# 리허설 파일 재생을 **실시간(1배속)** 으로. 기본(NODROP=1)은 페이싱을 건너뛰어
# 추론이 감당하는 만큼 달리는데, 실측 2.1배속이라 운영 뷰의 "경보 후 경과"가
# 그만큼 빨리 흐른다(벽시계 16.6s 에 35.2s). 시연·훈련은 실제 시간과 맞아야 한다.
# 되돌리려면 VSOURCE_FILE_NODROP=1.
export VSOURCE_FILE_NODROP="${VSOURCE_FILE_NODROP:-0}"

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
