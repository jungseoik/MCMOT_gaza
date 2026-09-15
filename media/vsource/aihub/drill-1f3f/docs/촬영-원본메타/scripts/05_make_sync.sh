#!/usr/bin/env bash
# STEP 5 — 오프셋 적용해 04_sync 생성
# work/offsets.csv 를 읽어 카메라별로 개별 명령 실행 (한 번에 넘기지 않음)
#
#   ./05_make_sync.sh          # copy 모드: 무손실·빠름, 컷이 키프레임으로 스냅 (±GOP 오차)
#   ./05_make_sync.sh exact    # 재인코딩: 프레임 정확, 느림
#   ./05_make_sync.sh copy cam3 cam7   # 특정 카메라만 다시
source "$(dirname "$0")/00_config.sh"

MODE="${1:-copy}"; shift || true
TARGETS=("$@"); [ ${#TARGETS[@]} -eq 0 ] && TARGETS=($(present_cams))

CSV="$WORK/offsets.csv"
[ -f "$CSV" ] || { echo "❌ $CSV 없음 — ./make_offsets_template.sh 먼저 실행"; exit 1; }

# HH:MM:SS.mmm 또는 초 → 초
to_sec() { case "$1" in *:*) awk -F: '{print ($1*3600)+($2*60)+$3}' <<<"$1";; *) echo "$1";; esac; }

for cam in "${TARGETS[@]}"; do
  off=$(awk -F, -v c="$cam" '$1==c{print $2; exit}' "$CSV")
  [ -n "$off" ] || { echo "⚠️  $cam 오프셋 없음 → 스킵"; continue; }
  off=$(to_sec "$off")
  src="$CONCAT/${cam}_full.mp4"; out="$SYNC/${cam}_sync.mp4"
  [ -f "$src" ] || { echo "⚠️  $src 없음"; continue; }

  echo "=== $cam  -ss $off  ($MODE) ==="
  if [ "$MODE" = "exact" ]; then
    ffmpeg -hide_banner -loglevel warning -y -ss "$off" -i "$src" \
      -c:v libx264 -crf 18 -preset medium -c:a aac -b:a 192k "$out"
  else
    ffmpeg -hide_banner -loglevel warning -y -ss "$off" -i "$src" -c copy "$out"
  fi

  # 실제 적용된 시작점 검증 (copy 모드는 요청값과 다를 수 있음)
  real=$(ffprobe -v error -select_streams v:0 -show_entries packet=pts_time \
           -read_intervals "%+#1" -of csv=p=0 "$src" 2>/dev/null | head -1)
  d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$out")
  printf "  → %s  길이 %.3fs\n" "$(basename "$out")" "$d"
done

echo
echo "--- 04_sync 길이 비교 (동일 이벤트가 같은 타임코드에 있어야 함) ---"
for cam in $(present_cams); do
  f="$SYNC/${cam}_sync.mp4"; [ -f "$f" ] || continue
  printf "%-6s %s\n" "$cam" "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")"
done
