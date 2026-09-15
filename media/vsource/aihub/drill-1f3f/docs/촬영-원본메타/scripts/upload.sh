#!/usr/bin/env bash
# hf_transfer 사용 업로드. .DS_Store / hf 캐시 제외
#
#   ./scripts/upload.sh 01_original          폴더 통째로
#   ./scripts/upload.sh 01_original/cam1     카메라 하나만
#   ./scripts/upload.sh 03_scenarios         시나리오 (trace·ssave 자동 제외)
#
# 03_scenarios 에는 이번 납품 대상이 아닌 2차 촬영분(trace_senario*, ssave_senario*)이
# 섞여 있어 자동으로 뺀다. 굳이 포함하려면 KEEP_ALL=1 을 준다.
# REPO 로 대상 저장소를 덮어쓸 수 있다.
set -e
cd /Users/dohyeong/Desktop/camera_data
REPO="${REPO:-PIA-SPACE/C-lab-AIHub-2026-09-11}"
T="$1"
[ -n "$T" ] || { echo "사용법: $0 <업로드할 경로>"; exit 1; }
[ -e "$T" ] || { echo "없는 경로: $T"; exit 1; }

# 주의: "**/.DS_Store" 는 하위 폴더만 잡고 업로드 루트 바로 아래 것은 못 거른다.
# ".DS_Store" 패턴을 반드시 함께 준다.
EX=(--exclude ".DS_Store" --exclude "**/.DS_Store" --exclude "**/.cache/**")
case "$T" in
  03_scenarios|03_scenarios/)
    if [ -z "${KEEP_ALL:-}" ]; then
      EX+=(--exclude "trace_senario*/**" --exclude "ssave_senario*/**")
      echo "제외: trace_senario*, ssave_senario*"
    fi ;;
esac

echo "업로드: $T  →  $REPO"
export HF_HUB_ENABLE_HF_TRANSFER=1
exec caffeinate -i work/venv/bin/hf upload "$REPO" "$T" "$T" \
  --repo-type=dataset --private "${EX[@]}"
