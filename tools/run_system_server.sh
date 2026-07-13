#!/bin/bash
# MACS 멀티카메라 시스템 서버 (pm2 등록용) — system/README.md 참조
cd /home/pia/seoik/MCMOT_gaza
export SITE_ID="${SITE_ID:-default}"
exec conda run --no-capture-output -n boosttrack \
  uvicorn system.api.server:app --host 0.0.0.0 --port 8900
