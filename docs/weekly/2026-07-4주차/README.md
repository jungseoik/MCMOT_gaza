# 2026년 7월 4주차

**이번 주 핵심**: **DeepStream zero-copy 다채널 인제스트 전환** — 기존 ffmpeg 경로(4ch@5fps 한계)를
zero-copy 디코드 + 배치 추론으로 교체해 **1GPU 16ch@5fps(총 78.7fps, 약 4배)** 실측. 출력 유사도
검증(트랙 매칭 98.95%)·운영 webui E2E 통합검증·b32 한계 재스윕(기각)까지 완료(P0~P11).

| 파일 | 내용 |
|------|------|
| [DeepStream 다채널 인제스트 전환 및 16채널 실측 보고](2026-07-20_DeepStream-다채널-인제스트-전환-및-16채널-실측-보고.md) | 전환 전 과정(P0~P11)·처리량 실측(4ch→16ch·79fps)·출력 유사도·UI 개선(min_conf/valid_roi)·TRT 레이스 버그수정·대분류별 완료 수준·다음 계획 |
| [WBS 진척 점검 (v8)](WBS-진척점검.md) | 90항목 — 완료 20/진행중 11/예정 59. v7→v8 상태 변경 6건(4.7.1·4.7.2·4.2.3 완료 / 4.7.3·4.7.4·6.3.2 진행중), 항목별 근거표·리스크·다음주 액션 |
| `C-lab_PoC_WBS_v8.xlsx` | 이번 주 WBS(최신본). 변경이력: [`docs/wbs/WBS-변경이력.md`](../../wbs/WBS-변경이력.md) |

> 관련 문서: [DeepStream zero-copy 인제스트 전환 ADR](../../architecture/04-DeepStream-zero-copy-인제스트-전환.md) ·
> [한계 처리량 실측](../../reports/DeepStream-한계처리량-실측.md) ·
> [전환 유사도 검증](../../reports/DeepStream-전환-유사도-검증.md) ·
> [운영 webui 통합검증](../../reports/DeepStream-webui-통합검증.md)
>
> 점검 절차: `/wbs-review` 스킬
