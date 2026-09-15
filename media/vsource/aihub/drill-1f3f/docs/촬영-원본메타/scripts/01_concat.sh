#!/usr/bin/env bash
# STEP 1 — 카메라별 원본 이어붙이기 (파일 경계 1초 중복 제거 + 녹화 세션 분리)
#
# 액션캠이 파일을 자동 분할할 때 이전 파일 끝 1초를 다음 파일 앞에 다시 씀 (프레임 해시로 실측 확인).
# 따라서 같은 세션 안에서는 첫 파일을 제외한 모든 파일에 inpoint 1.0 을 걸어 중복분을 건너뛴다.
# duration 지시어도 반드시 함께 지정한다 — 없으면 concat 디먹서가 세그먼트 길이를
# 오디오 길이(영상보다 0.088초 김)로 잡아 이음새에 영상 타임스탬프 갭이 생긴다.
# 1.000초는 키프레임 경계(0.5초 간격, 60fps 카메라는 0.25초)와 정확히 맞아 -c copy 무손실.
#
# 【세션 분리】촬영자가 녹화를 멈췄다 다시 시작하면 그 경계에는 중복이 없고 대신 실제
# 시간 공백이 있다. 거기에 inpoint 1.0 을 걸면 실제 내용 1초가 잘리고, 그대로 이어붙이면
# 공백이 사라져 타임라인이 실제와 어긋난다. 파일명의 녹화 시작 시각(YYYY_MMDD_HHMMSS)과
# 직전 파일의 끝 시각을 비교해 공백이 GAP_MIN초를 넘으면 별도 세션으로 끊어 따로 출력한다.
#   세션 1개  → 02_concat/camN_full.mp4
#   세션 2개+ → 02_concat/camN_s1_full.mp4, camN_s2_full.mp4 …
#
# 【시각 필터】FROM / TO 환경변수(HHMM 또는 HHMMSS)를 주면 파일명의 녹화 시작 시각이
# 그 범위인 파일만 합친다. 특정 촬영 블록만 뽑을 때 쓴다.
#
#   ./01_concat.sh                       전체
#   ./01_concat.sh cam3 cam7             특정 카메라만
#   FROM=1545 ./01_concat.sh cam31 cam32 15:45 이후 녹화분만
source "$(dirname "$0")/00_config.sh"

