#!/usr/bin/env bash
# 업로드 진행률 — 파일 수 기준 (바이트 카운터는 재시작 시 0부터 다시 시작하므로 신뢰 불가)
#   ./scripts/watch_upload.sh work/upload_03.log
LOG="${1:-work/upload_03.log}"
start=$(date +%s); base=""
while true; do
  line=$(tail -c 3000 "$LOG" 2>/dev/null | tr '\r' '\n' | grep 'uploaded' | tail -1)
  if [ -z "$line" ]; then printf "\r해시 계산 중...                              "; sleep 3; continue; fi
  up=$(echo "$line"  | sed -n 's/.*checked, \([0-9]*\)\/\([0-9]*\) uploaded.*/\1/p')
  tot=$(echo "$line" | sed -n 's/.*checked, \([0-9]*\)\/\([0-9]*\) uploaded.*/\2/p')
  by=$(echo "$line"  | sed -n 's/.*(\([0-9.]*\)\([MGk]*\)B transferred).*/\1\2/p')
  [ -z "$base" ] && base=$up
  el=$(( $(date +%s) - start )); [ "$el" -eq 0 ] && el=1
  done_now=$(( up - base ))
  if [ "$done_now" -gt 0 ] && [ "$up" -lt "$tot" ]; then
    eta=$(awk -v r="$done_now" -v e="$el" -v n="$((tot-up))" 'BEGIN{printf "%.0f", n*e/r/60}')
    etatxt="남은시간 약 ${eta}분"
  else etatxt="예상 계산 중"; fi
  pct=$(awk -v u="$up" -v t="$tot" 'BEGIN{if(t>0)printf "%.1f", u*100/t; else print 0}')
  printf "\r파일 %s/%s (%s%%)  이번 실행 전송 %s  경과 %d분  %s        " \
    "$up" "$tot" "$pct" "${by:-0B}" $((el/60)) "$etatxt"
  grep -q "^url=" "$LOG" 2>/dev/null && { echo; echo "✅ 커밋 완료"; break; }
  grep -qiE "error|traceback" "$LOG" 2>/dev/null && { echo; echo "⚠️ 로그에 오류 있음 — 확인 필요"; break; }
  sleep 5
done
