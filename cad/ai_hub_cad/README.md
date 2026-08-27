# AI지원센터(메인센터) CAD → 층별 평면도 렌더

AI-Hub 공개 건축 CAD("메인센터 도면", `메인센터 도면/`)에서 **사람이 보기 편한 층별
평면도 PNG**(외곽벽·코어·화장실·계단·문 위주, 미터 격자·스케일바)를 뽑는 작업 기록.
전체 레포 공통이 아니라 **이 폴더(AI-Hub CAD) 전용** 메모다.

> 이 작업은 진행 중(WIP)이다. 3층은 정리 완료 수준, 1층은 1차 렌더까지.

## 빠른 사용법

```bash
PY=~/miniconda3/envs/boosttrack/bin/python
# 1) DWG→DXF 변환 (ODA, 이 머신엔 설치됨). cad-convert 스킬 참고.
$PY tools/cad_convert.py dwg2dxf --in "cad/ai_hub_cad/메인센터 도면/xref/xref-AI지원센터-PLAN.dwg" --out cad/ai_hub_cad/_converted/
# 2) 층별 렌더 (3층 예시). 출력 없으면 기본 _converted/plan_<n>F.png
$PY cad/ai_hub_cad/render_plan.py 3 cad/ai_hub_cad/_converted/plan_3F.png
$PY cad/ai_hub_cad/render_plan.py 1 cad/ai_hub_cad/_converted/plan_1F.png
```

## 핵심 발견 (왜 이렇게 해야 하는가)

1. **기본 평면도 `A301_310 평면도(변경).dwg` 는 껍데기다.**
   벽을 외부참조(xref)로 불러오는데 ODA 바인딩 시 xref 블록이 **비어서**(entities {})
   벽이 하나도 없다. → 개방형처럼 보였던 원인.
2. **진짜 벽·방은 `메인센터 도면/xref/xref-AI지원센터-PLAN.dwg` 에 있다.**
   변환본 `_converted/xref-AI지원센터-PLAN.dxf`(26MB). 모든 렌더는 이 파일 기준.
3. **형상 대부분이 중첩 블록(nested INSERT)** 안에 있다 → 재귀 전개(virtual_entities
   재귀) 해야 벽이 나온다. 한 겹만 풀면 절반이 사라진다.
4. **레이어명이 용도와 어긋난다**(드래프터 관행). 실측 결과는 아래 표.

## 레이어 지도 (xref-AI지원센터-PLAN.dxf, 실측)

| 레이어 | 실제 정체 | 렌더 |
|--------|-----------|------|
| `A_WINDOW` `A_WIN` `A-WIN` | 외곽선 + 방 칸막이 + **커튼월 창호 유닛**(가장자리 반복 심볼) | 유지(가장자리 짧은 심볼만 정리) |
| `A_ELEV 1~5` | 코어(화장실·엘리베이터·계단) + 가장자리 반복 심볼 | 유지 |
| `A_insulation` | 건식벽체/단열 + 가장자리 기둥마감 | 유지 |
| `A_FINISH` | 마감선(코어 상세·**1층 바닥타일**) | 유지(1층은 타일 과다 — 정리 검토) |
| `A_HATCH_WALL` `A_LINE` `A_STAIR` `A_STAIR HANDRAIL` `A_Door` `A_WINDOW_DOOR` | 벽·계단·문 | 유지 |
| `A_LIGHTING_FACADE/EXTERIOR/LANDSCAPE` | **가장자리 "불빛"** (사용자가 지적한 조명) | 제거 |
| `A_CEILING` `*_CEILING` | 천정 반사평면선(HIDDEN이라 점선처럼 보임). **방 경계가 아니라 천정 디자인** | 제거 |
| `BUTTON_SPRING_BL4/BL26` | 좌석 스프링 심볼(각 1.6만개) | 제거 |
| `A_FURNITURE*` | 가구·집기 | 유지(방 채움). `A_FURNITURE_점자블럭`=바닥 점자블록 **원**은 제거 |
| `A_COLUMN` + 블록명 `*COLUMN*`(`##COLUMN_R` 원형, `#COLUMN_S/D` 각형) | 기둥 | 제거(사용자 요청) |
| `A_REV_*` | **개정 클라우드**(둥근 사각·원 마크업), 신설벽 아님 | 제거 |
| `Defpoint*` | 소화전 반경 원·치수보조 | 제거 |
| `A_DASH(SHORT)` | 가장자리 파사드 곡선 호 | 유지 |
| `A_ANNO_*` `A_DIM` `A_LANDSCAPE` `TREE*` `대지경계선` `A_PARKING` 등 | 주석·조경·대지·주차 | 제거 |

## 층별 배치 (한 파일에 여러 층 도면이 흩어져 있음)

- **2~7층**: X[556000,666000] 밴드에 음수 y로 **-89,100 간격 수직 적층**.
  3F 중심 ≈ y-286,000. 창 = `Y0,Y1 = -322000 + 89100*(3-FLOOR), -250000 + 89100*(3-FLOOR)`.
