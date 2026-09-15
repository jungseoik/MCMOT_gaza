#!/usr/bin/env bash
# offsets.csv 템플릿 생성 + 각 카메라 GOP(키프레임 간격) 측정
# GOP 값 = copy 모드로 자를 때 발생할 수 있는 최대 싱크 오차(초)
source "$(dirname "$0")/00_config.sh"
CSV="$WORK/offsets.csv"

if [ -f "$CSV" ]; then echo "이미 존재: $CSV (덮어쓰지 않음)"; else
  echo "cam,offset,memo" > "$CSV"
  for cam in $(present_cams); do echo "$cam,0,"; done >> "$CSV"
  echo "생성: $CSV — offset 열에 초 또는 HH:MM:SS.mmm 기입"
fi

echo
echo "--- 카메라별 키프레임 간격 (copy 모드 최대 오차) ---"
for cam in $(present_cams); do
  f="$CONCAT/${cam}_full.mp4"; [ -f "$f" ] || continue
  kf=$(ffprobe -v error -select_streams v:0 -skip_frame nokey \
        -show_entries frame=pts_time -of csv=p=0 -read_intervals "%+20" "$f" 2>/dev/null \
        | tr -d ',' | awk 'NF' | head -10)
  gap=$(echo "$kf" | awk 'NR>1{d=$1-p; s+=d; n++} {p=$1} END{if(n)printf "%.2f", s/n; else print "?"}')
  printf "%-6s GOP≈%ss  → 프레임단위 정밀 싱크 필요하면 exact 모드\n" "$cam" "$gap"
done
