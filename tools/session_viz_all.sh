#!/usr/bin/env bash
# 저장된 전 세션을 session_viz 로 일괄 렌더한다.
#
#   bash tools/session_viz_all.sh [동시실행수] [출력폴더]
#
# 이미 만들어진 mp4 는 건너뛴다(중단 후 이어서 돌리기). 로그는 출력폴더/_log/.
set -u
JOBS="${1:-3}"
OUT="${2:-results/session_viz}"
cd "$(dirname "$0")/.."
mkdir -p "$OUT/_log"

mapfile -t SIDS < <(ls data/sites/default/sessions/_drills/*.json 2>/dev/null \
                    | xargs -r -n1 basename | sed 's/\.json$//' | sort)
echo "[all] 세션 ${#SIDS[@]}건 · 동시 ${JOBS} · 출력 ${OUT}"

render() {
  local sid="$1" fl="$2" out="$3"
  local mp4="$out/${sid}_${fl}.mp4"
  local log="$out/_log/${sid}_${fl}.log"
  if [ -s "$mp4" ]; then echo "  skip  ${sid} ${fl} (이미 있음)"; return 0; fi
  if python tools/session_viz.py --session "$sid" --floor "$fl" --out "$out" \
        --width 1920 >"$log" 2>&1; then
    echo "  ok    ${sid} ${fl}  $(du -h "$mp4" 2>/dev/null | cut -f1)"
  else
    echo "  FAIL  ${sid} ${fl}  → ${log}"
    tail -3 "$log" | sed 's/^/        /'
  fi
}
export -f render

# 세션 × 녹화가 있는 층
: > "$OUT/_log/_targets.txt"
for sid in "${SIDS[@]}"; do
  for d in data/sites/default/sessions/floor*/; do
    fl="$(basename "$d")"
    [ -f "${d}${sid}.db" ] && echo "$sid $fl" >> "$OUT/_log/_targets.txt"
  done
done
N=$(wc -l < "$OUT/_log/_targets.txt")
echo "[all] 대상 ${N}건 (세션×층)"

xargs -a "$OUT/_log/_targets.txt" -P "$JOBS" -L1 \
      bash -c 'render "$0" "$1" "'"$OUT"'"'

echo "[all] 완료 — mp4 $(ls "$OUT"/*.mp4 2>/dev/null | wc -l)개 · $(du -sh "$OUT" 2>/dev/null | cut -f1)"
