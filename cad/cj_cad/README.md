# CJ제일제당센터 CAD — 층별 평면도 추출

삼성화재 PoC 현장(CJ제일제당센터)의 통짜 건축 CAD `A-101_128_각 층 평면도_최종_수정.dwg`
에서 **운영/편집기에서 쓸 층별 깨끗한 평면도**(벽·문·창·기둥·계단만, 글자·치수·해치 제거)를
잘라내는 작업 폴더. AI-Hub 케이스(`cad/ai_hub_cad/`)와 같은 패턴.

> 배경: 기존 운영 1층 맵 `data/sites/default/map_floor3.png`(지상1층)은 A-101에서
> 1층만 잘라 **래스터 PNG로만** 존재했고 **벡터 소스(잘라낸 중간본)가 레포에 없었다**
> (`docs/재현-새-GPU서버에서-현재상태-그대로.md:79~89`). 이 폴더가 그 **1층 벡터 소스를
> 영구 자산으로 확보**한다 — 다음에 1층 맵을 손볼 때 A-101 통짜를 다시 안 뒤져도 된다.

## 산출물

| 파일 | 내용 | 비고 |
|------|------|------|
| `CJ제일제당센터_1F.dxf` | **지상1층 정리 벡터**(벽·문·창·기둥·계단, 레이어 분리) | 편집기·AutoCAD 편집용 |
| `CJ제일제당센터_1F.png` | 렌더(용도별 색 + 5m 격자 + 10m 스케일바) | 사람이 보는 용 |

### DXF 규격
- **남서(좌하단) 코너 = 원점 (0,0)**, 단위 **mm**(`$INSUNITS=4`). 약 **83.6 × 70.7 m**.
  (참고: 기존 `map_floor3.png` = 93.8 × 66.9 m. 자른 범위가 달라 **그대로 겹치지 않음** — 아래 주의)
- **순수 선(LINE)만, 글자·치수 없음**(편집 방해 방지). 레이어 분리:
  `WALL`(벽·실구획) · `DOOR`(문·셔터·방화문) · `GLASS`(창호·커튼월·유리) ·
  `COLUMN`(기둥, 사용자 요청으로 유지) · `STAIR`(계단·보이드) · `ENVELOPE`(외곽 테두리 실선 4선).

## 지상1층 band 어떻게 찾았나

A-101은 **지하5층~지상20층+옥탑을 한 시트에 세로로 적층**(층당 ΔY≈118,800mm). 각 층 도면
**아래에 "지상N층 평면도" 캡션**이 붙어 있어, 캡션 좌표로 band를 특정했다
(`find_floor_labels.py`).

- **지상1층 캡션 @ y≈2,296,152 mm** (바로 위 지하1층 캡션 2,414,952 / 아래 지상2층 2,177,352)
- 도면은 캡션 위쪽 → **band Y = [2,290,000 .. 2,418,000] mm** 로 추출.

## 레이어 정리 정책 (band 내 119개 레이어 중)

- **남김(구조)**: `AA-CWALL/DWALL/BRCK/STON/석재` `A-WA3` `실구획선`(WALL) ·
  `AA-DOOR` `A-DOOR` `방화도어` `A-SHU/AA-SHU`(DOOR) · `AA-WINDOW/WIN4` `A-WIN` `유리` `AA-GLASS`(GLASS) ·
  `AA-COL` `STEEL COLUMN`(COLUMN) · `STAIR` `VOID`(STAIR).
- **버림(해치·패턴 clutter, 70만+ 선분/전체 99%)**: `AA-TOTO6`(446,950) `장애인`(129,231)
  `A-ETC` `00-F-*` `HATCH` `AA-PATT` `C-ANNO-PATT` `SG 블럭*` `SPIDER` `DM` `daform` 등.
- **버림(옥외 — 건물 밖)**: `080808 조경base$*` `0-차선` `CAR/PARK/parking` `충돌방지턱`
  `배치도-보도경계` `0-대지경계선` `SITE` 등. → 사용자 결정: **건물 footprint만** 유지.
- **버림(주석·치수·심볼)**: `AA-AXIS/BUBBLE` `DIM` `AZ-LIN*` `TEXT` `A-TXT` `Mark-실명`
  `AA-LEAD/지시선` `Cloud`(개정) 등. 편집기 DXF엔 글자 안 넣는 관행.
- **건물 클러스터 산출**: band에 흩어진 잔여 구조선(사이트 심볼)이 bbox를 399m로 늘려,
  축별 **밀집영역(sliding window: x100m·y90m)**으로 건물 클러스터만 남겼다(`save_1f.py`).

## ⚠️ 주의 — 기존 운영 맵과의 정합

이 DXF는 **A-101 원도면 좌표**를 남서코너 원점으로 옮긴 것이라, 기존 운영 1층 맵
`map_floor3.png`(2346×1672px, 0.039995 m/px)와 **범위·스케일·회전이 자동으로 일치하지 않는다**.
`:8900` 도면 편집기에 올려 **재캘리브(스케일·구역·출구·경로)**를 새로 떠야 운영에 투입 가능.
(기존 map_floor3 좌표계를 유지할 거면 이 DXF를 그 PNG에 맞춰 정렬하는 추가 작업 필요.)

## 재생성

```bash
PY=~/miniconda3/envs/boosttrack/bin/python
# 1) DWG→DXF (ODA, cad-convert 스킬). 130MB 중간본 → _converted/ (로컬 전용)
$PY tools/cad_convert.py dwg2dxf --in "cad/A-101_128_각 층 평면도_최종_수정.dwg" --out cad/cj_cad/_converted/
# 2) 층 라벨로 band 확인
$PY cad/cj_cad/find_floor_labels.py "cad/cj_cad/_converted/A-101_128_각 층 평면도_최종_수정.dxf"
# 3) band 미리보기(전 레이어) + 레이어별 선분 수
$PY cad/cj_cad/extract_1f.py
# 4) 구조만 추출 → 저장(--save 빼면 미리보기만)
$PY cad/cj_cad/save_1f.py --save
```

다른 층(지하5~지상20)도 같은 방식 — `save_1f.py`의 `Y0,Y1`을 해당 캡션 기준으로 바꾸면 된다
(캡션 y는 `find_floor_labels.py` 출력 참조, 층당 ΔY≈118,800mm).

## 저장 정책 (git vs 대용량)

- **git 추적(텍스트·스크립트·소도면)**: 이 `README.md`, `*.py`, 산출 `CJ제일제당센터_1F.png`(276KB).
- **대용량/로컬·HF**: `A-101_*.dwg`(14MB, 이미 gitignore) · `_converted/*.dxf`(130MB, 재생성 가능) ·
  `CJ제일제당센터_1F.dxf`(1.7MB) — AI-Hub 정책과 동일하게 **git엔 텍스트만** 권장.
  (DXF를 레포에 꼭 담으려면 Git LFS 검토.)
