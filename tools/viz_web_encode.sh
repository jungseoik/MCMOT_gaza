#!/usr/bin/env bash
# 시각화 mp4 를 **어디서나 재생되게** 다시 인코딩한다.
#
# 원래 출력은 그리드 크기 그대로라 3262x1350(level 5.0) 같은 것이 나오고,
# 5fps·키프레임 3개·무음성 트랙이라 윈도우 기본 재생기에서 막바지에 끊기거나
# 갑자기 닫히는 일이 있었다. 아래 값들이 그 원인을 하나씩 없앤다.
#
#   scale ≤1920      level 4.0 안으로 (하드웨어 디코더 상한을 넘지 않게)
#   fps 30           5fps 는 재생기마다 처리가 제각각이다
#   -g 60            2초마다 키프레임 — 한 번 튀어도 바로 복구된다
#   무음 aac         영상만 있는 mp4 를 못 다루는 재생기가 있다
#   +faststart       moov 를 앞으로 (스트리밍·부분 재생)
#
#   bash tools/viz_web_encode.sh results/rehearsal_viz_drill1f3f
set -euo pipefail
DIR="${1:?사용법: viz_web_encode.sh <폴더> [최대폭]}"
MAXW="${2:-1920}"
OUT="$DIR/web"
mkdir -p "$OUT"
n=0
for f in "$DIR"/*.mp4; do
  [ -e "$f" ] || continue
  b=$(basename "$f")
  [ -f "$OUT/$b" ] && { echo "  있음, 생략: $b"; continue; }
  echo "  → $b"
  ffmpeg -hide_banner -loglevel error -y -i "$f" \
    -f lavfi -i anullsrc=r=48000:cl=stereo -shortest \
    -vf "scale='min($MAXW,iw)':-2:flags=lanczos,fps=30" \
    -c:v libx264 -preset medium -crf 21 -profile:v high -level 4.0 -pix_fmt yuv420p \
    -g 60 -keyint_min 30 -sc_threshold 0 -c:a aac -b:a 64k \
    -movflags +faststart "$OUT/$b"
  n=$((n+1))
done
echo "완료 $n개 → $OUT"
