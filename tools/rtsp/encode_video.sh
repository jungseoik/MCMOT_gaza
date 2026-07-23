#!/usr/bin/env bash
# 영상을 MACS RTSP 송출용 WebRTC 호환 H.264로 인코딩한다.
# **먼저 check_video.sh로 적합성 검사** → 이미 적합하면 인코딩 건너뜀(그대로 복사).
#
# 사용:
#   bash tools/rtsp/encode_video.sh <입력.mp4> [출력.mp4]
#     출력 생략 시 <입력>_web.mp4 로 저장(원본 보존).
#   bash tools/rtsp/encode_video.sh --inplace *.mp4
#     디렉토리 일괄 — 부적합한 것만 원래 파일명으로 재인코딩(적합한 건 스킵).
#
# 조건: H.264 baseline / B-frame 없음 / 30프레임 I-frame 고정 / yuv420p / AAC 128k.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

encode_one() {  # $1 입력, $2 출력
  ffmpeg -y -i "$1" \
    -c:v libx264 -profile:v baseline -level 4.2 -pix_fmt yuv420p \
    -g 30 -keyint_min 30 -sc_threshold 0 \
    -x264-params "bframes=0:repeat-headers=1" \
    -preset veryfast -movflags +faststart \
    -c:a aac -b:a 128k \
    "$2"
}

if [[ "${1:-}" == "--inplace" ]]; then
  shift
  for f in "$@"; do
    if bash "$HERE/check_video.sh" "$f" >/dev/null 2>&1; then
      echo "⏭️  $f : 이미 적합 — 스킵"
      continue
    fi
    echo "🔄 $f : 재인코딩(원래 파일명으로 대체)"
    tmp="temp_$(basename "$f")"; mv "$f" "$tmp"
    encode_one "$tmp" "$f"; rm "$tmp"
    echo "✅ $f"
  done
else
  in="${1:?입력 mp4 경로 필요}"
  out="${2:-${in%.mp4}_web.mp4}"
  if bash "$HERE/check_video.sh" "$in" >/dev/null 2>&1; then
    echo "⏭️  $in : 이미 적합 — 복사만 → $out"; cp "$in" "$out"
  else
    echo "🔄 인코딩: $in → $out"; encode_one "$in" "$out"
  fi
  echo "✅ 완료: $out"
fi
