#!/usr/bin/env bash
# 영상이 MACS RTSP 송출에 적합한지(WebRTC 호환) ffprobe로 검사만 한다(인코딩 안 함).
# 인코딩 전에 먼저 돌려서 이미 적합하면 재인코딩을 건너뛰기 위한 용도.
#
# 사용:  bash tools/rtsp/check_video.sh <video.mp4> [video2.mp4 ...]
# 종료코드: 인자로 준 영상이 전부 적합하면 0, 하나라도 부적합이면 1.
#
# 적합 기준 (docs/RTSP-송출서버-구성.md):
#   - 코덱 h264
#   - 프로파일 Baseline / Constrained Baseline  (B-frame 없음)
#   - pix_fmt yuv420p
#   - GOP(키프레임 간격) ≤ ~30 (장면전환 무관 고정 I-frame 권장)
set -uo pipefail

command -v ffprobe >/dev/null 2>&1 || { echo "ffprobe 필요(apt install ffmpeg)"; exit 2; }

ok_all=0
for f in "$@"; do
  if [[ ! -f "$f" ]]; then echo "❌ $f : 파일 없음"; ok_all=1; continue; fi
  codec=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 "$f")
  profile=$(ffprobe -v error -select_streams v:0 -show_entries stream=profile -of csv=p=0 "$f")
  pixfmt=$(ffprobe -v error -select_streams v:0 -show_entries stream=pix_fmt -of csv=p=0 "$f")
  wh=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$f")
  fps=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0 "$f")

  reasons=()
  [[ "$codec" == "h264" ]] || reasons+=("코덱 $codec(≠h264)")
  case "$profile" in
    Baseline|"Constrained Baseline") ;;
    *) reasons+=("프로파일 '$profile'(B-frame 위험)");;
  esac
  [[ "$pixfmt" == "yuv420p" ]] || reasons+=("pix_fmt $pixfmt(≠yuv420p)")

  if [[ ${#reasons[@]} -eq 0 ]]; then
    echo "✅ $f : 적합 ($codec/$profile/$pixfmt, ${wh}, ${fps}fps) — 재인코딩 불필요"
  else
    echo "⚠️  $f : 부적합 → $(IFS=', '; echo "${reasons[*]}")  → 인코딩 필요(encode_video.sh)"
    ok_all=1
  fi
done
exit $ok_all
