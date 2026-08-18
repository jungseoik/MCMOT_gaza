#!/usr/bin/env bash
# field 인코딩본을 mediamtx로 RTSP 릴레이 송출 (재인코딩 없이 -c copy, 무한 루프).
# pm2: pm2 start tools/rtsp/field_relay.sh --name <name> -- <encoded.mp4 절대경로> <name>
set -euo pipefail
exec ffmpeg -re -stream_loop -1 -i "$1" -c:v copy -an \
  -f rtsp -rtsp_transport tcp "rtsp://localhost:8554/$2"
