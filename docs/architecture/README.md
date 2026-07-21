# Architecture · 기술 결정 (ADR)

요구사항을 충족하도록 프로젝트를 확장할 때의 **기술 스택 선정 근거**를 기록한다.
"왜 이걸 골랐나 / 대안은 무엇이었나 / 언제 바뀌나"를 추적하기 위한 문서.

## 문서

1. **[01-기술스택-결정.md](01-기술스택-결정.md)**
   — RTSP 수신·추론서빙·트래커·메시지버스·DB·대시보드·맵 시각화 7개 레이어별
   후보 비교(속도/메모리/운영부담/요구적합)와 결정·사유, 단계별(소규모↔50~60ch) 권고.
2. **[02-멀티카메라-시스템-전환-설계.md](02-멀티카메라-시스템-전환-설계.md)**
   — PoC WebUI → **공통 2D 맵 기반 16채널 시스템** 전환 설계(2026-07-13 인터뷰 확정).
   ffmpeg-NVDEC 인제스트·카메라별 트래커·맵 좌표 지표·JSON 영속화·M0~M7 구현 계획표.
   카메라 영상 오버레이 미표출(맵 데이터만 송출) 결정 포함.
3. **[03-M0-환경검증-디코딩스택-실측.md](03-M0-환경검증-디코딩스택-실측.md)**
   — M0 실측: GStreamer/DeepStream 미설치, **ffmpeg+NVDEC 16ch 디코드 dec 3%·CPU 2.2코어**
   → ingest 디코딩 스택 = ffmpeg 서브프로세스(NVDEC) 확정.
4. **[04-DeepStream-zero-copy-인제스트-전환.md](04-DeepStream-zero-copy-인제스트-전환.md)**
   — ffmpeg 경로 4ch@5fps 한계 실측 → **DeepStream 워커(16ch@5fps/GPU, 총 79fps)** 채택.
   `INGEST_BACKEND` 스위치(기본 ffmpeg)로 병행, 출력 유사도 검증·제약·롤백 절차 포함.

## 한 줄 요약

> DB는 **PostgreSQL(PostGIS+TimescaleDB+pgvector) 단일화**, 실시간은 **Redis로 시작**,
> **DeepStream은 채택(ADR 04)**, **Kafka·Triton·ClickHouse는 50~60ch 초과 확장 시 옵션**.
> 최종 목표 50~60채널(2026-07-4주차 하향, 구 150ch)은 현 경량 스택 + GPU 3~4장으로 도달 가능.
> 모든 선택은 "채널 스케일"과 "요구 2·4·5가 전부 기하 연산"이라는 두 축으로 갈린다.

## 관련 문서
- 요구사항(North Star): [../requirements/CCTV-영상분석-엔진-필수추출정보.md](../requirements/CCTV-영상분석-엔진-필수추출정보.md)
- 구현 갭 점검: [../reports/2026-06-12_요구사항-대비-webui-구현-점검-보고서.md](../reports/2026-06-12_요구사항-대비-webui-구현-점검-보고서.md)
