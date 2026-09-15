#!/usr/bin/env bash
# 슬레이트② 기점 18대 전체 그리드 (B안)
set -e
cd /Users/dohyeong/Desktop/camera_data
S2=1601.7896
START=$(awk -v s=$S2 'BEGIN{printf "%.4f", s-5}')      # 슬레이트② 5초 전
DUR=3638                                                # cam10 종료까지
CAMS=(cam1 cam2 cam3 cam4 cam5 cam6 cam7 cam8 cam9 cam10 cam11 cam12 cam13 cam14 cam15 cam16 cam17 cam18)
ins=(); fc=""; lbl=""; i=0
for cam in "${CAMS[@]}"; do
  off=$(awk -F, -v c="$cam" '$1==c{print $2}' work/offsets.csv)
  st=$(awk -v s=$START -v o=$off 'BEGIN{printf "%.4f", s+o}')
  ins+=(-ss "$st" -i "02_concat/${cam}_full.mp4")
  fc+="[$i:v]fps=30,scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2,setsar=1,tpad=stop_mode=add:stop_duration=4000:color=black[v$i];"
  lbl+="[v$i]"; i=$((i+1))
done
fc+="${lbl}xstack=inputs=18:grid=6x3:fill=black[o]"
ffmpeg -hide_banner -loglevel warning -y -stats "${ins[@]}" -filter_complex "$fc" -map "[o]" \
  -an -t "$DUR" -c:v libx264 -crf 26 -preset medium -pix_fmt yuv420p \
  04_sync/grid_all18_slate2_full.mp4
