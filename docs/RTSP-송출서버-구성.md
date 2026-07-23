# RTSP 송출 서버 구성 — MACS 테스트 영상 스트리밍

> MACS는 RTSP 입력으로 동작하므로, 테스트하려면 **RTSP 소스**가 있어야 한다.
> 이 문서는 테스트 영상을 **WebRTC 호환 H.264로 인코딩 → mediamtx → pm2 송출**하는
> 절차와, 이 프로젝트의 테스트 영상(HuggingFace 데이터셋)을 **한 방에 재현**하는 방법을 담는다.
>
> 새 서버 이관 시: **[이관 가이드](이관가이드-다른-GPU-서버로.md)** §RTSP 참조.

---

## 빠른 재현 (권장) — HF 데이터셋에서 받아 자동 송출

이 프로젝트 테스트 3채널 영상은 HuggingFace 데이터셋 **`backseollgi/mot_dataset`**의
`videos/`에 있다(이미 WebRTC 호환 인코딩 완료). 스크립트 하나로 다운로드+송출까지 재현:

```bash
# hf CLI 필요 (없으면: pip install -U huggingface_hub)
# 비공개 데이터셋이면 토큰 필요 — 환경변수로만(커밋 금지)
HF_TOKEN=hf_xxx bash tools/rtsp/setup_rtsp_streams.sh
```
→ `rtsp://<이서버IP>:8554/{1_v1,2_v1,3_v1}` 3채널 송출. MACS 등록 주소로 그대로 사용.

스크립트가 하는 일: HF에서 `videos/*.mp4` 다운로드(`~/rtsp-stream/`) → mediamtx(도커) 기동
→ 스트림별 pm2 송출 등록. 이미 있는 파일·컨테이너는 건너뛴다(멱등).

> **채널 추가/변경**: `tools/rtsp/setup_rtsp_streams.sh`의 `STREAMS=(...)` 배열 수정.
> 새 영상은 아래 "① 인코딩" 후 HF에 업로드(`hf upload backseollgi/mot_dataset new.mp4 videos/new.mp4 --repo-type dataset`).

---

## 수동 절차 (원리 이해 / 새 영상 준비)

### 조건 — WebRTC 호환 (MACS 모니터링·브라우저 표출 안정)
- **코덱**: H.264 (libx264) — 거의 모든 디바이스/브라우저/RTSP/WebRTC 지원
- **Baseline 프로파일**: B-frame 없음 — 모바일·WebRTC·구형 디바이스 호환
- **30프레임마다 I-frame 고정**: 장면 전환에도 중간 I-frame 추가 안 함
- **비트레이트**: 128kbps 수준
> 인코딩 없이 임의 mp4를 바로 RTSP로 쏘면 MACS에서 모니터링이 안 될 가능성이 높다.

### ① 인코딩 (디렉토리 내 *.mp4 일괄 → 원래 파일명으로 저장)
```bash
for file in *.mp4; do
  echo "🔄 처리 중: $file"
  tempfile="temp_$file"
  mv "$file" "$tempfile"
  ffmpeg -y -i "$tempfile" \
    -c:v libx264 -profile:v baseline -level 4.2 -pix_fmt yuv420p \
    -g 30 -keyint_min 30 -sc_threshold 0 \
    -x264-params "bframes=0:repeat-headers=1" \
    -preset veryfast -movflags +faststart \
    -c:a aac -b:a 128k \
    "$file"
  rm "$tempfile"
  echo "✅ 완료: $file"
done
```

### ② mediamtx 기동 (도커)
```bash
docker run -d --name mediamtx -p 8554:8554 -p 1935:1935 -p 8888:8888 bluenviron/mediamtx
```

### ③ pm2 설치 (없을 때만)
```bash
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt-get install -y nodejs
sudo npm i -g pm2 && pm2 -v
```

### ④ 송출 스크립트 작성
이미 WebRTC 호환으로 인코딩했으면 `-c:v copy`(재인코딩 없음):
```bash
# stream-카테고리_이름_1.sh
ffmpeg -re -stream_loop -1 -i /경로/비디오이름.mp4 \
  -c:v copy -an \
  -f rtsp -rtsp_transport tcp \
  rtsp://localhost:8554/카테고리_이름_1
```
- `-i`: mp4 경로 · 맨 끝 경로(`/카테고리_이름_1`)가 스트림 이름(영상명과 같게 하면 알아보기 쉬움)

### ⑤ pm2 등록·확인
```bash
pm2 start stream-카테고리_이름_1.sh --name 카테고리_이름_1
pm2 list                                   # online 확인
ffplay rtsp://localhost:8554/카테고리_이름_1   # 영상 확인(선택)
```

### ⑥ MACS 등록
카메라 등록 주소: `rtsp://<송출서버IP>:8554/카테고리_이름_1`
(송출서버 IP: `hostname -I | awk '{print $1}'`)

---

## 컨벤션
- **스트림이름 == pm2 프로세스명 == RTSP 경로** (일관 유지 — 추적 쉬움)
- 송출 디렉토리 기본: `~/rtsp-stream/` (영상 + `stream-<이름>.sh`)
- 포트: RTSP 8554 / RTMP 1935 / HLS 8888 / WebRTC 8889

## 테스트 데이터셋 (HuggingFace)
- 레포: **`backseollgi/mot_dataset`** (dataset), 경로 `videos/*.mp4`
- 현재 3채널: `1_v1`(1280×720), `2_v1`·`3_v1`(1108×828) — 전부 H.264 Constrained Baseline, 24fps
- 업로드: `hf upload backseollgi/mot_dataset <로컬>.mp4 videos/<이름>.mp4 --repo-type dataset`
- 다운로드: `hf download backseollgi/mot_dataset videos/<이름>.mp4 --repo-type dataset --local-dir <dir>`

> ⚠️ **토큰은 절대 레포에 커밋하지 않는다.** `HF_TOKEN` 환경변수 또는 `hf auth login`으로만 사용.
