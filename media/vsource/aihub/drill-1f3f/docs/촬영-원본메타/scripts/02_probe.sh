#!/usr/bin/env bash
# STEP 2 — 파일별 길이/코덱/fps 추출 (오프셋 계산 및 이상 파일 탐지용)
source "$(dirname "$0")/00_config.sh"

out="$WORK/file_probe.csv"
echo "cam,file,codec,width,height,fps,duration,sample_rate" > "$out"
for cam in $(present_cams); do
  find "$ORIG/$cam" -maxdepth 1 -type f \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.mts' \) \
    -print0 | sort -z | while IFS= read -r -d '' f; do
    v=$(ffprobe -v error -select_streams v:0 \
          -show_entries stream=r_frame_rate,codec_name,width,height \
          -show_entries format=duration -of csv=p=0 "$f" | paste -sd, -)
    sr=$(ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate -of csv=p=0 "$f")
    echo "$cam,$(basename "$f"),$v,${sr:-none}"
  done
done | tee -a "$out"
echo
echo "저장: $out"
echo "--- 카메라별 총 길이 ---"
for cam in $(present_cams); do
  d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$CONCAT/${cam}_full.mp4" 2>/dev/null || echo 0)
  printf "%-6s %s\n" "$cam" "$(awk -v s="$d" 'BEGIN{printf "%02d:%02d:%06.3f", s/3600, (s%3600)/60, s%60}')"
done
