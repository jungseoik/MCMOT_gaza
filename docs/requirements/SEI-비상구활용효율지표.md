# SEI — 비상구 공간 활용 효율성 지표

> FR-07  
> 상위 문서: [4대지표 요구사항](삼성화재-피난훈련-정량평가-4대지표-요구사항.md)

---

## 1. 목적

실제 출구 사용 분포가 설계 통과용량 분포와 얼마나 일치하는지(출구 쏠림/저활용)를 **L1 거리 기반으로 정량화**.
분포가 동일하면 100, 쏠림이 심할수록 0.

---

## 2. 입력 데이터

| 항목 | 설명 |
|------|------|
| 비상구 $j$ | 총 $m$개. 맵에 지정한 가상 통과선 (2점 + 안쪽 방향) |
| $W_{j,\mathrm{eff}}$ [m] | 통과선 픽셀 길이 × m_per_px 로 **도면 축척에서 자동 산출**. 도면과 실제 문 폭이 다를 수 있어 **문마다 수동 덮어쓰기 가능**(`width_m`) — 지정하면 그 값으로 계산 (v1.12) |
| $q_{\mathrm{design}}$ [인/분/m] | 단위 유효폭당 설계 통과기준. 사이트 전역값 + **문별 개별 지정**(`exits[].q_design`, v1.12). **현장·설계기준마다 다른 값**이라 코드 기본값(60)은 자리표시자일 뿐 — 고객 확정 대상(D-6) |
| $C_{j,\mathrm{design}}$ | 자동 산출: $W_{j,\mathrm{eff}} \times q_{\mathrm{design},j}$ — **파생값이라 직접 편집 대상이 아니다** |
| 객체 궤적 | 평면도 좌표 — 방향성 crossing 검출용 |

---

## 3. 처리/수식 (FR-07)

### 단계 1 — 설계 통과용량

$$C_{j,\mathrm{design}} = W_{j,\mathrm{eff}} \times q_{\mathrm{design},j} \quad [\text{인/분}]$$

사람이 조절하는 값은 **폭과 기준**이고 $C_j$ 는 그 결과다 (v1.12). 해소 순서:

| 항목 | 우선순위 |
|------|----------|
| $W_{j,\mathrm{eff}}$ | `exits[].width_m`(수동) → 통과선 길이 × `m_per_px`(도면 자동) → 없으면 산출 불가 |
| $q_{\mathrm{design},j}$ | `exits[].q_design`(문별) → `thresholds.q_design`(사이트 전역) |

$C_j = \max(1, \mathrm{round}(W \times q))$ 는 **설정 로드·저장마다 백엔드가 다시 파생**한다
(`SiteConfig._derive_exit_capacity`) — 프론트에서만 계산하면 API로 직접 넣은 설정이나
축척이 나중에 잡힌 층에서 $C_j$ 가 비어 SEI 분포에서 조용히 빠진다(v1.12 이전 실제 증상).
폭을 못 구하는 출구($W$ 없음)는 $C_j=\text{null}$ 로 분포에서 제외된다.

리플레이 재산출에서도 같은 규칙이 적용된다 — `exits:{id:{width_m,q_design}}` 를 주면
$C_j$ 를 다시 파생하고, `design_capacity` 를 직접 주면 그 값이 최종이다. 전역
`thresholds.q_design` 변경도 $C_j$ 에 반영된다(v1.12 이전에는 스냅샷 $C_j$ 가 그대로 남아
q_design을 바꿔도 SEI가 꿈쩍 안 했다).

### 단계 2 — 실제 통과인원 (최초 out 방향만)

$$E_{j,\mathrm{actual}} = \sum_i I_{i,j}, \quad I_{i,j} = \begin{cases}1 & \text{객체 } i \text{가 비상구 } j \text{를 최초 out 방향 통과}\\0 & \text{그 외}\end{cases}$$

- out = inside 반평면 → 바깥 방향 (안쪽 방향 벡터 반대)
- 왕복·재진입은 debounce 제외

### 단계 3 — 정규화 분포

$$P_{j,\mathrm{design}} = \frac{C_j}{\sum_c C_c}, \quad P_{j,\mathrm{actual}} = \frac{E_j}{\sum_c E_c}$$

### 단계 4 — SEI (L1 거리 기반, TVD 정규화)

$$\mathrm{SEI} = \left(1 - \frac{1}{2}\sum_{j=1}^{m}\left|P_{j,\mathrm{actual}} - P_{j,\mathrm{design}}\right|\right) \times 100$$

동치 표기:

$$\mathrm{SEI} = \left(1 - \frac{1}{2}\sum_{j=1}^{m}\left|\frac{E_j}{\sum E} - \frac{C_j}{\sum C}\right|\right) \times 100$$

범위: $0 \leq \mathrm{SEI} \leq 100$.  
$\sum E = 0$이면 `insufficient_data`.

