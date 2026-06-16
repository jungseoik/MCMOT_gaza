#!/usr/bin/env bash
# Build ONE overview montage (a mid-frame thumbnail per video) from a folder of
# mp4s — so a whole batch of concat results can be reviewed at a glance.
#
# Usage: tools/montage.sh <dir> [out.png] [cols]
#   dir   folder containing *.mp4 (e.g. results/clab_concat)
#   out   output image           (default <dir>/overview_montage.png)
#   cols  grid columns           (default 4)
set -euo pipefail
DIR="${1:?usage: tools/montage.sh <dir> [out.png] [cols]}"
OUT="${2:-$DIR/overview_montage.png}"
COLS="${3:-4}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

i=0
echo "index -> video"
for f in "$DIR"/*.mp4; do
  [ -e "$f" ] || { echo "no mp4 in $DIR"; exit 1; }
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")
  mid=$(awk "BEGIN{print ($dur>0?$dur*0.4:1)}")
  ffmpeg -y -loglevel error -ss "$mid" -i "$f" -vframes 1 \
    -vf "scale=480:135:force_original_aspect_ratio=decrease,pad=480:135:(ow-iw)/2:(oh-ih)/2:color=white" \
    "$TMP/thumb_$(printf %03d "$i").png" 2>/dev/null || true
  printf "%02d  %s\n" "$i" "$(basename "${f%.mp4}")"
  i=$((i+1))
done

n=$(ls "$TMP"/thumb_*.png | wc -l)
rows=$(( (n + COLS - 1) / COLS ))
ffmpeg -y -loglevel error -pattern_type glob -i "$TMP/thumb_*.png" \
  -vf "tile=${COLS}x${rows}:padding=4:color=0x222222" -frames:v 1 "$OUT"
echo "montage -> $OUT  ($n thumbs, ${COLS}x${rows})"
