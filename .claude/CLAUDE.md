# CLAUDE.md

이 저장소(MACS-EVAC / BoostTrack++ 기반)에서 작업할 때 따라야 할 안내.

## 🧭 북극성 요구사항 (가장 먼저 볼 것)

**"이 기능이 왜 필요한가 / 프로젝트가 궁극적으로 무엇을 추출해야 하는가"가
불분명할 때는 아래 요구사항 문서를 기준으로 판단한다.**

- ⭐ **[docs/requirements/삼성화재-피난훈련-정량평가-4대지표-요구사항.md](docs/requirements/삼성화재-피난훈련-정량평가-4대지표-요구사항.md)**
  — **프로젝트 최상위 목표.** 4대 정량지표(**IDR·EPFI·CBS·SEI**)의 정의·입력·수식·출력.
  아래 5대 카테고리를 기반(base)으로 그 위에서 계산된다. **현재의 North Star.**
- 📌 **[docs/requirements/CCTV-영상분석-엔진-필수추출정보.md](docs/requirements/CCTV-영상분석-엔진-필수추출정보.md)**
  — 4대 지표의 **기반**이 되는 5대 필수 추출 정보(객체 추적 / 평면도 좌표 / 이동속도·방향 /
  구역 밀도 / 시간 이벤트). 현재 단일채널 기준 대부분 충족.
- 인덱스: [docs/requirements/README.md](docs/requirements/README.md)

## 프로젝트 개요

BoostTrack++ 다중 객체 추적(MOT)을 활용해 CCTV 영상에서 재실자를 검출·추적하고,
평면도 좌표 변환·속도·밀도·피난 이벤트를 산출하는 피난 분석 엔진 + 웹 UI.

- **추론 엔진**: `src/` — `inference.py`(PyTorch), `inference_gpu.py`/`inference_trt.py`(TRT 최적화), `build_trt.py`
- **단일채널 웹 UI**: `webui/` — `server.py`, 속도/밀도/카운팅/뎁스 모듈, RTSP 라이브 (포트 8000)
- **멀티카메라 시스템**: `system/` — 다채널 RTSP·TRT·4대 지표 세션·2D 맵 UI (포트 8900). `system/README.md` 참조
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
| `system/README.md` | 멀티카메라 2D맵 시스템 실행·환경변수·pm2·모듈별 소유 정보 |

## 작업 규칙

- 문서는 **한국어**로 작성하고, 각 하위 폴더에는 인덱스 `README.md`를 둔다(기존 컨벤션).
- 새 요구사항/스펙이 추가되면 `docs/requirements/`에 넣고 이 CLAUDE.md의 북극성 링크를 갱신한다.
- 환경: conda `boosttrack` (Python 3.12). 설치는 `requirements.txt` + `install_yolox.sh` 참조.
- **주간 WBS 점검**: 매주 레포 기준으로 WBS 진척을 점검하고 기록한다 → **`/wbs-review` 스킬**.
  최신 WBS는 그 주차 폴더(`docs/weekly/YYYY-MM-N주차/C-lab_PoC_WBS_vN.xlsx`)에 두고(컨벤션 #1),
  수정 시 `vN→vN+1` 버전업 + 변경이력(`docs/wbs/WBS-변경이력.md`) 기록 + 진척 보고서
  (`docs/weekly/<주차>/WBS-진척점검.md`) 작성. (xlsx는 바이너리라 텍스트 기록이 필수)
