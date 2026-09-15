#!/usr/bin/env bash
# STEP 9 — 시나리오 구간 분할
#
# work/scenarios.csv : name,start_mmss,end_mmss,sync_start,dur,cams
#   sync_start 는 그리드 영상(= 슬레이트 접촉을 0으로 잡은 타임라인) 기준 초.
#   cams 는 공백으로 구분한 카메라 목록.
# work/offsets.csv   : cam,offset,memo   →  concat_time = sync_time + offset
#
# 주의:
#  - 반드시 bash 로 실행한다. zsh 는 unquoted 변수를 워드 분할하지 않아 cams 목록이
#    통째로 파일명 하나가 되어버린다.
#  - ffmpeg 에 -nostdin 필수. 없으면 while 루프가 읽던 CSV 를 ffmpeg 가 먹어치워
#    두 번째 행부터 사라진다.
#  - -c copy 금지. GOP 경계(30fps 0.5초) 때문에 프레임 단위로 못 자른다. 재인코딩한다.
source "$(dirname "$0")/00_config.sh"

CRF="${CRF:-16}"
getoff() { awk -F, -v c="$1" '$1==c{print $2}' "$WORK/offsets.csv"; }

tail -n +2 "$WORK/scenarios.csv" | tr -d '\r' | while IFS=, read -r name s e sync dur cams; do
  [ -n "$name" ] || continue
  dest="$SCEN/$name"; mkdir -p "$dest"
  echo "=== $name  ($s ~ $e, ${dur}초) ==="
  for c in $cams; do
    off=$(getoff "$c")
    [ -n "$off" ] || { echo "  ⚠️ $c offset 없음 → 건너뜀"; continue; }
    st=$(awk -v s="$sync" -v o="$off" 'BEGIN{printf "%.4f", s+o}')
    ffmpeg -hide_banner -loglevel error -nostdin -y -ss "$st" -i "$CONCAT/${c}_full.mp4" -t "$dur" \
      -vf "fps=$FPS,setpts=N/$FPS/TB" -an -c:v libx264 -crf "$CRF" -preset medium -pix_fmt yuv420p \
      -metadata "comment=cam=$c scenario=$name grid=${s}-${e} sync_start=$sync dur=$dur" \
      "$dest/${c}.mp4"
    vd=$(ffprobe -v error -select_streams v:0 -show_entries stream=duration -of csv=p=0 "$dest/${c}.mp4")
    nb=$(ffprobe -v error -select_streams v:0 -show_entries stream=nb_frames -of csv=p=0 "$dest/${c}.mp4")
    printf "  %-7s %8.3f초 / %6s프레임 / %6s" "$c" "$vd" "$nb" "$(du -h "$dest/${c}.mp4" | cut -f1)"
    awk -v a="$dur" -v b="$vd" -v n="$nb" -v f="$FPS" 'BEGIN{d=a-b; if(d<0)d=-d;
      if(d>0.05 || n!=a*f) printf "  ⚠️ 기대 %d프레임\n", a*f; else print "  ✅"}'
  done
done
