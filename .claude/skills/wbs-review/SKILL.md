---
name: wbs-review
description: >-
  주간 WBS 점검·버전 관리. 현재 레포 상태(코드/문서/커밋) 기준으로 C-LAB PoC WBS의
  진척을 점검하고, 진척 보고서를 그 주차 폴더에 남기고, 수정이 있으면 버전을 올려
  변경이력에 기록한다. "WBS 점검해줘", "이번 주 WBS 진척 보자", "WBS 버전 올려줘",
  "WBS 업데이트/갱신", "주간 WBS 정리" 같은 요청에 사용. (WBS, 주간점검, 버전관리, 진척)
---

# 주간 WBS 점검 · 버전 관리

C-LAB PoC WBS를 **매주** 현재 레포 기준으로 점검하고, 진척을 기록하고, 수정 시 버전을
올린다. xlsx는 바이너리라 변경을 git이 못 보므로 **텍스트 기록(진척 보고서 + 변경이력)을
반드시 같이 남기는 것**이 이 절차의 핵심이다.

## 컨벤션 (컨벤션 #1 — 주차 폴더 단일 관리)
- **최신 WBS 위치**: `docs/weekly/YYYY-MM-N주차/C-lab_PoC_WBS_vN.xlsx` (그 주차 폴더에만 최신본).
- **예전 버전**: `docs/wbs/`에 아카이브(현재 `v3`·`v4`).
- **변경이력(중앙 1파일)**: `docs/wbs/WBS-변경이력.md` (append-only).
- **진척 보고서(주차별)**: `docs/weekly/YYYY-MM-N주차/WBS-진척점검.md`.
- **버전 규칙**: **한 주에 한 버전.** 그 주의 현재 버전 xlsx를 **그대로(in-place) 갱신**(완료
  처리 등). 새 주차가 시작되면 직전 버전을 `cp -p`로 복사해 `vN+1`로 올린 뒤 그 주 동안 갱신.
- 주차 명명: 그 달의 몇째 주(예: 6/17 → `2026-06-3주차`). 새 주차면 폴더+`README.md` 생성.

## xlsx 다루기 (환경)
- 파이썬: `~/miniconda3/envs/boosttrack/bin/python` (openpyxl 설치돼 있음).
- 시트: `Sheet1`. 주요 컬럼 — A:WBS번호 / B:대분류 / C:중분류 / D:내용 / E:산출결과 /
  F:기한 / G:완료일정 / H:**상태**(예정·진행중·완료) / I~:간트(6~9월 주차). 버전 셀 ≈ `C5`.
- **읽기**는 openpyxl로 직접.
- **상태(H)·완료일정(G) 셀은 openpyxl로 직접 편집 허용**(`data_only` 없이 로드 → 해당 셀만
  set → save). 그 외 셀(내용·산출결과·간트)·서식은 건드리지 않는다.
- 편집 전 현재 파일을 스냅샷(예: `/tmp`)으로 복사해 두고, 저장 후 **셀 diff로 변경분만 바뀌었는지
  검증**. openpyxl 저장은 **간트 색상·차트 등 일부 서식을 손상**시킬 수 있으니 사용자에게 Excel
  육안 검토를 요청한다.

## 절차 (매주)

1. **최신본·주차 파악**
   - `docs/weekly/` 최신 주차 폴더에서 가장 높은 `..._vN.xlsx`를 찾는다.
   - 오늘 날짜의 주차 폴더가 없으면 새로 만든다(`README.md`도).

2. **레포 ↔ WBS 대조 (진척 점검)**
   각 WBS 항목(특히 `4. AI 분석 기능 개발`, `5. 대시보드`)의 `내용/산출결과`를 실제 근거와 대조:
   - 코드: `src/`(추론·TRT), `tracker/`, `webui/`(speed/counter/depth/map/server).
   - 문서: `docs/`(optimization-report, reports, webui-dev), `README.md`.
   - 최근 활동: `git log --oneline -30`.
   각 항목을 **완료 / 진행중 / 예정**으로 판정하고 **근거(file:line·커밋)**를 적는다.
   과대평가 금지 — 부분 구현은 진행중, 데모만 있으면 그 사실을 명시.
   - **항목 증감도 점검**: WBS는 상태만 바뀌는 게 아니라 **항목이 추가/삭제**될 수 있다
     (요구사항 변경·범위 조정). 직전 버전 대비 **추가된/삭제된 WBS 번호**도 잡아내고,
     총 항목 수 변동을 기록한다(아래 diff 스니펫은 WBS 번호 기준이라 행 추가/삭제도 감지).

