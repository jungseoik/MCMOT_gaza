#!/usr/bin/env bash
# 모든 카메라의 모든 파일 이음새에서 중복 구간을 프레임 해시로 실측
cd /Users/dohyeong/Desktop/camera_data/01_original
out=/Users/dohyeong/Desktop/camera_data/work/overlap_report.csv
echo "cam,file_a,file_b,dup_frames,dup_sec" > "$out"
for d in $(ls | sort -V); do
  files=($(ls "$d" | sort -V)); n=${#files[@]}
  for ((i=1;i<n;i++)); do
    a="$d/${files[$i]}"; b="$d/${files[$i+1]}"
    ad=$(ffprobe -v error -select_streams v:0 -show_entries stream=duration -of default=nw=1:nk=1 "$a")
    st=$(echo "$ad - 2" | bc)
    ffmpeg -v error -ss "$st" -i "$a" -an -f framemd5 - 2>/dev/null | grep -v '^#' | awk -F, '{print $6}' | tr -d ' ' > /tmp/ov_a
    ffmpeg -v error -i "$b" -t 3 -an -f framemd5 - 2>/dev/null | grep -v '^#' | awk -F, '{print $6}' | tr -d ' ' > /tmp/ov_b
    # a의 꼬리와 b의 머리가 겹치는 최대 길이 탐색
    dup=0
    na=$(wc -l < /tmp/ov_a)
    for ((k=na;k>=1;k--)); do
      tail -n "$k" /tmp/ov_a > /tmp/ov_t
      head -n "$k" /tmp/ov_b > /tmp/ov_h
      if cmp -s /tmp/ov_t /tmp/ov_h; then dup=$k; break; fi
    done
    echo "$d,${files[$i]},${files[$i+1]},$dup,$(echo "scale=3;$dup/30"|bc)" | tee -a "$out"
  done
done
echo "DONE" >> "$out"
