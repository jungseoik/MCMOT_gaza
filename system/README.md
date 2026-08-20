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
  `INGEST_BACKEND`(기본 **ffmpeg** — 아래 '인제스트 백엔드' 참조) ·
  `SESSION_RECORD`(기본 **on** — 세션 녹화. `0`이면 끔=기존과 동일·롤백, 아래 '세션 녹화·리플레이' 참조)
- 설정은 `data/sites/<site_id>/`(site.json·cameras/*.json·map.png)에 영속화 — 재시작 시 자동 복원
- **디폴트 세팅(seed)**: `data/seed/default/`가 git에 커밋돼 있어 **클론 후 첫 기동 시
  자동 복사**됨(3개 층 17F/19F/지상1층 맵·카메라·매핑·valid_roi·경로/구역/출입구 전부 포함
  — 지상1층은 실건물 CAD 척도만 있고 카메라 미배정·공간요소 비어 있음) —
  바로 운영 뷰 확인 가능. 카메라는 **`rtsp://127.0.0.1:8554/{1,2,3}_v1`**(같은 호스트의
  mediamtx, pm2 `1_v1`~`3_v1` 송출)을 바라본다 → **같은 서버에서 `tools/rtsp/setup_rtsp_streams.sh`로
  송출을 띄우면 추가 설정 없이 라이브가 붙는다**(RTSP 재현: [`docs/RTSP-송출서버-구성.md`](../docs/RTSP-송출서버-구성.md)).
  송출 서버가 **다른 호스트**면 UI ② 카메라 등록·매핑에서 주소의 IP만 그 호스트로 수정.
  송출이 죽어 있으면 재접속 대기만 하며 무해.
  현재 라이브 세팅을 seed로 갱신: `bash tools/seed_snapshot.sh` (sessions 제외)
- 평가 세션: 운영 뷰 🔔 경보 시작(맵 클릭) → 4대 지표 카드 → 종료 시 결과 산출,
  `sessions/<id>.json` 영속화 + `GET /api/sessions` 이력 (계약 v1.3)
- **세션 녹화·리플레이 (계약 v1.10)**: 세션 중 입력 트랙을 `sessions/<id>.db`(SQLite)에
  자동 녹화(도면·카메라 스냅샷 포함) → **④ 리플레이 탭**에서 2D 재생(시크·배속) +
  임계값만 바꿔 4대 지표 재계산(`POST /api/session/{id}/replay`). 도면 그대로,
  값만 변경 = 역방향 재파라미터화. 설계·DB 근거: [ADR 05](../docs/architecture/05-세션-녹화-리플레이-지표재계산-설계.md)
- **기존 webui PoC(webui/server.py)와 같은 프로세스 동시 구동 금지** (전역 GeneralSettings 충돌, CONTRACT v1.1 §5). 별도 포트로 각각 실행 — 기존 UI 좌측 레일의 맵 아이콘이 :8900을 연다.
- **로그인 게이트(간이·프론트 전용)**: 진입 시 PIA×삼성화재 브랜딩 로그인 화면(`webui/static/main/login_gate.js`). **mock**이라 접속코드는 아무거나(빈 값 포함) 통과(`ACCEPT_ANY`), 매 접속마다 표시(`REMEMBER=false`). 로고·배경은 `webui/static/`(samsung-fire-*.png·login-bg.jpg). 끄기: index.html에서 `login_gate.js` `<script>` 한 줄 제거. **실제 인증 아님** — 접근통제 필요 시 서버측 세션으로 교체.

## 인제스트 백엔드 (INGEST_BACKEND)

| 값 | 경로 | 한계 실측 | 비고 |
|----|------|-----------|------|
| `ffmpeg` (기본) | 카메라별 ffmpeg-NVDEC → FrameQueue → 호스트 직렬 TRT | **4ch@5fps** (총 ~21fps 포화) | 기존 경로 — 미지정 시 100% 기존 동작 |
| `deepstream` | GPU별 DS 워커 컨테이너(zero-copy 배치 추론·트래킹) → ZMQ 브리지 | **16ch@5fps/GPU** (총 78.7fps, 20ch부터 5fps 붕괴 · ~54ch에서 1fps) | 선행조건·워커 실행법: [ingest_ds/README.md](ingest_ds/README.md) |

### 추론 프로파일 (검출기·ReID 교체)

검출기·ReID·트래커 조합은 레포 루트 [`model_zoo.py`](../model_zoo.py)에 **프로파일**로
선언돼 있고, 세 경로(단일영상 `src/inference_gpu.py` · ffmpeg `tracking/analyzer.py` ·
DS `ingest_ds/worker.py`)가 프로파일 id 하나만 받는다.

| id | 검출 | ReID | 비고 |
|----|------|------|------|
| `yolox_fastreid` | YOLOX-X(MOT20) 896×1600 | FastReID SBS-S50 2048d | **기본** — 미설정 시 기존 동작 |
| `yolo26_clipreid` | YOLO26-L v6.3 640×640 | CLIP-ReID ViT-B/16 768d | det_thresh 0.6 |

- 선택: **UI `① 맵 설정 → [추론 모델]`** (→ `data/infer_profile.json`) · 배포 기본값은 `INFER_PROFILE` 환경변수
- 전환 시 추론 계층만 재기동. 평가 세션 진행 중이면 409로 거부
- 엔진 빌드: `bash tools/build_profile_engines.sh [--ds]` (원천 ONNX는 `tools/fetch_assets.sh --onnx`)
- 설계 [ADR 07](../docs/architecture/07-추론-프로파일-교체구조.md) · 실측 [보고서](../docs/reports/2026-08-20_신규-추론스택-YOLO26-CLIPReID-도입-실측.md)

```bash
INGEST_BACKEND=deepstream GPU_DEVICES=1 pm2 restart macs-system --update-env
# 롤백: INGEST_BACKEND 제거(기본 ffmpeg)로 재기동. git 레벨 롤백·제약(스냅샷 폴백,
# hot add/remove 시 워커 재시작 ~50s 등)은 ADR 04 참조.
```

근거·제약·롤백 절차: [docs/architecture/04-DeepStream-zero-copy-인제스트-전환.md](../docs/architecture/04-DeepStream-zero-copy-인제스트-전환.md)

## 카메라 일괄 등록 (벌크)

현장 수십 채널을 웹 UI에서 **한 대씩** 추가하면 안 된다. deepstream 백엔드는
카메라 추가 시 해당 GPU 슬롯의 워커 컨테이너를 재시작하므로, **추가할 때마다
그 슬롯의 기존 채널이 전부 끊긴다.**

실측 (a6000 / GPU1 전유 / `analyze_fps=5.0` / 채널 4→12에서 일정):

| 방식 | 채널당 | 기존 채널 데이터 공백 | 9채널 | 40채널 |
|------|--------|----------------------|-------|--------|
| `POST /api/cameras` 순차 | API 6.1s · 복귀 **14.3s** | 매번 **~8s** | 129s | **~9.5분** + 40회 단절 |
| `POST /api/cameras/bulk` | — | **~8.7s 1회** | **15.4s** | 채널 수에 거의 무관 |

**UI**: ② 카메라 등록·매핑 → 좌측 **"⿻ 여러 대 한 번에 등록…"** → 모달.
① 주소 붙여넣기(한 줄에 한 대 `이름,rtsp주소` 또는 `rtsp주소`만, `#`·빈 줄 무시)
→ **목록으로 변환** → ② 표에서 이름 인라인 수정·행 제거, **연결 테스트**(등록 전
`POST /api/cameras/probe`, 동시 4개)로 붙는 주소·해상도 확인 → 매핑 층 선택 →
**비활성으로 등록** 체크 시 워커에 올리지 않고 설정만 저장 → 등록.

매핑을 마친 뒤에는 카메라 목록 아래 **"▶ 매핑 완료 N대 전부 활성화"** 버튼으로
한 번에 켠다(`PUT /api/cameras/bulk` → `DsIngestManager.update_cameras()`,
워커 재시작 1회). 목록의 활성 체크박스를 하나씩 켜면 **켤 때마다** 재시작된다 —
비활성→활성은 코드상 신규 추가와 같은 경로(`launcher.py:441`)이기 때문이다.

현장 순서:

| 단계 | 동작 | 워커 재시작 | GPU 부하 |
|------|------|------------|---------|
| ① | 모달에서 벌크 등록 (**비활성으로 등록** 체크) | 0회 | 0 |
| ② | 연결 테스트로 안 붙는 주소 걸러내기 | 0회 | 0 |
| ③ | 카메라별 매핑(대응점) 지정 | 0회 | 0 |
| ④ | **매핑 완료 N대 전부 활성화** | **1회** | 정상 가동 |

> **비활성 등록을 쓰는 이유**: mapping 없는 카메라도 디코드·추론·트래킹 자원은
> 그대로 쓰고 결과만 버려진다(`metrics/engine.py:190`). 매핑 전 40채널을 활성으로
> 두면 요구 fps가 GPU 천장(총 ~75fps)을 넘겨 **이미 매핑된 채널의 fps까지 떨어진다.**
> 비활성이면 슬롯 배정 자체를 안 하므로(`launcher.py:380`) 부하가 0이다.
> 매핑 추가(`PUT /api/cameras/{id}/mapping`)는 `reload_engine()`만 하고 워커를
> 재시작하지 않으므로, 운영 중에도 안전하게 붙일 수 있다.

```bash
# CSV → 일괄 등록 (서버 실행 중 — 재기동 불필요)
NVR_USER=pia NVR_PASS='...' python tools/bulk_register_cams.py cams.csv
python tools/bulk_register_cams.py cams.csv --dry-run     # 파싱 결과만 확인
python tools/bulk_register_cams.py cams.csv --offline     # 서버 미기동 시 JSON만 생성
```

CSV는 `rtsp` 컬럼을 직접 주거나, `nvr,port,track,stream`으로 NVR URL을 조립한다
(`rtsp://<user>:<pass>@<nvr>:<port>/trackID=<track>&streamID=<stream>`, stream 기본 2 =
서브스트림). 계정은 CSV가 아니라 `NVR_USER`/`NVR_PASS` 환경변수로 주입한다.

- `POST /api/cameras/bulk` — body `{"cameras": [{rtsp, name?, analyze_fps?,
  floor_id?, min_conf?, enabled?}, ...]}`. **전건 검증 후 반영**(하나라도 유효하지
  않으면 아무것도 등록되지 않음) → `DsIngestManager.add_cameras()`가 슬롯당 재시작 1회.
  단일 등록(`POST /api/cameras`)은 그대로 동작한다.
- GPU 배정은 자동 — 누적 `analyze_fps`가 가장 작은 슬롯으로 간다(단일·벌크 동일).
  ⚠️ 배정 기준이 fps뿐이라 **해상도가 섞이면 실부하와 어긋난다**(해상도 통일 권장).
- **mapping(평면도 대응점)은 자동 생성 불가** — 등록 후 UI에서 카메라별로 지정해야
  맵 투영·지표에 포함된다. 미지정 카메라는 처리에서 제외.
- 평가 세션 진행 중에는 카메라 추가/삭제를 하지 않는다(8초 공백 = 트랙 ID 유실 =
  지표 오염).

## 모듈 (트랙별 소유 — CONTRACT §6)

| 모듈 | 내용 | 트랙 |
|------|------|------|
| `config/` | pydantic 스키마 + JSON 영속화(SiteStore) | B |
| `ingest/` | ffmpeg-NVDEC 카메라 워커·FrameQueue(oldest-drop)·재접속 워치독 | A |
| `ingest_ds/` | DeepStream zero-copy 워커·멀티 GPU 런처·ZMQ 브리지 (`INGEST_BACKEND=deepstream`) | A |
| `tracking/` | 공유 TRT 검출·ReID + 카메라별 BoostTrack + 분석 스레드 | A |
| `spatial/` | 호모그래피 맵 투영·polygon/통과선/polyline 기하 | B |
| `metrics/` | MetricsEngine — 속도·정렬도·구역 밀도·병목·출입구 카운트 → MapState. `recorder.py`(세션 SQLite 녹화)·`replay.py`(헤드리스 재생·재계산, v1.10) | B |
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
