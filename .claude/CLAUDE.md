# CLAUDE.md

이 저장소(MACS-EVAC / BoostTrack++ 기반)에서 작업할 때 따라야 할 안내.

## 🧭 북극성 요구사항 (가장 먼저 볼 것)

**"이 기능이 왜 필요한가 / 프로젝트가 궁극적으로 무엇을 추출해야 하는가"가
불분명할 때는 아래 요구사항 문서를 기준으로 판단한다.**

- ⭐ **[docs/requirements/삼성화재-피난훈련-정량평가-4대지표-요구사항.md](../docs/requirements/삼성화재-피난훈련-정량평가-4대지표-요구사항.md)**
  — **프로젝트 최상위 목표.** 4대 정량지표(**IDR·EPFI·CBS·SEI**)의 정의·입력·수식·출력.
  아래 5대 카테고리를 기반(base)으로 그 위에서 계산된다. **현재의 North Star.**
- 📌 **[docs/requirements/CCTV-영상분석-엔진-필수추출정보.md](../docs/requirements/CCTV-영상분석-엔진-필수추출정보.md)**
  — 4대 지표의 **기반**이 되는 5대 필수 추출 정보(객체 추적 / 평면도 좌표 / 이동속도·방향 /
  구역 밀도 / 시간 이벤트). 현재 단일채널 기준 대부분 충족.
- 인덱스: [docs/requirements/README.md](../docs/requirements/README.md)

## 프로젝트 개요

BoostTrack++ 다중 객체 추적(MOT)을 활용해 CCTV 영상에서 재실자를 검출·추적하고,
평면도 좌표 변환·속도·밀도·피난 이벤트를 산출하는 피난 분석 엔진 + 웹 UI.

- **추론 엔진**: `src/` — `inference.py`(PyTorch), `inference_gpu.py`/`inference_trt.py`(TRT 최적화), `build_trt.py`. **검출기 투트랙**(YOLOX ↔ RF-DETR): `BoostTrackGPUInference(detector=...)`·CLI `--detector`, RF-DETR은 `src/rfdetr_trt.py`(라이브러리 불필요)+`tools/setup_rfdetr.sh`. 근거·사용법 `docs/reports/RF-DETR-TRT-변환-사용법.md`
- **추론 프로파일(모델 교체)**: 검출기·ReID·트래커 조합을 레포 루트 **`model_zoo.py`** 한 곳에서 갈아끼운다 — `yolox_fastreid`(기본, 기존 동작) ↔ `yolo26_clipreid`(YOLO26-L v6.3 + CLIP-ReID). 세 호출 지점(단일영상·ffmpeg 백엔드·DS 컨테이너)이 프로파일 id 하나만 받는다. **:8900 [① 설정 → 추론 모델]에서 전환**(세션 중 불가). 엔진 빌드 `tools/build_profile_engines.sh [--ds]`, ONNX는 `tools/fetch_assets.sh --onnx`. 설계 `docs/architecture/07-추론-프로파일-교체구조.md` · 실측 `docs/reports/2026-08-20_신규-추론스택-YOLO26-CLIPReID-도입-실측.md`
- **단일채널 웹 UI**: `webui/` — `server.py`, 속도/밀도/카운팅/뎁스 모듈, RTSP 라이브 (포트 8000)
- **멀티카메라 시스템**: `system/` — 다채널 RTSP·TRT·4대 지표 세션·2D 맵 UI + **세션 리플레이·지표 재계산(v1.10)** · **건물 훈련(전 층 공유 세션·4대지표 롤업·전 층 리플레이, v1.11 — UI 표기 "건물 훈련", 내부 코드·API는 `drill`)** · **리허설 패키지(ADR 09 — `media/vsource/<site>/<set>/rehearsal.json` = 영상·시나리오·카메라·매핑 정본, 사이트 층 빙의, RTSP 없는 파일 모드 잠금 동기 추론; 영상은 HF `PIA-SPACE/C-lab`, `tools/fetch_assets.sh --rehearsal`)** · 간이 로그인 게이트 (포트 8900). `system/README.md` 참조
- **트래커**: `tracker/`, `boostracker/`, 외부 의존 `external/`

## 문서 맵 (docs/)

