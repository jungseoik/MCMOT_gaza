#!/usr/bin/env bash
# 공통 설정 — 모든 스크립트가 source 함
set -euo pipefail

ROOT="$HOME/Desktop/camera_data"
ORIG="$ROOT/01_original"
CONCAT="$ROOT/02_concat"
SCEN="$ROOT/03_scenarios"
SYNC="$ROOT/04_sync"
WORK="$ROOT/work"

# 카메라 목록 (실제 촬영된 대수만 남기면 나머지 스크립트 전부 자동 대응)
CAMS=(cam1 cam2 cam3 cam4 cam5 cam6 cam7 cam8 cam9 cam10 cam11 cam12 cam13 cam14 cam15 cam16 cam17 cam18 cam19 cam31 cam32 cam33 cam34 cam35 cam36 cam37 cam38 cam39)

FPS=30              # 프레임 환산 기준 (1프레임 = 1/FPS 초)
GRID_W=320          # 그리드 타일 가로 (28대 6x5 = 1920x900)
GRID_H=180

mkdir -p "$CONCAT" "$SCEN" "$SYNC" "$WORK"

# 실제 존재하는 카메라만 반환
present_cams() {
  local c
  for c in "${CAMS[@]}"; do
    [ -d "$ORIG/$c" ] && [ -n "$(ls -A "$ORIG/$c" 2>/dev/null)" ] && echo "$c"
  done
}
