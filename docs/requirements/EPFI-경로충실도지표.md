# EPFI — 권장 경로 대비 실제 이동 경로 충실도

> FR-05  
> 상위 문서: [4대지표 요구사항](삼성화재-피난훈련-정량평가-4대지표-요구사항.md)

---

## 1. 목적

각 사람이 권장 대피경로를 얼마나 충실히 따랐는지, 경로 이탈 정도를 0~100 점수로 정량화한다.
완전히 따르면 100점, 허용 이탈거리 밖을 오래 걸을수록 0점에 수렴한다.

---

## 2. 입력 데이터

| 항목 | 설명 |
|------|------|
| $P_i(t)$ | 객체 $i$의 평면도 좌표 시계열 (호모그래피 변환 결과) |
| 권장 경로 polyline | 관리자 자유곡선 화살표 또는 CAD 산출 — 맵 좌표계 |
| `assigned_route_id` | 세션 시작 시 각 객체에 배정된 경로 (최근접 경로 자동 배정) |
| $T_i$ | 객체 $i$의 경로 평가 시간 ($t_{i,\mathrm{end}} - t_{i,\mathrm{start}}$) |
| $d_{\mathrm{allow}}$ | 허용 이탈거리 [m] — UI/config 입력 (기본 2.0 m). **경로 중심선에서 한쪽 반경** — $d_i$ 는 중심선까지의 부호 없는 최단거리라 좌/우 구분이 없다. "허용 통로 폭"으로 읽으면 **좌우 합 $2 \times d_{\mathrm{allow}}$** (예: 1.0 m → 폭 2 m) |

---

## 3. 처리/수식 (FR-05)

### 3.1 객체별 경로 이탈거리 시계열

매 시각 $t$에서 객체 $i$의 현재 위치와 배정된 권장 경로 polyline 사이의 **최근접 거리**:

$$d_i(t) = \min_{\mathbf{p}\in\mathrm{route}_i}\left\|\mathbf{P}_i(t) - \mathbf{p}\right\| \quad [\text{m}]$$

경로 위에 있으면 $d_i(t) = 0$.

### 3.2 객체별 누적 이탈량

$$\int_{t_{i,\mathrm{start}}}^{t_{i,\mathrm{end}}} d_i(t)\,dt \quad [\text{m·s}]$$

실제로는 1초 샘플 사다리꼴 적분으로 근사:

$$\sum_{k} \frac{d_i(t_k) + d_i(t_{k+1})}{2} \cdot \Delta t$$

### 3.3 객체별 EPFI

$$\mathrm{EPFI}_i = \max\!\left(0,\;1 - \frac{1}{T_i \cdot d_{\mathrm{allow}}}\int_{t_{i,\mathrm{start}}}^{t_{i,\mathrm{end}}} d_i(t)\,dt\right) \times 100$$

동치 표기:

$$\mathrm{EPFI}_i = \max\!\left(0,\;1 - \frac{\text{cumulative\_deviation}_i}{\text{travel\_time}_i \times d_{\mathrm{allow}}}\right) \times 100$$

> **직관**: $x$축 = 시각, $y$축 = $d_i(t)$로 그린 곡선의 **아래 면적**이 누적 이탈량.  
> 면적이 $T_i \times d_{\mathrm{allow}}$(허용 사각형)에 도달하면 0점.

### 3.4 전체 EPFI

$$\mathrm{EPFI} = \frac{1}{n}\sum_{i=1}^{n}\mathrm{EPFI}_i \quad \text{(유효 객체 평균, }T_i > 0\text{)}$$

---

## 4. d_i(t) 시계열 저장·지연 표출 (2026-07-14 확정)

- 각 객체의 $d_i(t)$를 **1초 샘플 단위로 저장소에 기록**한다.
- 대시보드는 강제 실시간 갱신 없이 **저장본에서 지연 표출** 가능 (객체별 곡선+면적 시각화).
- 모든 점수는 최종값에서 원본 시계열까지 **역추적 가능**해야 한다 (§8 역추적성).

---

## 5. 변수 정의

| 기호 | 의미 |
|------|------|
| $i$ | 탐지 객체 인덱스 |
| $P_i(t)$ | 객체 $i$의 평면도 좌표 (m) |
| $d_i(t)$ | 객체 $i$의 배정 경로 최근접 거리 (m) |
| $T_i$ | 객체 $i$의 경로 평가 시간 (s) |
| $d_{\mathrm{allow}}$ | 허용 이탈거리 (m) — 중심선 한쪽 반경, 좌우 합 폭 ×2 |
| $\mathrm{EPFI}_i$ | 객체별 경로 충실도 (0~100) |
| $\mathrm{EPFI}$ | 전체 평균 경로 충실도 (0~100) |
| $n$ | 유효 객체 수 ($T_i > 0$인 객체) |

---

## 6. 출력

**person_metrics[]**:

```text
global_track_id     : 객체 식별자
assigned_route_id   : 배정 경로
duration_sec        : T_i
mean_deviation_m    : mean(d_i(t))
max_deviation_m     : max(d_i(t))
epfi                : EPFI_i  (0~100)
```

전체 `epfi_avg` (세션 결과), 객체별 `d_i(t)` 시계열 (세션 저장소).

---

## 7. 시각화

운영뷰 EPFI 카드 (session.js):
- 전체 평균 EPFI 수치 + 스파크라인 (시간 추이)
- **객체별 d_i(t) 차트** (`drawDev()`): x축 = 시각, y축 = d_i(t), d_allow 기준선 표시 (우측 라벨)
- 세션 종료 후: 객체별 EPFI 분포 히스토그램

---

## 8. 구현 현황 (2026-07-15)

| 항목 | 상태 | 파일 |
|------|------|------|
| 호모그래피 궤적 (맵 좌표) | ✅ | `system/metrics/engine.py` |
| 경로 polyline 모델 (다구간) | ✅ | `system/config/schema.py:Route` |
| 최근접 거리 $d_i(t)$ 계산 | ✅ | `system/spatial/geometry.py:nearest_on_polyline()` |
| 시간 적분 + EPFI_i 산출 | ✅ | `system/metrics/session.py` |
| 전체 epfi_avg 산출 | ✅ | `session.py:_epfi_avg()` |
| d_i(t) 시계열 저장 | ✅ | `session.py:EvalSession.persons` |
| 개별 차트 (drawDev) | ✅ | `session.js:drawDev()` |
| mean/max deviation 기록 | ✅ | `session.py:_person_metric()` |
| CAD API 경로 자동입력 | ❌ | 미구현 (D-2 ②) |
| d_i(t) SSE 실시간 스트리밍 | ⚠️ | 현재 저장 후 지연 표출 |