TRIM=1.0                        # 같은 세션 내 파일 간 중복 길이(초)
GAP_MIN=1.5                     # 이 초를 넘는 공백은 별도 녹화 세션으로 간주
TARGETS=("$@"); [ ${#TARGETS[@]} -eq 0 ] && TARGETS=($(present_cams))

# HHMM / HHMMSS → 자정 기준 초
to_sec() { local t="$1"; [ ${#t} -eq 4 ] && t="${t}00"
  echo $(( 10#${t:0:2}*3600 + 10#${t:2:2}*60 + 10#${t:4:2} )); }
FROM_S=$([ -n "${FROM:-}" ] && to_sec "$FROM" || echo -1)
TO_S=$([ -n "${TO:-}" ] && to_sec "$TO" || echo 999999)
[ "$FROM_S" -ge 0 ] || [ "$TO_S" -lt 999999 ] && \
  echo "시각 필터: ${FROM:-처음} ~ ${TO:-끝}"

for cam in "${TARGETS[@]}"; do
  cdir="$ORIG/$cam"
  [ -d "$cdir" ] || { echo "스킵: $cdir 없음"; continue; }

  files=()
  while IFS= read -r f; do files+=("$f"); done < <(find "$cdir" -maxdepth 1 -type f \
      \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.mts' \) | sort -V)
  # 시각 필터
  if [ "$FROM_S" -ge 0 ] || [ "$TO_S" -lt 999999 ]; then
    kept=()
    for f in "${files[@]}"; do
      hms=$(basename "$f" | sed -E 's/.*_([0-9]{6})_.*/\1/')
      fs=$(( 10#${hms:0:2}*3600 + 10#${hms:2:2}*60 + 10#${hms:4:2} ))
      [ "$fs" -ge "$FROM_S" ] && [ "$fs" -le "$TO_S" ] && kept+=("$f")
    done
    files=("${kept[@]:-}"); [ -n "${files[0]:-}" ] || files=()
  fi
  n=${#files[@]}
  [ "$n" -gt 0 ] || { echo "스킵: $cam 해당 시각 범위 영상 없음"; continue; }

  # 파일별 길이 + 파일명 시작 시각(초) → 세션 경계 판정
  durs=(); starts=(); breaks=()
  for f in "${files[@]}"; do
    durs+=("$(ffprobe -v error -select_streams v:0 -show_entries stream=duration -of default=nw=1:nk=1 "$f")")
    hms=$(basename "$f" | sed -E 's/.*_([0-9]{6})_.*/\1/')
    starts+=("$(( 10#${hms:0:2}*3600 + 10#${hms:2:2}*60 + 10#${hms:4:2} ))")
  done
  for ((i=1;i<n;i++)); do
    gap=$(awk -v s="${starts[$i]}" -v p="${starts[$((i-1))]}" -v d="${durs[$((i-1))]}" \
              'BEGIN{printf "%.3f", s-(p+d)}')
    breaks+=("$(awk -v g="$gap" -v m="$GAP_MIN" 'BEGIN{print (g>m)?1:0}')")
  done

  nsess=1; for b in "${breaks[@]:-}"; do [ "${b:-0}" = "1" ] && nsess=$((nsess+1)); done
  echo "=== $cam : ${n}개 파일 / 녹화 세션 ${nsess}개 ==="

  # 세션별로 묶어서 각각 concat
  s=1; si=0
  while [ "$si" -lt "$n" ]; do
    # 이번 세션에 속하는 파일 범위 [si, ei)
    ei=$((si+1))
    while [ "$ei" -lt "$n" ] && [ "${breaks[$((ei-1))]}" = "0" ]; do ei=$((ei+1)); done

    if [ "$nsess" -eq 1 ]; then tag=""; else tag="_s$s"; fi
    out="$CONCAT/${cam}${tag}_full.mp4"
    list="$CONCAT/${cam}${tag}_filelist.txt"

    : > "$list"; sum=0
    for ((i=si;i<ei;i++)); do
      printf "file '%s'\n" "${files[$i]//\'/\'\\\'\'}" >> "$list"
      if [ "$i" -gt "$si" ]; then
        echo "inpoint $TRIM"                                   >> "$list"
        echo "duration $(echo "${durs[$i]} - $TRIM" | bc)"      >> "$list"
        sum=$(echo "$sum + ${durs[$i]} - $TRIM" | bc)
      else
        echo "duration ${durs[$i]}" >> "$list"
        sum=$(echo "$sum + ${durs[$i]}" | bc)
      fi
    done

    st=${starts[$si]}
    printf "  세션%d: %d개 파일 / 이음새 %d곳 / 시작 %02d:%02d:%02d / 기대 %s초\n" \
      "$s" "$((ei-si))" "$((ei-si-1))" "$((st/3600))" "$(((st%3600)/60))" "$((st%60))" "$sum"

    if [ -f "$out" ]; then echo "    이미 존재 → 스킵"; else
      ffmpeg -hide_banner -loglevel error -nostdin -y -f concat -safe 0 -i "$list" -c copy "$out"
      fps=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of default=nw=1:nk=1 "${files[$si]}" | cut -d/ -f1)
      vd=$(ffprobe -v error -select_streams v:0 -show_entries stream=duration -of default=nw=1:nk=1 "$out")
      nb=$(ffprobe -v error -select_streams v:0 -show_entries stream=nb_frames -of default=nw=1:nk=1 "$out")
      gaps=$(ffprobe -v error -select_streams v:0 -show_entries packet=pts_time -of csv=p=0 "$out" 2>/dev/null \
             | tr -d ',' | awk 'NF' | sort -n \
             | awk -v fps="$fps" 'NR>1{if($1-p > 1.02/fps) c++} {p=$1} END{print c+0}')
      printf "    결과 %.3f초 / %s프레임 / PTS갭 %s곳" "$vd" "$nb" "$gaps"
      awk -v a="$sum" -v b="$vd" -v g="$gaps" 'BEGIN{d=a-b; if(d<0)d=-d;
        if(d>0.002 || g>0) printf "  ⚠️ 확인필요 (길이차 %.4f초)\n", d; else print "  ✅"}'
    fi
    si=$ei; s=$((s+1))
  done
done
