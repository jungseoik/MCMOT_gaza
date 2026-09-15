#!/usr/bin/env bash
# STEP 5b / 6b — 싱크 확인용 그리드 (카메라 대수에 맞춰 레이아웃 자동)
# 사용법:
#   ./06_grid.sh                                   04_sync 전체, 처음 60초
#   ./06_grid.sh 04_sync 00:02:00 30               구간 지정
#   ./06_grid.sh 03_scenarios/scenario_01 0 0      시나리오 폴더 전체 길이
source "$(dirname "$0")/00_config.sh"
SRC="${1:-04_sync}"; START="${2:-0}"; LEN="${3:-60}"

case "$SRC" in
  04_sync)   SDIR="$SYNC";   FILES=(); for c in $(present_cams); do [ -f "$SYNC/${c}_sync.mp4" ] && FILES+=("$SYNC/${c}_sync.mp4"); done;;
  02_concat) SDIR="$CONCAT"; FILES=(); for c in $(present_cams); do [ -f "$CONCAT/${c}_full.mp4" ] && FILES+=("$CONCAT/${c}_full.mp4"); done;;
  *)         SDIR="$ROOT/$SRC"; FILES=(); while IFS= read -r f; do FILES+=("$f"); done < <(find "$SDIR" -maxdepth 1 -name 'cam*.mp4' | sort -V);;
esac

n=${#FILES[@]}; [ "$n" -gt 0 ] || { echo "❌ 입력 영상 없음: $SDIR"; exit 1; }
# 레이아웃 열 수
case $n in 1)C=1;;2)C=2;;3)C=3;;4)C=2;;5|6)C=3;;7|8)C=4;;9)C=3;;10)C=5;;11|12)C=4;;13|14|15)C=5;;16|17|18)C=6;;19|20|21|22|23|24)C=6;;*)C=6;;esac
R=$(( (n + C - 1) / C ))
echo "카메라 ${n}대 → ${C}x${R} 그리드 ($((C*GRID_W))x$((R*GRID_H)))"
for i in "${!FILES[@]}"; do printf "  [%d행 %d열] %s\n" $((i/C+1)) $((i%C+1)) "$(basename "${FILES[$i]}" .mp4)"; done

ins=(); fc=""; labels=""
for i in "${!FILES[@]}"; do
  if [ "$START" != "0" ]; then ins+=(-ss "$START"); fi
  if [ "$LEN" != "0" ]; then ins+=(-t "$LEN"); fi
  ins+=(-i "${FILES[$i]}")
  name=$(basename "${FILES[$i]}" .mp4)
  fc+="[$i:v]scale=$GRID_W:$GRID_H:force_original_aspect_ratio=decrease,pad=$GRID_W:$GRID_H:(ow-iw)/2:(oh-ih)/2,setsar=1[v$i];"
  labels+="[v$i]"
done
# xstack grid=CxR 는 정확히 C*R 개 입력을 요구하므로(fill=black 이 빈 칸을 채워주지
# 않는다) 항상 layout= 으로 좌표를 직접 지정한다. 27대처럼 격자에 딱 떨어지지
# 않는 대수에서도 안전하다.
lay=""
for i in "${!FILES[@]}"; do
  [ -n "$lay" ] && lay+="|"
  lay+="$(( (i%C)*GRID_W ))_$(( (i/C)*GRID_H ))"
done
fc+="${labels}xstack=inputs=$n:layout=${lay}:fill=black:shortest=1[out]"

outname="$WORK/grid_$(basename "$SRC")_${START//:/}.mp4"
[ "$SRC" != "04_sync" ] && [ "$SRC" != "02_concat" ] && outname="$SDIR/grid_$(basename "$SDIR").mp4"

ffmpeg -hide_banner -loglevel warning -y "${ins[@]}" \
  -filter_complex "$fc" -map "[out]" \
  -c:v libx264 -crf 28 -preset ultrafast -pix_fmt yuv420p "$outname"
echo "✅ $outname"
