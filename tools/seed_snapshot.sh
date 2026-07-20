#!/bin/bash
# 현재 라이브 사이트 세팅(data/sites/<site_id>/)을 seed(data/seed/<site_id>/)로 스냅샷.
# seed는 git에 커밋되는 디폴트 UI 세팅 — 새 서버 클론 후 첫 기동 시 자동 복사된다.
# 사용: bash tools/seed_snapshot.sh [site_id]   (기본 default)
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SITE_ID="${1:-default}"
SRC="$REPO_ROOT/data/sites/$SITE_ID"
DST="$REPO_ROOT/data/seed/$SITE_ID"

[ -f "$SRC/site.json" ] || { echo "오류: $SRC/site.json 없음"; exit 1; }

mkdir -p "$DST"
# sessions(실행 이력)는 seed에 포함하지 않는다
rsync -a --delete --exclude 'sessions/' --exclude '*.tmp' "$SRC/" "$DST/"
echo "seed 갱신 완료: $DST"
du -sh "$DST"