- **1층(지상층)**: 별도 위치 **양수 y ≈ +82,000**("1F/400" 라벨 @ (673461,77595)).
  지상주차장·주출입구·조경 포함해 더 크고 지저분함. `render_plan.py`에서 FLOOR==1 예외 창
  `Y0,Y1=45000,120000` 사용.
- 지하/옥탑 등은 미확인.

## 렌더 레시피 (render_plan.py 로직)

1. 재귀 전개하며 **허용 아님(=DROP) 레이어/블록 제외**(위 표), `COLUMN` 블록·점자블록 스킵.
2. **강건 bbox**: 좌표 0.5~99.5 백분위수로 사이트 잔여선(창 경계 걸침) 배제 후 재계산.
3. **가장자리 밴드(4m)의 짧은(<1.8m) 세그먼트 제거** → 커튼월 창호 유닛 심볼 정리(안쪽 벽 보존).
4. **외곽을 굵은 실선**으로 마감(bbox 사각 테두리).
5. 5m 격자 + 10m 스케일바 + 실명(室名) 텍스트(개정메모·규격 텍스트는 BAD 필터로 제외).
6. **전 선을 실선으로** 그린다(라인타입 미적용). 라인타입 기반 제거는 하지 말 것 —
   **문 스윙 호가 HIDDENX2**라 같이 지워진다(실수 이력).

## 유리 칸막이(폰룸·소회의실·업무공간) — 중요

- 실물은 **강화유리 칸막이**(T5 투명강화유리·T10 강화유리). 근거:
  - `A701_729 창호 안내도 및 창호 일람표(변경)` → "3층 폰룸/소회의실/입주기업 업무공간 = 강화유리"
  - `A731_732 유리파티션 상세도_준공` → 단면·입면·코너·도어 시공 상세
- **그러나 건물 좌표계에 배치된 "평면" 도면은 어디에도 없다.** 기본평면·확대평면·코어확대·
  실내마감표·천정평면 전수 확인 결과 폰룸/소회의실은 **라벨·마감스펙·시공상세로만** 존재,
  평면 칸막이 선은 미입력(별도 인테리어 시공). → 기본 평면상 그 공간은 "개방"으로 그려짐.
- 합성으로 그려 넣는 시도는 배치가 실제와 안 맞아 보류(원본 좌표 없음).

## 저장 정책 — git(텍스트만) vs HF(대용량 전부)

이 폴더는 **git엔 텍스트만**(이 `README.md`·`render_plan.py`), **이미지·DXF·원본 dwg 등
대용량/바이너리는 전부 HuggingFace `backseollgi/MCMOT`** 에 둔다(`.gitignore`가 그렇게 강제).

| 대상 | 위치 | 받는 법 |
|------|------|---------|
| README·render_plan.py | **git** | clone |
| 원본 dwg(`메인센터 도면/`) · target_floors(png·dxf) · 참조도면 | **HF** `cad/ai_hub_cad/**` | `bash tools/fetch_assets.sh --cad` |
| `_converted/`(509M 중간산출) | 로컬만 | dwg에서 재변환(아래) |

> HF는 비공개라 `HF_TOKEN` 필요. `fetch_assets.sh` 의 CAD 섹션이 `cad/ai_hub_cad/**` 를 폴더째 받는다.

## 타겟 층 산출물 — `target_floors/` (도면 편집기/CAD용)

프로젝트에서 실제로 쓰는 **타겟 층(1F·3F)** 의 정리 완료 도면. **HF에 보관**(git 제외).
(`_converted/`는 원본 dwg의 대량 변환/중간산출물 — 로컬 전용, 재생성 가능)

| 파일 | 내용 |
|------|------|
| `AI지원센터_1F.png` / `AI지원센터_3F.png` | 렌더 이미지(5m 격자·10m 스케일바) |
| `AI지원센터_1F.dxf` / `AI지원센터_3F.dxf` | **CAD 벡터**(정리된 선만). 도면 편집기·AutoCAD에서 편집 가능 |

DXF 규격: **남서 코너=원점(0,0)**, 단위 mm(`$INSUNITS=4`), **순수 선(LINE)만 — 글자 없음**.
레이어 분리 `WALL`(벽·코어·문), `ENVELOPE`(외곽 실선), `GLASS`(유리파티션, 있을 때).
실명(室名) 텍스트는 **PNG에만** 남기고 편집기용 DXF엔 넣지 않는다(편집 시 방해 방지).
- 3F ≈ 38.9 × 39.2 m, 1F ≈ 59.1 × 49.6 m(1층은 지상 진입부 포함).
- 재생성: `render_plan.py <층> target_floors/AI지원센터_<n>F.png` (PNG와 같은 이름의 .dxf 동시 생성).

## 관련 파일

- 렌더 스크립트: `render_plan.py` (PNG + DXF 동시 출력)
- 최종 산출물: `target_floors/`
- 대량 변환본/중간산출: `_converted/*.dxf` (원본 dwg는 `메인센터 도면/`)
- 참조 이미지: `AI hub 가상 도면.png`(운영자 제공 3층 개념 배치)
- 변환 도구/설치: 루트 `tools/cad_convert.py`, `.claude/skills/cad-convert`
