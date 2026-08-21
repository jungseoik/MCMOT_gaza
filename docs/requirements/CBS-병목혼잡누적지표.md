# CBS — 병목 구역 혼잡 누적 지표

> FR-06  
> 상위 문서: [4대지표 요구사항](삼성화재-피난훈련-정량평가-4대지표-요구사항.md)

---

## 1. 목적

병목(문·계단·복도 협소부) 구역에서 임계밀도를 초과한 혼잡이 **얼마나 오래·심하게 누적됐는지**를 정량화한다.
CBS 값이 클수록 해당 병목에서 병목 혼잡이 심각하고 오래 지속된 것.

---

## 2. 입력 데이터

| 항목 | 설명 |
|------|------|
| 병목 polygon $k$ | M개. 관리자가 맵에 수동 지정 — **자유 다각형 또는 부채꼴**(문·계단 앞 형상, v1.12) |
| 그룹 라벨 $g$ | 병목 $k$가 속한 집계 그룹 (예: "출구1", "계단A") — 미지정 가능 (v1.12) |
| $A_k$ | 병목 $k$의 실면적 [m²] (축척으로 자동 계산) |
| $\rho_{\mathrm{crit},k}$ | 병목 $k$의 임계밀도 [명/m²] — 병목별 개별 지정 (UI 인라인 입력) |
| $w_k$ | 병목 $k$의 중요도 가중치 — 병목별 개별 지정 (기본 1.0) |
| $N_k(t)$ | 시각 $t$에 병목 $k$ 내 재실 객체 수 (polygon 포함 판정) |
| $T$ | 전체 분석 시간 (경보 발생~세션 종료) |

---

## 3. 처리/수식 (FR-06)

### 단계 1 — 영역 밀도

$$\rho_k(t) = \frac{N_k(t)}{A_k} \quad [\text{명/m}^2]$$

### 단계 2 — 임계 초과분 (음수는 0 절단)

$$e_k(t) = \max\!\left(0,\;\rho_k(t) - \rho_{\mathrm{crit},k}\right)$$

밀도가 임계밀도 이하인 구간은 CBS 기여 없음.

### 단계 3 — 병목 $k$의 혼잡 누적

$$\mathrm{CBS}_k = \int_0^T e_k(t)\cdot w_k\,dt = \int_0^T \max\!\left(0,\;\rho_k(t)-\rho_{\mathrm{crit},k}\right)\cdot w_k\,dt$$

### 단계 4 — 전체 CBS ($M$개 병목 합산)

$$\mathrm{CBS} = \sum_{k=1}^{M}\mathrm{CBS}_k = \sum_{k=1}^{M}\int_0^T\max\!\left(0,\;\frac{N_k(t)}{A_k}-\rho_{\mathrm{crit},k}\right)\cdot w_k\,dt$$

### 단계 5 — 그룹 집계 (선택 병목 합산·평균, v1.12)

전체 합 $\mathrm{CBS}$ 하나만 보면 "어느 문·계단이 문제였나"가 묻힌다.
같은 그룹 라벨 $g$ 를 가진 병목들에 대해:

$$\mathrm{CBS}_g^{\Sigma} = \sum_{k \in g}\mathrm{CBS}_k, \quad
\mathrm{CBS}_g^{\mu} = \frac{1}{|g|}\sum_{k \in g}\mathrm{CBS}_k, \quad
\mathrm{CBS}_g^{\max} = \max_{k \in g}\mathrm{CBS}_k$$

$\rho^{\mathrm{peak}}_g = \max_{k\in g}\rho^{\mathrm{peak}}_k$,
초과시간·risk_level 도 그룹 내 최악값을 취한다.
중요도 차이는 이미 $w_k$ 가 $\mathrm{CBS}_k$ 안에 반영돼 있으므로 합산에서 재가중하지 않는다.

운영뷰에서는 그룹 라벨을 프리셋으로, 임의 선택(체크박스)을 즉석 집계로 쓴다 —
선택 집합에 대한 합계·평균·최악은 같은 식이다.

### 이산 구현 ($\Delta t = 1\,\text{s}$ 샘플, 좌리만 적분)

$$\mathrm{CBS}_k \approx \sum_l \max\!\left(0,\;\rho_k(t_l)-\rho_{\mathrm{crit},k}\right)\cdot w_k\cdot\Delta t$$

구현: `session.py:_sample()` — `prev_density` 기준 좌리만:

```python
acc.cbs += max(0, prev_density - rho_crit) * weight * dt
```

---

## 3-1. 병목 영역 형상 (v1.12)

병목은 실제로 문·계단을 꼭짓점으로 하는 **부채꼴**로 형성된다. 이를 자유
다각형으로 손으로 찍으면 모양이 매번 다르고, 나중에 반경·각도를 못 고친다.
그래서 도형 파라미터를 저장하고 polygon은 거기서 **생성**한다.

