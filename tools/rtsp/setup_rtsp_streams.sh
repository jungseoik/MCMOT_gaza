#!/usr/bin/env bash
# MACS RTSP 테스트 송출 — 재현 스크립트
# HuggingFace backseollgi/MCMOT (model, videos/) 에서 테스트 영상을 받아
# mediamtx(도커)로 RTSP 송출(pm2 등록)한다. 새 서버 이관 시 이거 한 방으로 재현.
#
# 사용:
#   HF_TOKEN=hf_xxx bash tools/rtsp/setup_rtsp_streams.sh            # 기본 3채널(멀티카메라 최소재현)
#   HF_TOKEN=hf_xxx bash tools/rtsp/setup_rtsp_streams.sh --all      # 전체 12개(단일영상 MVP·벤치 재현 포함)
#   STREAMS="sample1 zara01" HF_TOKEN=hf_xxx bash tools/rtsp/setup_rtsp_streams.sh   # 임의 조합
#   ⚠️ backseollgi/MCMOT 는 **비공개(model)** 라 HF_TOKEN 이 필요하다.
#      (또는 `hf auth login`. hf CLI가 HF_TOKEN 환경변수/로그인 세션을 인식한다. 토큰 커밋 금지)
#
# 결과: rtsp://<이서버IP>:8554/<스트림이름> 송출 (기본 1_v1,2_v1,3_v1).
# MACS 등록 주소 = rtsp://<송출서버IP>:8554/<스트림이름>
#
# 상세·조건(WebRTC 호환 인코딩 등)은 docs/RTSP-송출서버-구성.md 참조.
set -euo pipefail

# ── 설정 ──────────────────────────────────────────────────────────────────
HF_REPO="${HF_REPO:-backseollgi/MCMOT}"             # model 타입, videos/ 하위 (비공개)
STREAM_DIR="${STREAM_DIR:-$HOME/rtsp-stream}"       # 영상·스크립트 보관 위치
MEDIAMTX_NAME="mediamtx"

# 송출할 스트림(=영상 파일명, backseollgi/MCMOT videos/<이름>.mp4).
#   기본: 멀티카메라 최소재현 3채널.
#   --all: 단일영상 MVP·벤치(sample1 등) 재현용 전체 12개.
#   STREAMS 환경변수(공백구분)로 임의 조합 오버라이드 가능.
DEFAULT_STREAMS=("1_v1" "2_v1" "3_v1")
ALL_STREAMS=("1_v1" "2_v1" "3_v1" \
  "sample1" "zara01" "zara02" "eth" "hotel" "students03" \
  "arxiepiskopi" "in_out_counting" "inout_sample2")

if [[ -n "${STREAMS:-}" ]]; then
  # STREAMS="a b c" 환경변수 오버라이드
  read -r -a STREAMS <<< "$STREAMS"
elif [[ "${1:-}" == "--all" ]]; then
  STREAMS=("${ALL_STREAMS[@]}")
else
  STREAMS=("${DEFAULT_STREAMS[@]}")   # 기본 3채널
fi
echo "▶ 송출 대상(${#STREAMS[@]}개): ${STREAMS[*]}"

echo "▶ 송출 디렉토리: $STREAM_DIR"
mkdir -p "$STREAM_DIR"

# ── 1) hf CLI 확인 ────────────────────────────────────────────────────────
if ! command -v hf >/dev/null 2>&1; then
  echo "hf CLI가 없습니다. 설치: pip install -U huggingface_hub" >&2
  echo "(conda면: conda run -n boosttrack pip install -U huggingface_hub)" >&2
  exit 1
fi

# ── 2) HF에서 테스트 영상 다운로드 (videos/ 하위) ─────────────────────────
# backseollgi/MCMOT 는 비공개(model) — HF_TOKEN(또는 hf auth login) 필요.
echo "▶ HF 레포(model)에서 영상 다운로드: $HF_REPO"
for s in "${STREAMS[@]}"; do
  if [[ -f "$STREAM_DIR/$s.mp4" ]]; then
    echo "  - $s.mp4 이미 있음, 건너뜀"
    continue
  fi
  hf download "$HF_REPO" "videos/$s.mp4" --repo-type model \
    --local-dir "$STREAM_DIR/.hf_tmp" >/dev/null
  mv "$STREAM_DIR/.hf_tmp/videos/$s.mp4" "$STREAM_DIR/$s.mp4"
  echo "  - $s.mp4 받음"
done
rm -rf "$STREAM_DIR/.hf_tmp"

# ── 2b) 적합성 체크 → 부적합하면 인코딩 (송출 전 안전장치) ─────────────────
# HF(MCMOT) 영상은 이미 WebRTC 호환이라 보통 전부 스킵되지만, 다른 영상을
# 넣었거나 원본이 바뀐 경우를 대비해 검사 후 필요한 것만 재인코딩한다.
HERE="$(cd "$(dirname "$0")" && pwd)"
for s in "${STREAMS[@]}"; do
  if ! bash "$HERE/check_video.sh" "$STREAM_DIR/$s.mp4" >/dev/null 2>&1; then
    echo "▶ $s.mp4 부적합 감지 — WebRTC 호환으로 재인코딩"
    bash "$HERE/encode_video.sh" --inplace "$STREAM_DIR/$s.mp4"
  fi
done

# ── 3) mediamtx (도커) 기동 ───────────────────────────────────────────────
if ! docker ps --format '{{.Names}}' | grep -qx "$MEDIAMTX_NAME"; then
  if docker ps -a --format '{{.Names}}' | grep -qx "$MEDIAMTX_NAME"; then
    docker start "$MEDIAMTX_NAME" >/dev/null
  else
    docker run -d --name "$MEDIAMTX_NAME" \
      -p 8554:8554 -p 1935:1935 -p 8888:8888 -p 8889:8889 \
      bluenviron/mediamtx >/dev/null
  fi
  echo "▶ mediamtx 기동됨 (RTSP :8554)"
else
  echo "▶ mediamtx 이미 실행 중"
fi

# ── 4) 스트림별 송출 스크립트 생성 + pm2 등록 ─────────────────────────────
# 영상은 이미 WebRTC 호환(H.264 baseline)으로 인코딩돼 있어 -c:v copy로 송출.
# (원본이 비호환이면 docs/RTSP-송출서버-구성.md의 인코딩 절차 먼저)
command -v pm2 >/dev/null 2>&1 || { echo "pm2가 없습니다. npm i -g pm2" >&2; exit 1; }

for s in "${STREAMS[@]}"; do
  sh="$STREAM_DIR/stream-$s.sh"
  cat > "$sh" <<EOF
#!/usr/bin/env bash
exec ffmpeg -re -stream_loop -1 -i "$STREAM_DIR/$s.mp4" \\
  -c:v copy -an \\
  -f rtsp -rtsp_transport tcp \\
  rtsp://localhost:8554/$s
EOF
  chmod +x "$sh"
  pm2 delete "$s" >/dev/null 2>&1 || true
  pm2 start "$sh" --name "$s" >/dev/null
  echo "  - pm2 송출 등록: $s → rtsp://localhost:8554/$s"
done

pm2 save >/dev/null 2>&1 || true
IP="$(hostname -I | awk '{print $1}')"
echo
echo "✅ 송출 완료. MACS 카메라 등록 주소:"
for s in "${STREAMS[@]}"; do echo "   rtsp://$IP:8554/$s"; done
echo "확인: pm2 list  또는  ffplay rtsp://localhost:8554/${STREAMS[0]}"
