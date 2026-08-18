# 훈련평가 — 피난훈련 정량평가 레포트

경보 세션 종료로 산출된 **4대 정량지표(IDR·EPFI·CBS·SEI)** 를 해석한 세션별 평가
레포트 모음. 작성은 **`evac-report` 스킬**이 담당한다("세션 결과 분석해줘",
"피난훈련 레포트 써줘").

- 기준: [4대지표 요구사항](../../requirements/삼성화재-피난훈련-정량평가-4대지표-요구사항.md)
- 양식: [`.claude/skills/evac-report/TEMPLATE.md`](../../../.claude/skills/evac-report/TEMPLATE.md)
- 계산 도구: [`tools/session_digest.py`](../../../tools/session_digest.py)
  (세션 JSON → 다이제스트 + 차트 5종. 해석은 사람/에이전트 몫)
- 차트: `img/<session_id>_{idr,epfi,cbs,sei,timeline}.png`

> **종합점수·A/B/C 등급은 산출하지 않는다** — 요구사항 D-8(고객 승인 가중치 확정 후
> 별도 반영). 지표 간 교차 관찰은 하되 합산하지 않는다.
> 모든 임계값은 §9 기준 **고객 미확정**이므로 판정에 "현 임계값 기준" 단서를 붙인다.

## 레포트

| 날짜 | 세션 | 층 | 지속 | 요약 |
|------|------|-----|------|------|
| 2026-08-17 | [`sess-1786977645508`](2026-08-17_sess-1786977645508.md) | default | 152.8초 | 파이프라인 검증 세션. e3 비상구 미사용(SEI 51.6) · z4 구역 미개시 · b1 병목 위험도 high |

## 세션 목록 확인

```bash
conda run -n boosttrack python tools/session_digest.py --list
```
