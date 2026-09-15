#!/usr/bin/env bash
# STEP 4 — 동기화 기준점 프레임 추출
# 사용법: ./04_sync_frames.sh <시작시각> <길이초> <fps> [소스디렉토리]
#   거친 탐색:  ./04_sync_frames.sh 00:05:00 90 1        (1초 간격, 02_concat)
#   미세 조정:  ./04_sync_frames.sh 00:00:10 3  10 04_sync (0.1초 간격)
source "$(dirname "$0")/00_config.sh"
START="${1:?시작시각 예: 00:05:00}"; LEN="${2:-90}"; RATE="${3:-1}"; SRC="${4:-02_concat}"

if [ "$SRC" = "04_sync" ]; then SDIR="$SYNC"; SUF="_sync"; else SDIR="$CONCAT"; SUF="_full"; fi
OUT="$WORK/sync_${SRC}_${START//:/}_${RATE}fps"
rm -rf "$OUT"; mkdir -p "$OUT"

for cam in $(present_cams); do
  src="$SDIR/${cam}${SUF}.mp4"; [ -f "$src" ] || continue
  mkdir -p "$OUT/$cam"
  ffmpeg -hide_banner -loglevel error -y -ss "$START" -i "$src" -t "$LEN" \
    -vf "fps=$RATE,scale=800:-1" \
    "$OUT/$cam/%03d.jpg"
  echo "$cam ✅ $(ls "$OUT/$cam" | wc -l | tr -d ' ')장"
done
(cd "$WORK" && zip -qr "$(basename "$OUT").zip" "$(basename "$OUT")")
echo "결과: $OUT  /  $OUT.zip"