| 경로 | 내용 |
|------|------|
| `docs/requirements/` | **북극성 요구사항** (위 참조) |
| `docs/architecture/` | 기술스택 결정 기록(ADR) |
| `docs/wbs/` | 기능별 난이도 매트릭스 · 4개월 WBS · 시수 산정 + **WBS 변경이력**(`WBS-변경이력.md`) · 예전 버전 아카이브 |
| `docs/weekly/` | **주간 미팅 자료(주차별 폴더)** — 진척 보고서·시각화 보고서·최신 WBS(`vN.xlsx`) |
| `docs/webui-dev/` | 웹 UI 개발 문서(아키텍처·스트리밍·속도·캘리브·맵·카운팅 등) |
| `docs/reports/` | YOLO26·해상도·다채널 비교 실측 보고서 + 요구사항 점검 + 벤치 스크립트 |
| `docs/guide/` | **사용 가이드 허브(3종)** — 단일영상-분석-MVP(:8000) · 멀티카메라-시스템 4대지표(:8900) · 도면-편집기(:8910). 각 하위폴더 README + img (스크린샷 기반) |
| `docs/optimization-report.md` | 추론 최적화 보고서 |
| `docs/재현-새-GPU서버에서-현재상태-그대로.md` | **새 서버/노트북에서 현재 운영상태 그대로 재현** — clone + HF 토큰만으로 12채널·17F/16F/10F 도면·리허설 패키지·편집기까지. `tools/fetch_assets.sh [--rehearsal]` + `tools/seed_version.py restore v8 --apply` |
| `docs/설치-맨서버-부트스트랩.md` | **순정 우분투(드라이버만) 0단계 시스템 준비** — conda·docker·nvidia-container-toolkit·ffmpeg·node/pm2·CAD 복붙 체크리스트 + 기능별 필수의존 표 + 재현 판정. 대용량은 `tools/fetch_assets.sh`(HF `backseollgi/MCMOT`) |
| `docs/이관가이드-다른-GPU-서버로.md` | **다른 GPU 서버 이관 체크리스트** — 현 기준은 전부 Blackwell(sm_120). 다른 아키텍처(RTX 5000 Ada 등)면 TRT 엔진 재빌드·한계 재측정·`GPU_DEVICES` 실인덱스 확인 필수 |
| `docs/현장-NVR-RTSP-수집-대응계획.md` | **PoC 현장(CJ제일제당) NVR 수집 계획** — H.265/VBR/RTSP 세션 개념 정리 + 수집 아키텍처 3안 비교(권장: 로컬 mediamtx 허브) + 현장 진단 체크리스트·업체 요청서 |
| `docs/RTSP-송출서버-구성.md` | **RTSP 테스트 송출** — WebRTC 호환 인코딩·mediamtx·pm2 절차 + HF `backseollgi/MCMOT`(model, `videos/`, 비공개→`HF_TOKEN` 필요)에서 받아 재현하는 `tools/rtsp/setup_rtsp_streams.sh` |
| `media/vsource/` | **리허설 패키지** — `<site>/<set>/rehearsal.json`(git) + 영상(HF). 준비 CLI `tools/rehearsal_prep.py`, 오프라인 진단 `tools/rehearsal_viz.py`. 설계 `docs/architecture/09-리허설-패키지-구조.md` |
| `data/seed_versions/` | **디폴트 세팅(seed) 버전 보관** — [Reset] 이 복원하는 상태를 이름 붙여 저장·복원. `tools/seed_version.py` (save/list/show/restore) |
| `system/README.md` | 멀티카메라 2D맵 시스템 실행·환경변수·pm2·모듈별 소유 정보 |

## 작업 규칙

- 문서는 **한국어**로 작성하고, 각 하위 폴더에는 인덱스 `README.md`를 둔다(기존 컨벤션).
- 새 요구사항/스펙이 추가되면 `docs/requirements/`에 넣고 이 CLAUDE.md의 북극성 링크를 갱신한다.
- 환경: conda `boosttrack` (Python 3.12). 설치는 `requirements.txt` + `install_yolox.sh` 참조.
- **주간 WBS 점검**: 매주 레포 기준으로 WBS 진척을 점검하고 기록한다 → **`/wbs-review` 스킬**.
  최신 WBS는 그 주차 폴더(`docs/weekly/YYYY-MM-N주차/C-lab_PoC_WBS_vN.xlsx`)에 두고(컨벤션 #1),
  수정 시 `vN→vN+1` 버전업 + 변경이력(`docs/wbs/WBS-변경이력.md`) 기록 + 진척 보고서
  (`docs/weekly/<주차>/WBS-진척점검.md`) 작성. (xlsx는 바이너리라 텍스트 기록이 필수)
