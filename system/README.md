# system/ — 멀티카메라 2D 맵 분석 시스템

> 설계: [docs/architecture/02-멀티카메라-시스템-전환-설계.md](../docs/architecture/02-멀티카메라-시스템-전환-설계.md) ·
> 계약: [CONTRACT.md](CONTRACT.md) (v1.9) · M0 실측: [docs/architecture/03](../docs/architecture/03-M0-환경검증-디코딩스택-실측.md)

## 사전 설치 (1회)

```bash
# pm2 — 프로세스 관리자. Node.js가 없으면 먼저 설치
node -v || curl -fsSL https://fnm.vercel.app/install | bash  # fnm으로 Node 설치
npm install -g pm2
```

## 실행

```bash
# 실서버 (RTSP → NVDEC → TRT 추적 → 맵 좌표 → SSE → 4대 지표 세션) + main 탭 UI
# 상시 기동(권장): pm2 등록 완료 — pm2 restart macs-system
pm2 start tools/run_system_server.sh --name macs-system
# 수동 실행:
conda run -n boosttrack uvicorn system.api.server:app --host 0.0.0.0 --port 8900
# → http://<host>:8900/  (맵 설정 → 카메라 등록·매핑 → 운영 뷰)

# mock 서버 (프론트 개발용 — 가짜 객체 시뮬레이션, GPU 불필요)
conda run -n boosttrack uvicorn system.api.mock_server:app --port 8901
```

- 환경변수: `SITE_ID`(기본 default) · `SITE_ROOT`(기본 data/sites) ·
  `GPU_DEVICES`(ffmpeg 모드 기본 0,1 — NVDEC 분산 / deepstream 모드 기본 1) ·
  `INGEST_BACKEND`(기본 **ffmpeg** — 아래 '인제스트 백엔드' 참조)
