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
