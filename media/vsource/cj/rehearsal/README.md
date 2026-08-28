# cj/rehearsal — CJ제일제당센터 리허설 영상 (시나리오별)

CJ제일제당센터 **10층**에서 촬영한 (전 시나리오 공통 — 시나리오는 회차 구분일 뿐 층 동일) **리허설 영상 14개 시나리오**. 시나리오마다 4~5개 카메라
동시 촬영본. 패키지 규격·흐름은 [ADR 09](../../../../docs/architecture/09-리허설-패키지-구조.md), 매니페스트는 `rehearsal.json`. 그리드 미리보기(`grid_scenarioN.mp4`, 1440×540 몽타주)는 **송출 금지** — `grid_preview/` 에 분리 보관.

- 출처: HuggingFace dataset `PIA-SPACE/C-lab` → `03_scenarios.7z.001` (3.48GB, 2026-08-27 수령)
- 원본(`01_original`)·concat(`02_concat`)·sync(`04_sync`)는 같은 레포에 있으나 미수령
- 전부 1920×1080 · 30fps · **h264 baseline 재인코딩본**(2026-08-27, `rehearsal_prep.py --encode`) · yuv420p, 길이 23~33초
  — HF 원본은 High 프로파일이라 앞머리(baseline) concat 에서 스트림이 깨짐(실측). 원본은 `_orig_high_profile/` 에 보관
- 시나리오 내 채널 간 길이 차 ≤30ms → 동기 송출 요건(같은 순간 시작) 충족

| 시나리오 | 카메라 | 길이 |
|---|---|---|
| scenario_01 | cam8·9·10·11·14 | 32s |
| scenario_02 | cam8·9·10·11·14 | 27s |
| scenario_03 | cam3·8·9·10·11 | 32s |
| scenario_04 | cam3·8·9·10·11 | 29s |
| scenario_05 | cam1·4·5·6·7 | 33s |
| scenario_06 | cam1·4·5·6·7 | 27s |
| scenario_07 | cam4·5·6·7·12 | 29s |
| scenario_08 | cam4·5·6·7·12 | 30s |
| scenario_09 | cam5·6·7·13 | 26s |
| scenario_10 | cam5·6·7·13 | 23s |
| scenario_11 | cam2·5·6·7 | 28s |
| scenario_12 | cam2·5·6·7 | 28s |
| scenario_13 | cam2·8·9·10 | 31s |
| scenario_14 | cam8·9·10·13 | 25s |

vsource 시나리오(`data/scenarios/<id>.json`) 등록 시 `file` 은
`media/vsource/cj/rehearsal/scenario_NN/camX.mp4` 로 가리킨다. 층: 사이트 층 `floor10`(10F)에 붙는다 — 도면은 ① 맵설정에 16F(=17F_v2.dwg) 것을 그대로 올림(구조 동일). 구역·출구·CAD 는 ①에서 세팅.
RTSP 경로(`path`)는 `cj_camN` 으로 통일 —
시나리오가 바뀌어도 같은 cam 번호는 같은 경로라 :8900 카메라는 한 번만 등록한다.