3. **진척 보고서 작성** → `docs/weekly/<주차>/WBS-진척점검.md`
   - 상태 요약(완료/진행중/예정 수, 대분류별), 변동(지난 버전 대비), 항목별 근거 표,
     다음 주 액션. (양식은 아래 템플릿)

4. **xlsx 갱신 + 버전 표기**
   - 그 주의 현재 버전 파일에서 완료된 항목의 **상태(H)='완료', 완료일정(G)=점검일**로 셀 편집
     (위 'xlsx 다루기' 규칙). 새 주차 진입 시에만 직전 버전을 `cp -p`로 `vN+1` 복사 후 편집.
   - 버전 셀(≈`C5`)에 `vN (YYYY-MM-DD 갱신: 사유)` 표기.
   - **변경 셀 diff**: 직전 버전(`docs/wbs/` 아카이브)과 openpyxl 셀 단위 비교(아래 스니펫) →
     변경이력에 기록.

5. **변경이력 갱신** → `docs/wbs/WBS-변경이력.md` 맨 위에 새 버전 블록 추가
   - 버전·날짜·주차, **상태 변경 + 추가/삭제된 항목** 목록(WBS번호+항목+근거), 진척 요약
     (총 N개 — 변동 시 이전 대비), 다음 액션.
   - 상단 "현재 최신" 줄 갱신.

6. 커밋 제안: `docs(wbs): <주차> WBS 점검 vN+1 (진척 반영)`. (생성물 `results/`는 제외)

## 셀 단위 diff 스니펫 (WBS 번호 기준 — 추가/삭제/변경 모두 감지)
행 인덱스가 아니라 **WBS 번호(A열)로 키잉**하므로, 행이 추가/삭제돼도 어긋나지 않고
`추가 / 삭제 / 셀 변경`을 정확히 분리한다.
```bash
PY=~/miniconda3/envs/boosttrack/bin/python
$PY - <<'PYEOF'
import openpyxl
def rows_by_id(path):
    ws=openpyxl.load_workbook(path,data_only=True)["Sheet1"]; d={}
    for r in range(1,ws.max_row+1):
        wid=ws.cell(r,1).value
        if wid and isinstance(wid,str) and wid[0].isdigit():
            d[wid]=[ws.cell(r,c).value for c in range(1,ws.max_column+1)]
    return d
A=rows_by_id("이전.xlsx"); B=rows_by_id("새버전.xlsx")
print("총 항목:", len(A), "->", len(B))
print("추가:", [k for k in B if k not in A] or "없음")
print("삭제:", [k for k in A if k not in B] or "없음")
for k in B:                                  # 공통 항목의 셀 변경
    if k in A:
        for c,(va,vb) in enumerate(zip(A[k],B[k]),1):
            if (va or "")!=(vb or ""):
                print(f"[{k}] C{c} {va!r} -> {vb!r}")
PYEOF
```

## 항목 추가/삭제 (행 삽입 + 병합 재구성)

WBS 항목을 추가할 때는 **맨 아래 append가 아니라 번호순 위치에 행을 삽입**한다. 단,
이 시트는 `대분류(B)`·`중분류(C)`가 블록 단위로 **세로 병합**돼 있어 주의가 필요하다.

**openpyxl 특성/함정**
- `ws.insert_rows(idx, n)`은 **셀 값·서식(색 포함)은 자동으로 아래로 밀어주지만**, 병합셀
  범위·차트·데이터검증은 **자동으로 안 옮긴다** → 병합은 수동 재구성 필요.
- **`unmerge_cells()`를 쓰지 말 것** — 삽입 후 미생성(unmaterialized) 셀을 지우려다
  `KeyError`로 죽는다. 대신 `ws.merged_cells.ranges.clear()`로 목록만 비우고 다시 `merge_cells`.
- 새 행은 위 블록의 **대분류/중분류 병합에 포함**돼야 한다: 대분류(B)는 삽입 위치가 범위 안이면
  자동 확장되지만, **중분류(C) 블록이 삽입행 바로 위에서 끝나면 그 병합을 수동으로 1행 확장**해야
  새 행의 중분류가 빈칸이 되지 않는다.

