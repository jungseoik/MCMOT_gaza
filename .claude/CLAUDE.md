# CLAUDE.md

이 저장소(MACS-EVAC / BoostTrack++ 기반)에서 작업할 때 따라야 할 안내.

## 🧭 북극성 요구사항 (가장 먼저 볼 것)

**"이 기능이 왜 필요한가 / 프로젝트가 궁극적으로 무엇을 추출해야 하는가"가
불분명할 때는 아래 요구사항 문서를 기준으로 판단한다.**

- 📌 **[docs/requirements/CCTV-영상분석-엔진-필수추출정보.md](docs/requirements/CCTV-영상분석-엔진-필수추출정보.md)**
  — 분석 엔진의 5대 필수 추출 정보(객체 추적 / 평면도 좌표 / 이동속도·방향 /
  구역 밀도 / 시간 이벤트). **이 프로젝트의 North Star.**
- 인덱스: [docs/requirements/README.md](docs/requirements/README.md)

## 프로젝트 개요

BoostTrack++ 다중 객체 추적(MOT)을 활용해 CCTV 영상에서 재실자를 검출·추적하고,
평면도 좌표 변환·속도·밀도·피난 이벤트를 산출하는 피난 분석 엔진 + 웹 UI.

- **추론 엔진**: `src/` — `inference.py`(PyTorch), `inference_gpu.py`/`inference_trt.py`(TRT 최적화), `build_trt.py`
- **웹 UI**: `webui/` — `server.py`, 속도/밀도/카운팅/뎁스 모듈, RTSP 라이브
- **트래커**: `tracker/`, `boostracker/`, 외부 의존 `external/`

## 문서 맵 (docs/)

| 경로 | 내용 |
|------|------|
| `docs/requirements/` | **북극성 요구사항** (위 참조) |
| `docs/architecture/` | 기술스택 결정 기록(ADR) |
| `docs/wbs/` | 기능별 난이도 매트릭스 · 4개월 WBS · 시수 산정 (경영 보고용) |
| `docs/webui-dev/` | 웹 UI 개발 문서(아키텍처·스트리밍·속도·캘리브·맵·카운팅 등) |
| `docs/reports/` | YOLO26·해상도·다채널 비교 실측 보고서 + 요구사항 점검 + 벤치 스크립트 |
| `docs/guide/` | 웹 UI 사용 가이드(스크린샷 기반) |
| `docs/optimization-report.md` | 추론 최적화 보고서 |

## 작업 규칙

- 문서는 **한국어**로 작성하고, 각 하위 폴더에는 인덱스 `README.md`를 둔다(기존 컨벤션).
- 새 요구사항/스펙이 추가되면 `docs/requirements/`에 넣고 이 CLAUDE.md의 북극성 링크를 갱신한다.
- 환경: conda `boosttrack` (Python 3.12). 설치는 `requirements.txt` + `install_yolox.sh` 참조.