- 설정은 `data/sites/<site_id>/`(site.json·cameras/*.json·map.png)에 영속화 — 재시작 시 자동 복원
- **디폴트 세팅(seed)**: `data/seed/default/`가 git에 커밋돼 있어 **클론 후 첫 기동 시
  자동 복사**됨(2개 층 17F/19F 맵·카메라·매핑·valid_roi·경로/구역/출입구 전부 포함) —
  바로 운영 뷰 확인 가능. 카메라는 **`rtsp://127.0.0.1:8554/{1,2,3}_v1`**(같은 호스트의
  mediamtx, pm2 `1_v1`~`3_v1` 송출)을 바라본다 → **같은 서버에서 `tools/rtsp/setup_rtsp_streams.sh`로
  송출을 띄우면 추가 설정 없이 라이브가 붙는다**(RTSP 재현: [`docs/RTSP-송출서버-구성.md`](../docs/RTSP-송출서버-구성.md)).
  송출 서버가 **다른 호스트**면 UI ② 카메라 등록·매핑에서 주소의 IP만 그 호스트로 수정.
  송출이 죽어 있으면 재접속 대기만 하며 무해.
  현재 라이브 세팅을 seed로 갱신: `bash tools/seed_snapshot.sh` (sessions 제외)
- 평가 세션: 운영 뷰 🔔 경보 시작(맵 클릭) → 4대 지표 카드 → 종료 시 결과 산출,
  `sessions/<id>.json` 영속화 + `GET /api/sessions` 이력 (계약 v1.3)
- **기존 webui PoC(webui/server.py)와 같은 프로세스 동시 구동 금지** (전역 GeneralSettings 충돌, CONTRACT v1.1 §5). 별도 포트로 각각 실행 — 기존 UI 좌측 레일의 맵 아이콘이 :8900을 연다.

## 인제스트 백엔드 (INGEST_BACKEND)

| 값 | 경로 | 한계 실측 | 비고 |
|----|------|-----------|------|
| `ffmpeg` (기본) | 카메라별 ffmpeg-NVDEC → FrameQueue → 호스트 직렬 TRT | **4ch@5fps** (총 ~21fps 포화) | 기존 경로 — 미지정 시 100% 기존 동작 |
| `deepstream` | GPU별 DS 워커 컨테이너(zero-copy 배치 추론·트래킹) → ZMQ 브리지 | **16ch@5fps/GPU** (총 78.7fps, 20ch부터 5fps 붕괴 · ~54ch에서 1fps) | 선행조건·워커 실행법: [ingest_ds/README.md](ingest_ds/README.md) |

```bash
INGEST_BACKEND=deepstream GPU_DEVICES=1 pm2 restart macs-system --update-env
# 롤백: INGEST_BACKEND 제거(기본 ffmpeg)로 재기동. git 레벨 롤백·제약(스냅샷 폴백,
# hot add/remove 시 워커 재시작 ~50s 등)은 ADR 04 참조.
```

근거·제약·롤백 절차: [docs/architecture/04-DeepStream-zero-copy-인제스트-전환.md](../docs/architecture/04-DeepStream-zero-copy-인제스트-전환.md)

## 모듈 (트랙별 소유 — CONTRACT §6)

| 모듈 | 내용 | 트랙 |
|------|------|------|
| `config/` | pydantic 스키마 + JSON 영속화(SiteStore) | B |
| `ingest/` | ffmpeg-NVDEC 카메라 워커·FrameQueue(oldest-drop)·재접속 워치독 | A |
| `ingest_ds/` | DeepStream zero-copy 워커·멀티 GPU 런처·ZMQ 브리지 (`INGEST_BACKEND=deepstream`) | A |
| `tracking/` | 공유 TRT 검출·ReID + 카메라별 BoostTrack + 분석 스레드 | A |
| `spatial/` | 호모그래피 맵 투영·polygon/통과선/polyline 기하 | B |
| `metrics/` | MetricsEngine — 속도·정렬도·구역 밀도·병목·출입구 카운트 → MapState | B |
| `api/` | `server.py`(실서버) · `mock_server.py`(개발용) | 메인/C |
| `contracts.py` | FrameItem → TrackedObject → MapState 인터페이스 | 동결 |

테스트: `conda run -n boosttrack python -m pytest tests/system -q` (86개 —
spatial 17 · metrics 20 · session 24 · ds_launcher 11 · floors 14.
85 pass + 기존 이슈 1건 `test_graph_empty_straight_line_fallback` fail)

## 통합 검증 실측 (2026-07-13, M7 · 2026-07-19 DS 갱신)

| 항목 | ffmpeg 경로 (M7) | DeepStream 경로 (P1~P7) |
|------|------------------|------------------------|
| e2e 전 구간 | 2ch 실추적: RTSP→NVDEC→TRT→트래커→맵→SSE 동작, 5fps/ch·드랍 0 | 4ch 등록→운영뷰 SSE 동일 스키마, 5fps/ch·드랍 0 (:8902 검증) |
| 채널당 5fps 한계 | **4ch** (라이브 스윕 — 6ch부터 미달) | **16ch/GPU** (12ch는 드랍 0 여유 · 20ch부터 붕괴, 워커 분할·b32 엔진으로도 불변 — P9/P11) |
| 총 처리량 포화 | ~21fps | **78.7fps** (약 4배, GPU1 단독) · 1fps 도달 ~54ch |
| 평균 추론 | 56.7ms/frame (GPU 공유 조건) | 12.7ms/frame (배치 16 환산) |
| 출력 동등성 | (기준) | 검출 매칭 99.4%·트랙 98.95% — [유사도 검증](../docs/reports/DeepStream-전환-유사도-검증.md) |

- 상세: [DeepStream-한계처리량-실측](../docs/reports/DeepStream-한계처리량-실측.md) · ADR 04
- 개별 실측(ffmpeg 경로): 16ch ingest 드랍 0 · 재접속 14s 복구 · ID 공간 독립 · 단일영상 회귀 무손상