**절차 (검증된 방식)**
1. 편집 전 스냅샷 복사. 삽입 위치 결정(예: `4.1.6`은 `4.1.5`(R42) 뒤 = R43).
2. `orig=[(m.min_col,m.min_row,m.max_col,m.max_row) ...]`로 병합 캡처 → `ws.merged_cells.ranges.clear()`.
3. `ws.insert_rows(ins,cnt)` (여러 곳이면 순서대로; 이후 삽입의 행번호는 앞 삽입 반영분).
4. 새 행: 위 블록 행에서 `cell._style` 복사, **B·C(중분류) 값은 설정 안 함**(병합에 흡수),
   간트열(I~)은 값=None·`fill=PatternFill(fill_type=None)`(계획 V 없음), `A/D/E/G/H`만 채움,
   상태 색(완료=초록 `C6EFCE` / 진행중 `FFEB9C` / 예정 무채색).
5. 캡처한 병합을 삽입량만큼 시프트(`min_row>=ins`면 +cnt) / 확장(`min_row<ins<=max_row`면 max_row+cnt)
   해서 재병합. **중분류 블록이 새 행 바로 위에서 끝나면 그 범위의 max_row를 새 행까지 확장**.
6. 검증: 번호 기준 diff(추가/삭제·기존 무변경), **각 새 행이 올바른 대/중분류 블록에 속하는지**
   (그 행을 덮는 C 병합 앵커 값 확인), 총 항목 수, 상태 색. → Excel 육안 검토 요청.

```python
import openpyxl; from copy import copy; from openpyxl.styles import PatternFill
wb=openpyxl.load_workbook(F); ws=wb["Sheet1"]; MAXC=ws.max_column
orig=[(m.min_col,m.min_row,m.max_col,m.max_row) for m in ws.merged_cells.ranges]
ws.merged_cells.ranges.clear()
INS=[(43,1)]                                  # (삽입행, 개수) — 여러 곳이면 순서대로
for ins,cnt in INS: ws.insert_rows(ins,cnt)
def style_row(r,src):                          # src(위 행) 스타일 복사
    for c in range(1,MAXC+1):
        ws.cell(r,c)._style=copy(ws.cell(src,c)._style)
        if c>=9: ws.cell(r,c).value=None; ws.cell(r,c).fill=PatternFill(fill_type=None)
style_row(43,42)
ws.cell(43,1).value="4.1.6"; ws.cell(43,4).value="..."; ws.cell(43,8).value="완료"
ws.cell(43,8).fill=copy(ws.cell(38,8).fill)    # 완료 초록 복사
def shift(b,ins,cnt):
    mnc,mnr,mxc,mxr=b
    if mnr>=ins: mnr+=cnt;mxr+=cnt
    elif mnr<ins<=mxr: mxr+=cnt
    return (mnc,mnr,mxc,mxr)
EXTEND={(3,38,3,42):43}                         # 중분류 블록을 새 행까지 확장: {원본범위:새max_row}
for b in orig:
    for ins,cnt in INS: b=shift(b,ins,cnt)
    mnc,mnr,mxc,mxr=b
    if b in EXTEND: mxr=EXTEND[b]
    if (mnr,mnc)!=(mxr,mxc): ws.merge_cells(start_row=mnr,start_column=mnc,end_row=mxr,end_column=mxc)
wb.save(F)
```

> **항목 삭제**도 같은 원리(`delete_rows` + 병합 시프트/축소). 삭제 시 그 행이 단독으로
> 속한 중분류 블록이면 병합 범위를 1행 줄인다. 추가/삭제는 변경이력에 `추가/삭제 WBS 번호`로 기록.

## 진척 보고서 템플릿
```markdown
# WBS 진척 점검 — <YYYY년 M월 N주차> (vN 기준)
- 점검일 / 기준 버전 / 레포 커밋(HEAD 단축해시)
## 1. 진척 요약
- 총 N개: 완료 X / 진행중 Y / 예정 Z  (지난 버전 대비 +a 완료, 항목 ±b 등)
- 대분류별 표
## 2. 이번 주 변동 (vN-1 → vN)   ← 상태 변경 + 추가/삭제 항목
## 3. 항목별 점검 (완료/진행중 위주, 근거 file:line·커밋)
## 4. 리스크 / 수정 필요
## 5. 다음 주 액션
```

## 주의
- 진척은 **레포 사실 기준**으로만. WBS에 "완료"여도 코드 근거가 없으면 보고서에 플래그.
- xlsx 자동 편집은 **상태(H)·완료일정(G) 셀에 한정**(그 외 셀·서식 보존). 저장 후 서식 손상
  가능성이 있어 편집 전 스냅샷 보존 + Excel 육안 검토 요청.
- 원본 NAS·`docs/wbs/` 아카이브는 건드리지 않는다.
