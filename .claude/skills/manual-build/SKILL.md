---
name: manual-build
description: >-
  「피난훈련 정량평가 시스템 사용 가이드」를 새 버전으로 다시 만든다 —
  화면 재캡처 → 원고 갱신 → Word(.docx) → PDF. "사용 가이드 다시 만들어줘",
  "매뉴얼 버전 올려줘", "가이드 v0.0.2로 갱신", "스크린샷 다시 찍어서 문서 새로",
  "사용설명서 업데이트" 같은 요청에 사용. 화면이 바뀐 뒤 문서를 최신화할 때가
  주 용도. (사용 가이드, 사용자 매뉴얼, docx, PDF, 버전업, 스크린샷 재캡처)
---

# 사용 가이드 개정·재생성

문서는 **버전 폴더 = 스냅샷** 구조다. 개정판은 폴더를 통째로 복사한 뒤 그 안에서
원고와 화면을 갱신한다. 이전 버전은 그대로 남는다.

```
docs/manual/
├─ README.md              개정 이력 · 빌드 절차
├─ v0.0.1/
│   ├─ 원고.md             ← 편집 원본 (단일 진실 원천)
│   ├─ img/                ← 화면 캡처
│   ├─ *_v0.0.1.docx       ← 산출물
│   └─ *_v0.0.1.pdf        ← 최종본
└─ v0.0.2/ …
```

원고가 원본이고 docx/pdf는 산출물이다. **docx를 직접 편집하지 않는다** — 바이너리라
변경 추적이 안 되고, 다음 빌드에서 덮어써진다.

## 절차

### 1. 개정 범위 확인

사용자에게 무엇이 바뀌었는지 묻는다. 답에 따라 작업량이 갈린다.

| 변경 | 필요한 작업 |
|------|-------------|
| 오타·문구만 | 원고 수정 → 빌드 (캡처 불필요) |
| 화면 UI 변경 | 재캡처 → 원고 반영 → 빌드 |
| 기능 추가 | 재캡처 → 원고에 절 추가 → 빌드 |

버전 번호도 확인한다(기본: 마지막 자리 +1).

### 2. 버전 폴더 생성

```bash
cp -r docs/manual/<이전> docs/manual/<새버전>
```

새 폴더의 `원고.md` 상단 `@meta`의 `version`·`date`를 갱신하고,
`@revision` 표에 이번 개정 내용을 한 행 추가한다.

> 폴더명과 `version`이 다르면 빌더가 오류로 막는다(개정 절차 누락 방지).

### 3. 화면 재캡처

**기존 스크린샷을 재사용하지 않는다.** 화면이 바뀐 뒤의 문서가 목적이므로
매 버전 새로 찍는다.

```bash
conda run -n boosttrack python tools/capture_manual_shots.py \
  --out docs/manual/<새버전>/img
```

사전 조건 — :8900·:8910 기동, 카메라 최소 1대 매핑·활성.
`--skip-session`을 주면 평가 세션 예시 캡처를 건너뛴다(세션 파일이 생기지 않음).
`--only 8900` / `--only 8910` 으로 한쪽만 다시 찍을 수 있다.

캡처는 **기본 시드 설정**(17F · 구역 z3~z5 · 병목 b1·b2 · 출구 e1·e3 · 경로 r1·r2)을
담는다. 원고 본문과 부록 11.2가 이 구성을 예시로 설명하므로, 시드가 바뀌면
원고의 해당 수치도 같이 고쳐야 한다.

실패한 캡처가 있으면 원고의 해당 `@img` 블록을 지우거나 화면을 수동 확보한다.
빌드는 없는 그림을 경고만 하고 건너뛴다.

### 4. 원고 갱신

`원고.md`를 고친다. 지원 문법:

| 문법 | 용도 |
|------|------|
| `# / ## / ###` | 장 / 절 / 항 (장은 페이지 나눔 + 목차 등록) |
| `@img 파일명` … `@end` | 그림 + 자동 번호 캡션 (`[그림 5-1]`) |
| `@note` … `@end` | 참고 상자 |
| `@steps` … `@end` | 순서 목록 |
| `@flow` … `@end` | 흐름 표시 |
| `\| a \| b \|` | 표 (첫 행이 헤더) |
| `- 항목` | 글머리 목록 |
| `**굵게**` · `` `등폭` `` | 인라인 서식 |

**어투** — 실무 매뉴얼체("~합니다" / 지시는 "~하십시오"). 절차는 `@steps`,
주의는 `@note`로 분리한다. 기능 나열이 아니라 **실제 함정**을 적는다
(예: 개구부 처리를 빠뜨리면 경로가 벽을 통과해 산출된다).

### 5. 빌드

```bash
conda run -n boosttrack python tools/build_manual.py docs/manual/<새버전>/원고.md
```

docx와 pdf가 같은 폴더에 생성된다. `--no-pdf`로 PDF를 건너뛸 수 있다.

### 6. 확인

PDF를 실제로 렌더해 눈으로 본다. 빌드 성공이 곧 정상 문서는 아니다.

```bash
pdfinfo docs/manual/<새버전>/*.pdf | grep -E "Pages|Page size"   # A4 확인
pdftoppm -png -r 70 -f 1 -l 1 docs/manual/<새버전>/*.pdf /tmp/pg
```

표지·목차·그림 배치·표 넘침을 확인한다. 그림 캡션 번호가 장별로 1부터 시작하는지도 본다.

### 7. 산출물 업로드 (HF)

**docx·pdf·img는 git에 커밋하지 않는다.** `.gitignore`가 막고 있으며, 버전당 약
45MB라 저장소 이력을 키우기 때문이다. `backseollgi/MCMOT` 의 `manual/<버전>/` 에 올린다.

```bash
STG=$(mktemp -d) && mkdir -p "$STG/manual/<버전>"
cp -r docs/manual/<버전>/img docs/manual/<버전>/*.docx docs/manual/<버전>/*.pdf \
   "$STG/manual/<버전>/"
(cd "$STG" && hf upload backseollgi/MCMOT . --repo-type model --include "manual/**" \
   --commit-message "docs(manual): 사용 가이드 <버전>")
```

내려받는 쪽은 `bash tools/fetch_assets.sh --manual` 한 줄이다(HF_TOKEN 필요).
업로드 후 실제로 올라갔는지 파일 목록으로 확인한다.

### 8. 이력 기록

`docs/manual/README.md` 표에 새 버전 행을 추가한다. git에 커밋되는 것은
`원고.md`와 이 README뿐이다.

## 필요 도구

| 도구 | 라이선스 | 설치 |
|------|----------|------|
| python-docx | MIT | `pip install python-docx` |
| LibreOffice | MPL 2.0 | 시스템 설치 (`soffice`) |
| playwright | Apache 2.0 | `pip install playwright && playwright install chromium` |

> 문서 생성에 Anthropic 공식 docx 스킬을 쓰지 않는다 — 해당 스킬은 라이선스상
> 저장소에 사본을 두거나 배포할 수 없다. 위 도구만으로 동일한 결과를 얻는다.