> **TVD(Total Variation Distance) 정규화**: $\frac{1}{2}\sum|p-q|$ 는 출구 수와 무관하게 $[0,1]$ 범위를 보장.  
> MAE($\frac{1}{m}\sum|p-q|$)는 출구 수에 따라 스케일이 달라지므로 사용하지 않음.

### 단계 5 — 폐쇄 비상구 동적 마스킹 (옵션, 미구현)

$$C'_{j,\mathrm{design}}(t) = C_{j,\mathrm{design}} \times M_j(t), \quad M_j \in \{0,1\}$$

$$P'_{j,\mathrm{design}}(t) = \frac{C_j M_j}{\sum_c C_c M_c} \quad \text{(재정규화)}$$

$$\mathrm{SEI}(t) = \left(1 - \frac{1}{2}\sum_j\left|P_{j,\mathrm{actual}} - P'_{j,\mathrm{design}}(t)\right|\right) \times 100$$

모든 비상구 폐쇄 시 ($\sum C_c M_c = 0$) → `insufficient_data`.

---

## 4. 변수 정의

| 기호 | 의미 |
|------|------|
| $j$ | 비상구 인덱스 (1…m) |
| $m$ | 총 비상구 수 |
| $W_{j,\mathrm{eff}}$ | 비상구 $j$ 유효폭 [m] |
| $q_{\mathrm{design}}$ | 단위 유효폭당 설계 통과기준 [인/분/m] |
| $C_{j,\mathrm{design}}$ | 비상구 $j$ 설계 통과용량 [인/분] |
| $E_{j,\mathrm{actual}}$ | 비상구 $j$ 실제 통과인원 |
| $P_{j,\mathrm{design}}$ | 설계 통과 분포 비율 |
| $P_{j,\mathrm{actual}}$ | 실제 통과 분포 비율 |
| $M_j(t)$ | 비상구 $j$ 개방 여부 마스크 (0=폐쇄, 1=개방) |
| $\mathrm{SEI}$ | 비상구 공간 활용 효율성 (0~100) |

---

## 5. 출력

**exit_metrics[]**:

```text
exit_id           : 비상구 식별자
actual_count      : E_j (고유 최초 통과인원)
design_capacity   : C_j (설계 통과용량) = W_eff × q_design
actual_share      : P_j,actual
design_share      : P_j,design
width_m           : 계산에 쓰인 W_eff [m]            (v1.12)
width_manual      : true=사람이 지정, false=도면 자동  (v1.12)
q_design          : 계산에 쓰인 q [인/분/m]           (v1.12)
```

폭·기준을 결과에 남기는 이유: 리포트에서 "이 C_j 가 정확한 값으로 나왔나"를
되짚을 수 있어야 한다. 운영뷰 SEI 카드의 출구별 행에도 `폭 3.57m(수동) × 6인/분/m`
형태로 표시된다.

전체 `sei` (0~100 또는 `null` = insufficient_data).

---

## 6. 시각화

운영뷰 SEI 카드 (session.js):
- SEI 수치 (0~100, insufficient_data)
- **설계 vs 실제 분포 그룹 바차트** (`drawSeiGrouped()`): 비상구별 2개 바 (cyan=설계, orange=실제)
- **출구별 Δ 차이 행**: $\Delta_j = P_{j,\mathrm{actual}} - P_{j,\mathrm{design}}$, 최대 편차 출구 강조

---

## 7. 구현 현황 (2026-08-21)

| 항목 | 상태 | 파일 |
|------|------|------|
| 가상선 방향성 통과 카운팅 (in/out, debounce) | ✅ | `system/metrics/engine.py:_exits` |
| 비상구별 다중 카운터 | ✅ | `system/metrics/engine.py` |
| $q_{\mathrm{design}}$ 전역 파라미터 입력 | ✅ | `system/config/schema.py:Thresholds.q_design` |
| $q_{\mathrm{design}}$ **문별** 지정 (v1.12) | ✅ | `schema.py:ExitLine.q_design` · `view_map.js:fillExits()` |
| $W_{\mathrm{eff}}$ 도면 자동 산출 + **수동 덮어쓰기** (v1.12) | ✅ | `schema.py:ExitLine.width_m`·`auto_width_m()` |
| $C_j$ 자동 계산 ($W_{\mathrm{eff}} \times q_{\mathrm{design},j}$) | ✅ | `schema.py:SiteConfig._derive_exit_capacity()` (백엔드가 정본) |
| $C_j$ 근거(폭·수동여부·기준) 결과 기록 (v1.12) | ✅ | `contracts.py:ExitMetric.width_m/width_manual/q_design` |
| 리플레이에서 폭·기준 재파라미터화 (v1.12) | ✅ | `replay.py:_apply_overrides()` |
| L1 분포비교 SEI 수식 | ✅ | `session.py:_sei()` |
| `insufficient_data` ($\sum E=0$) 예외 | ✅ | `session.py:_sei()` → `None` 반환 |
| 그룹 바차트 + Δ 행 시각화 | ✅ | `session.js:drawSeiGrouped()` |
| 폐쇄 비상구 동적 마스킹 ($M_j$ 토글) | ❌ | 잔여 과제 |
