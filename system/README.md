# system/ — 멀티카메라 2D 맵 분석 시스템

> 설계: [docs/architecture/02-멀티카메라-시스템-전환-설계.md](../docs/architecture/02-멀티카메라-시스템-전환-설계.md) ·
> 계약: [CONTRACT.md](CONTRACT.md) (v1.1) · M0 실측: [docs/architecture/03](../docs/architecture/03-M0-환경검증-디코딩스택-실측.md)

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

- 환경변수: `SITE_ID`(기본 default) · `SITE_ROOT`(기본 data/sites) · `GPU_DEVICES`(기본 0,1 — ffmpeg NVDEC 분산)
- 설정은 `data/sites/<site_id>/`(site.json·cameras/*.json·map.png)에 영속화 — 재시작 시 자동 복원
- 평가 세션: 운영 뷰 🔔 경보 시작(맵 클릭) → 4대 지표 카드 → 종료 시 결과 산출,
  `sessions/<id>.json` 영속화 + `GET /api/sessions` 이력 (계약 v1.3)
- **기존 webui PoC(webui/server.py)와 같은 프로세스 동시 구동 금지** (전역 GeneralSettings 충돌, CONTRACT v1.1 §5). 별도 포트로 각각 실행 — 기존 UI 좌측 레일의 맵 아이콘이 :8900을 연다.

## 모듈 (트랙별 소유 — CONTRACT §6)

| 모듈 | 내용 | 트랙 |
|------|------|------|
| `config/` | pydantic 스키마 + JSON 영속화(SiteStore) | B |
| `ingest/` | ffmpeg-NVDEC 카메라 워커·FrameQueue(oldest-drop)·재접속 워치독 | A |
| `tracking/` | 공유 TRT 검출·ReID + 카메라별 BoostTrack + 분석 스레드 | A |
| `spatial/` | 호모그래피 맵 투영·polygon/통과선/polyline 기하 | B |
| `metrics/` | MetricsEngine — 속도·정렬도·구역 밀도·병목·출입구 카운트 → MapState | B |
| `api/` | `server.py`(실서버) · `mock_server.py`(개발용) | 메인/C |
| `contracts.py` | FrameItem → TrackedObject → MapState 인터페이스 | 동결 |

테스트: `conda run -n boosttrack python -m pytest tests/system -q` (33개)

## 통합 검증 실측 (2026-07-13, M7)

- 2ch 실추적 e2e: RTSP→NVDEC→TRT→트래커→맵 투영→SSE 전 구간 동작. 수신 5fps/ch·드랍 0, 평균 추론 56.7ms/frame(타 워크로드와 GPU 공유 조건), 큐 지연 0.17s
- **처리량 한계(공유 GPU)**: 분석 ~18fps 지속 — 16ch×5fps(80fps)는 GPU 전유 또는 채널 fps 하향·검출 배치 필요(트랙 A 보고와 일치). oldest-drop이라 과부하에도 시스템은 안정(최신 프레임 우선)
- 개별 실측: 16ch ingest 드랍 0(트랙 A) · 재접속 14s 복구 · ID 공간 독립 · 단일영상 회귀 무손상
