#!/usr/bin/env bash
# STEP 10 — 시나리오별 확인용 그리드 (패널을 대피 경로 순으로 배치)
#
# work/scenarios.csv 의 cams 컬럼이 이미 경로 순으로 정렬돼 있다. 그 순서대로
# 왼쪽 위 → 오른쪽 → 다음 줄로 채운다.
#
# 주의:
#  - bash 로 실행할 것 (zsh 는 cams 목록을 워드 분할하지 않는다).
#  - xstack 은 grid=CxR 대신 layout= 으로 좌표를 직접 준다. grid= 는 정확히 C*R 개
#    입력을 요구하는데 대수가 격자에 딱 안 맞는 경우가 많고 fill=black 이 빈 칸을
#    채워주지 않는다.
#  - 타일이 16:9 이므로 C≈R 일 때 전체가 16:9 가 된다 → C = ceil(sqrt(n)).
source "$(dirname "$0")/00_config.sh"

CRF="${CRF:-24}"
MAP="$WORK/grid_layouts.txt"
: > "$MAP"

tail -n +2 "$WORK/scenarios.csv" | tr -d '\r' | while IFS=, read -r name s e sync dur cams; do
  [ -n "$name" ] || continue
  dest="$SCEN/$name"; [ -d "$dest" ] || { echo "스킵: $dest 없음"; continue; }

  set -- $cams; n=$#
  C=$(awk -v n="$n" 'BEGIN{c=int(sqrt(n)); if(c*c<n)c++; print c}')
  R=$(( (n + C - 1) / C ))
  TW=$(( 1920 / C )); TW=$(( TW - TW % 2 )); TH=$(( TW * 9 / 16 )); TH=$(( TH - TH % 2 ))

  # 주의: 여기서 `{ ... } | tee` 로 묶으면 파이프가 서브셸을 만들어 ins/fc/lay 가
  # 루프 밖으로 전달되지 않는다. 배열은 현재 셸에서 만들고, 출력만 따로 기록한다.
  ins=(); fc=""; lbl=""; lay=""; i=0
  msg="=== $name  ($s ~ $e, ${dur}초)  ${C}x${R}  $((C*TW))x$((R*TH)) ==="
  for c in $cams; do
    f="$dest/${c}.mp4"
    [ -f "$f" ] || { msg+=$'\n'"  ⚠️ $f 없음"; continue; }
    ins+=(-i "$f")
    fc+="[$i:v]scale=$TW:$TH,setsar=1[v$i];"
    lbl+="[v$i]"
    [ -n "$lay" ] && lay+="|"
    lay+="$(( (i%C)*TW ))_$(( (i/C)*TH ))"
    msg+=$'\n'"$(printf '  [%d행 %d열] %s' $((i/C+1)) $((i%C+1)) "$c")"
    i=$((i+1))
  done
  printf '%s\n' "$msg" | tee -a "$MAP"

  fc+="${lbl}xstack=inputs=$i:layout=${lay}:fill=black[o]"
  ffmpeg -hide_banner -loglevel error -nostdin -y "${ins[@]}" -filter_complex "$fc" \
    -map "[o]" -an -c:v libx264 -crf "$CRF" -preset veryfast -pix_fmt yuv420p \
    "$dest/grid_${name}.mp4"
  echo "  → $WORK/grid_${name}.mp4  $(du -h "$dest/grid_${name}.mp4" | cut -f1)"
done
echo
echo "배치표: $MAP"