```text
shape = {kind:"sector", center, radius, radius_in, a0, sweep, segments}
polygon = sector_polygon(shape)        # 계약상 정본 — 엔진은 polygon만 본다
```

- 생성식: `system/config/shapes.py:sector_polygon()` (백엔드가 저장 시 재생성 = 값의 진실)
- `radius_in > 0` 이면 도넛 부채꼴(문 바로 앞을 비울 때)
- `shape` 없는 병목은 기존 자유 다각형 그대로 (하위호환)
- 면적 $A_k$ 는 형상과 무관하게 polygon 실면적으로 계산 — segments=24 기준
  해석 면적과 오차 0.2% 이내

---

## 4. 해석

| 조건 | 결과 |
|------|------|
| 임계 초과 강도 ↑ | CBS ↑ |
| 임계 초과 지속시간 ↑ | CBS ↑ |
| 가중치 $w_k$ ↑ (예: 비상계단 > 복도) | 동일 혼잡에서도 CBS ↑ |
| CBS = 0 | 전 구간 임계밀도 이하 |

---

## 5. 변수 정의

| 기호 | 의미 |
|------|------|
| $k$ | 병목 구역 인덱스 (1…M) |
| $M$ | 총 병목 수 |
| $A_k$ | 병목 $k$ 실면적 [m²] |
| $\rho_k(t)$ | 병목 $k$의 순간 밀도 [명/m²] |
| $\rho_{\mathrm{crit},k}$ | 병목 $k$의 임계밀도 [명/m²] |
| $w_k$ | 병목 $k$의 중요도 가중치 |
| $e_k(t)$ | 임계 초과분 [명/m²] |
| $\mathrm{CBS}_k$ | 병목 $k$ 혼잡 누적 지표 |
| $\mathrm{CBS}$ | 전체 합산 혼잡 누적 지표 |
| $T$ | 분석 시간 [s] |

---

## 6. 출력

**bottleneck_metrics[]**:

```text
bottleneck_id       : 병목 식별자
peak_density        : max(ρ_k(t))  [명/m²]
over_threshold_sec  : 임계 초과 지속시간 [s]
cbs                 : CBS_k
risk_level          : "low" | "mid" | "high"  (최대 CBS 대비 3분위)
group               : 집계 그룹 라벨 ("" = 미분류)
```

**bottleneck_groups[]** (v1.12 — 라벨이 있는 병목만):

```text
group               : 그룹 라벨
members             : 소속 병목 id[]
count               : 소속 병목 수
cbs_sum             : Σ CBS_k        (그룹 총 혼잡)
cbs_mean            : 병목 1개당 평균
cbs_max             : 그룹 내 최악 병목
peak_density        : 그룹 내 최대 순간밀도
over_threshold_sec  : 그룹 내 최대 초과시간
risk_level          : 그룹 내 최악 등급
```

전체 `cbs_total` = Σ CBS_k.

---

## 7. 시각화

운영뷰 CBS 카드:
- 전체 `cbs_total` 수치 + 스파크라인 (시간 추이)
- 병목별 `cbs`, `peak_density`, `risk_level` 목록 + **선택 체크박스** (v1.12)
- **선택 집계 줄** — 선택 n개의 합계·평균·최악(병목명)·최대 초과시간
- **그룹 프리셋 칩** — `[전체]` + 그룹 라벨별 빠른 선택
- 진행 중 세션도 병목별 CBS 진행값(`MapState.bottlenecks[].cbs`)으로 즉시 집계
- (잔여) 세션 결과 리포트 내 병목별 CBS 막대차트

---

## 8. 구현 현황 (2026-08-21)

| 항목 | 상태 | 파일 |
|------|------|------|
| 다중 병목 polygon 정의·저장 | ✅ | `system/config/schema.py:Bottleneck` |
| 병목별 $\rho_{\mathrm{crit}}$·$w$ 개별 지정 (UI 인라인 편집) | ✅ | `view_map.js:fillBns()` |
| 영역별 밀도 $N_k(t)/A_k$ | ✅ | `session.py:_bn_densities()` |
| 임계초과 누적 적분·가중치 | ✅ | `session.py:_sample()` |
| `over_threshold_sec`, `peak_density` | ✅ | `session.py:finalize()` |
| `risk_level` (3분위) | ✅ | `session.py:_risk()` |
| `cbs_total` | ✅ | `session.py:_cbs_total()` |
| 부채꼴 영역 도구·파라미터 재편집 (v1.12) | ✅ | `shapes.py:sector_polygon()` · `view_map.js` 병목 부채꼴 |
| 그룹 라벨·그룹 집계 (합계·평균·최악, v1.12) | ✅ | `session.py:_bn_groups()` → `bottleneck_groups[]` |
| 선택 병목 즉석 집계 UI (v1.12) | ✅ | `session.js:renderCbs()` |
| 병목별 CBS 막대차트 (세션 결과) | ❌ | 잔여 과제 |
