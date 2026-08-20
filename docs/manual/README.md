# 사용 매뉴얼 (공식 배포본)

「**피난훈련 정량평가 시스템 사용 가이드**」의 버전별 스냅샷.
Word(.docx)와 PDF로 배포하며, **PDF가 최종본**이다.

> 개발용 마크다운 가이드는 [`docs/guide/`](../guide/) 에 따로 있다. 그쪽은 화면별
> 빠른 참조용이고, 이 폴더는 **배포용 통합 문서**다. 두 곳은 별개로 관리한다.

## 버전

| 버전 | 일자 | 쪽수 | 범위 | 산출물 |
|------|------|------|------|--------|
| [v0.0.1](v0.0.1/) | 2026-08-17 | A4 41쪽 | 도면 편집기(:8910) + 멀티카메라 시스템(:8900) 전체 흐름 | [PDF](v0.0.1/피난훈련-정량평가-시스템-사용-가이드_v0.0.1.pdf) · [DOCX](v0.0.1/피난훈련-정량평가-시스템-사용-가이드_v0.0.1.docx) |
| [v0.0.3](v0.0.3/) | 2026-08-20 | A4 48쪽 | v0.0.2 + **RTSP 미리보기**(등록 전 추론 확인)·**출입구 화면 통과선**(문 앞이 대응점 범위 밖일 때), 유효 ROI 사용 기준 정정, 카메라 목록 층별 그룹·스트림명 | [PDF](v0.0.3/피난훈련-정량평가-시스템-사용-가이드_v0.0.3.pdf) · [DOCX](v0.0.3/피난훈련-정량평가-시스템-사용-가이드_v0.0.3.docx) |
| [v0.0.2](v0.0.2/) | 2026-08-19 | A4 45쪽 | v0.0.1 + **건물 훈련**(전 층 공유 세션)·리플레이 건물훈련 모드, 맵설정 미터격자·스케일바·축척출처·Reset, 매핑 커버리지·도면회전, 편집기 출발점·공간차단·요소 개별삭제·층선택. 예시 3개 층(17F·16F·지상1층·12캠) | PDF·DOCX (HF `manual/v0.0.2/` — `fetch_assets.sh --manual`) |

## 폴더 구조와 보관 위치

```
docs/manual/<버전>/
├─ 원고.md      편집 원본 — 문서 수정은 항상 여기서 한다      [git]
├─ img/         화면 캡처 (버전마다 새로 촬영)                 [HF]
├─ *.docx       산출물                                        [HF]
└─ *.pdf        최종본                                        [HF]
```

**대용량 산출물은 git이 아니라 HuggingFace에 둔다.** git에는 텍스트 원본
(`원고.md`·이 README)만 남기고, 이미지·docx·pdf(버전당 약 45MB)는
`backseollgi/MCMOT` 의 `manual/<버전>/` 에 보관한다. 버전마다 45MB씩 저장소
이력에 쌓이는 것을 막기 위한 것이다.

```bash
# 산출물 내려받기 (신규 클론 후 1회)
bash tools/fetch_assets.sh --manual        # HF_TOKEN 필요 (MCMOT 비공개)

# 새 버전을 만든 뒤 올리기
cd <스테이징>/ && hf upload backseollgi/MCMOT . --repo-type model \
  --include "manual/**" --commit-message "docs(manual): ... v0.0.2"
```

**버전 폴더 = 스냅샷.** 개정 시 폴더를 통째로 복사한 뒤 그 안에서 작업하므로
이전 버전은 그대로 남는다.

## 개정 절차

전체 절차는 **`manual-build` 스킬**이 담당한다("사용 가이드 다시 만들어줘",
"매뉴얼 버전 올려줘"). 수동으로 할 경우:

```bash
# 1. 버전 폴더 복사 후 원고의 @meta version·date, @revision 표 갱신
cp -r docs/manual/v0.0.1 docs/manual/v0.0.2

# 2. 화면 재캡처 (:8900·:8910 기동 상태여야 함)
conda run -n boosttrack python tools/capture_manual_shots.py \
  --out docs/manual/v0.0.2/img

# 3. 원고 갱신 후 빌드 → docx + pdf
conda run -n boosttrack python tools/build_manual.py docs/manual/v0.0.2/원고.md

# 4. 산출물을 HF에 업로드 (git에는 원고만 남는다)
STG=$(mktemp -d) && mkdir -p "$STG/manual/v0.0.2"
cp -r docs/manual/v0.0.2/img docs/manual/v0.0.2/*.docx docs/manual/v0.0.2/*.pdf "$STG/manual/v0.0.2/"
(cd "$STG" && hf upload backseollgi/MCMOT . --repo-type model --include "manual/**" \
   --commit-message "docs(manual): 사용 가이드 v0.0.2")
```

- 원고의 `version`과 폴더명이 다르면 빌더가 오류로 막는다(절차 누락 방지).
- **docx를 직접 편집하지 않는다.** 바이너리라 변경 추적이 안 되고 다음 빌드에서 덮어써진다.
- **스크린샷은 재사용하지 않는다.** 화면 변경 반영이 개정의 목적이다.

## 필요 도구

| 도구 | 라이선스 | 용도 |
|------|----------|------|
| [python-docx](https://python-docx.readthedocs.io/) | MIT | 마크다운 원고 → Word |
| LibreOffice (`soffice`) | MPL 2.0 | Word → PDF |
| [Playwright](https://playwright.dev/) | Apache 2.0 | 화면 캡처 |
| 나눔고딕 | OFL | 본문 글꼴 |

```bash
conda run -n boosttrack pip install python-docx playwright
conda run -n boosttrack playwright install chromium
```

## 문서에 쓰인 예시 설정

본문의 모든 화면 예시는 저장소 기본 시드(`data/seed/default/`)를 사용한다.
독자가 같은 화면을 보면서 따라올 수 있도록 한 것이므로, 시드를 바꾸면
원고 본문과 부록 11.2의 수치도 함께 고쳐야 한다.

| 구분 | 내용 |
|------|------|
| 도면 | 17F 평면도 3400 × 3207 px |
| 축척 | 73.2 m 기준 · 0.0239 m/px |
| 구역 | z3 · z4 · z5 |
| 병목 | b1 · b2 (ρcrit 0.1 명/㎡, w 1) |
| 비상구 | e1 (설계 32명) · e3 (설계 30명) |
| 피난경로 | r1 · r2 |
| 카메라 | 3대 |
