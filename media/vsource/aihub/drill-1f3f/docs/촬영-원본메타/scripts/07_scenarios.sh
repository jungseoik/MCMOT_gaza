#!/usr/bin/env bash
# STEP 6 — 시나리오별 분할. work/scenarios.csv 기반
# CSV 형식:  name,start,duration,cams
#   scenario_01,00:03:12,45,all
#   scenario_02,00:08:40,120,cam1 cam3 cam5 cam9
#
#   ./07_scenarios.sh              전체 시나리오
#   ./07_scenarios.sh scenario_02  하나만
source "$(dirname "$0")/00_config.sh"
CSV="$WORK/scenarios.csv"
ONLY="${1:-}"
[ -f "$CSV" ] || { echo "❌ $CSV 없음"; echo "name,start,duration,cams" > "$CSV"; echo "예시 헤더 생성됨 — 채운 뒤 다시 실행"; exit 1; }

tail -n +2 "$CSV" | grep -v '^[[:space:]]*$' | while IFS=, read -r name start dur cams; do
  [ -n "$name" ] || continue
  [ -n "$ONLY" ] && [ "$ONLY" != "$name" ] && continue

  if [ "$cams" = "all" ] || [ -z "$cams" ]; then list=$(present_cams); else list=$cams; fi
  dest="$SCEN/$name"; mkdir -p "$dest"
  echo "=== $name  $start +${dur}s ==="

  for cam in $list; do
    src="$SYNC/${cam}_sync.mp4"
    [ -f "$src" ] || { echo "  ⚠️ $src 없음"; continue; }
    ffmpeg -hide_banner -loglevel warning -y -ss "$start" -i "$src" -t "$dur" \
      -c copy "$dest/${cam}.mp4"
    echo "  $cam ✅"
  done

  # 시나리오 그리드
  "$(dirname "$0")/06_grid.sh" "03_scenarios/$name" 0 0
done
