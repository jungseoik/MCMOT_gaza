#!/usr/bin/env bash
# STEP 3 — 각 카메라 00:00 번인 타임스탬프 확인용 프레임 + 전체 대조 그리드 1장
source "$(dirname "$0")/00_config.sh"
mkdir -p "$WORK/frame00"

for cam in $(present_cams); do
  ffmpeg -hide_banner -loglevel error -y -i "$CONCAT/${cam}_full.mp4" \
    -frames:v 1 -vf "scale=960:-1" \
    "$WORK/frame00/${cam}.png"
  echo "$cam ✅"
done
# 한 장으로 모아보기 (번인 시각 15개 동시 비교)
ffmpeg -hide_banner -loglevel error -y -pattern_type glob -i "$WORK/frame00/*.png" \
  -vf "scale=640:-1,tile=3x5" -frames:v 1 "$WORK/frame00_all.png" 2>/dev/null \
  && echo "대조표: $WORK/frame00_all.png"
