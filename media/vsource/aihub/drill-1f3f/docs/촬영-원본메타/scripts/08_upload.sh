#!/usr/bin/env bash
# STEP 7 — 15GB 분할 압축 + Hugging Face 개별 업로드
#   ./08_upload.sh pack 03_scenarios     압축만
#   ./08_upload.sh push 03_scenarios     업로드만 (파일 하나씩)
#   ./08_upload.sh verify                레포 파일 목록 확인
source "$(dirname "$0")/00_config.sh"
REPO="PIA-SPACE/C-lab"
CMD="${1:?pack|push|verify}"; TARGET="${2:-}"

case "$CMD" in
  pack)
    cd "$ROOT"
    7z a -v15g "${TARGET}.7z" "${TARGET}/"
    ls -lh "${TARGET}".7z.* ;;
  push)
    cd "$ROOT"
    for f in "${TARGET}".7z.*; do
      echo "=== 업로드: $f ==="
      caffeinate -i hf upload "$REPO" "$f" --repo-type=dataset || { echo "❌ $f 실패"; exit 1; }
    done ;;
  verify)
    python3 -c "
from huggingface_hub import HfApi
for f in sorted(HfApi().list_repo_files('$REPO', repo_type='dataset')): print(f)
" ;;
esac
