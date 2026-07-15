# IDR — 구역별 피난 반응 확산 지표

> FR-03 (피난 개시 판정) · FR-04 (IDR 산출)  
> 상위 문서: [4대지표 요구사항](삼성화재-피난훈련-정량평가-4대지표-요구사항.md)

---

## 1. 목적

경보 이후 각 구역이 **얼마나 빨리, 얼마나 멀리 떨어진 곳까지** 피난을 개시했는지를 정량화한다.
반응이 느린 구역·거리 대비 지연을 드러내어 훈련 취약지점을 식별한다.

---

## 2. 입력 데이터

| 항목 | 설명 |
|------|------|
| `t_alarm` | 경보 발생 시각 |
| `S_origin_j` ($j=1\ldots N$) | **N개 경보 발생원** 위치 (맵 좌표). 맵설정 [경보원] 도구로 지정. |
| 구역 polygon 집합 | IDR 판정 단위. 관리자가 맵에 수동 지정 (Option B) |
| 격자 셀 크기 $c$ [m] | 격자 BFS 해상도 — `grid.cell_size_m` (기본 2.0 m). 맵 전체를 정사각형 셀로 자동 분할 (Option C 배경 격자) |
| 객체 시계열 | 각 구역 내 재실 객체의 평면도 좌표·속도·방향 |
| 권장 피난 경로 | Route polyline (관리자 자유곡선 화살표 또는 CAD 산출) |
| 임계값 | `v_th`, `a_th`, `r_th`, `Δt_hold` (UI/config 입력, 버전 관리) |

---

## 3. 피난 개시 판정 수식 (FR-03)

### 3.1 기본 변수

시각 $t$에 구역 $e$에서 탐지된 객체 집합:

$$\mathcal{I}_e(t), \quad N_e(t) = \left|\mathcal{I}_e(t)\right|$$

객체 $i$의 이동 벡터: $\mathbf{V}_i(t)$  
객체 $i$ 위치에서의 권장 피난 방향 벡터: $\mathbf{G}_i(t)$  
(최근접 Route polyline 구간 tangent — 그리드 셀 field 미구현 시 대체, D-2)

---

### 3.2 객체별 이동 방향 정렬도

$$a_i(t) = \cos\theta_i(t) = \frac{\mathbf{V}_i(t)\cdot\mathbf{G}_i(t)}{\left\|\mathbf{V}_i(t)\right\|\left\|\mathbf{G}_i(t)\right\|}$$

| 값 | 해석 |
|----|------|
| $a_i \approx 1$ | 권장 피난 방향과 동일하게 이동 |
| $a_i \approx 0$ | 수직에 가깝게 이동 |
| $a_i \approx -1$ | 역방향으로 이동 |

---

### 3.3 구역별 평균 이동 속도

$$v_e(t) = \frac{1}{N_e(t)}\sum_{i\in\mathcal{I}_e(t)}\left\|\mathbf{V}_i(t)\right\| \quad [\text{m/s}]$$

---

### 3.4 구역별 평균 정렬도

$$a_e(t) = \frac{1}{N_e(t)}\sum_{i\in\mathcal{I}_e(t)} a_i(t)$$

---

### 3.5 조건 만족 객체 비율

$$r_e(t) = \frac{\left|\left\{i\in\mathcal{I}_e(t)\;\middle|\;\left\|\mathbf{V}_i(t)\right\|\geq v_{\mathrm{th}},\;a_i(t)\geq a_{\mathrm{th}}\right\}\right|}{N_e(t)}$$

$$0 \leq r_e(t) \leq 1$$

---

### 3.6 이진 판정값 (세 조건 동시)

$$S_e(t) = \mathbb{1}\!\left[v_e(t)\geq v_{\mathrm{th}}\right] \cdot \mathbb{1}\!\left[a_e(t)\geq a_{\mathrm{th}}\right] \cdot \mathbb{1}\!\left[r_e(t)\geq r_{\mathrm{th}}\right]$$

$S_e(t) = 1$이면 세 조건 모두 만족.

---

### 3.7 피난 개시 시점 ($\Delta t_{\mathrm{hold}}$ 연속 유지)

$$t_{e,\mathrm{start}} = \inf\left\{t\geq t_{\mathrm{alarm}}\;\middle|\;S_e(\tau)=1,\;\forall\tau\in\left[t-\Delta t_{\mathrm{hold}},\,t\right]\right\}$$

적분 표기:

$$t_{e,\mathrm{start}} = \inf\left\{t\geq t_{\mathrm{alarm}}\;\middle|\;\int_{t-\Delta t_{\mathrm{hold}}}^{t}S_e(\tau)\,d\tau\geq\Delta t_{\mathrm{hold}}\right\}$$

> **직관**: 속도·정렬도·비율 세 조건이 한 번이라도 깨지면 카운터 리셋. $\Delta t_{\mathrm{hold}}$ 동안 연속 만족이 최초로 확인된 순간을 개시 시점으로 확정.

---

### 3.8 이산 구현 (프레임 기반)

영상 샘플 주기 $\Delta t$ (현재 고정 1s):

$$K_{\mathrm{hold}} = \left\lceil\frac{\Delta t_{\mathrm{hold}}}{\Delta t}\right\rceil$$

프레임 $k$의 조건:

$$S_e[k] = \mathbb{1}\!\left[v_e[k]\geq v_{\mathrm{th}}\right]\cdot\mathbb{1}\!\left[a_e[k]\geq a_{\mathrm{th}}\right]\cdot\mathbb{1}\!\left[r_e[k]\geq r_{\mathrm{th}}\right]$$

피난 개시 판정:

$$\prod_{q=k-K_{\mathrm{hold}}+1}^{k} S_e[q] = 1 \quad \text{을 최초로 만족하는 } t_k = t_{e,\mathrm{start}}$$

**구현 방식** (`session.py:_sample()`):  
`cond_since` — 조건 최초 성립 시각 추적. 조건 깨지면 `None` 리셋.  
`t - cond_since ≥ dt_hold` 이면 `started_at = cond_since` 확정.

**구현 의사코드**:

```python
for each timestamp t:
    objects = detected_objects_in_zone[e][t]
    if not objects:
        cond_since = None
        continue

    v_e = mean(norm(V_i) for i in objects)
    a_e = mean(cosine(V_i, G_i) for i in objects)
    r_e = sum(1 for i in objects
              if norm(V_i) >= v_th and cosine(V_i, G_i) >= a_th) / len(objects)

    if v_e >= v_th and a_e >= a_th and r_e >= r_th:
        if cond_since is None:
            cond_since = t
        if t - cond_since >= dt_hold:
            t_e_start = cond_since   # 개시 확정
            break
    else:
        cond_since = None
```

---

## 4. IDR 산출 수식 (FR-04, v1.6 — Option C 격자 BFS + N-origin)

### 4.1 구역-경보원 거리 $D(e, S_j)$ — 격자 BFS (Option C)

맵 전체를 셀 크기 $c$의 정사각형 격자로 분할한다 (배경 격자).  
구역 polygon $e$ 안에 포함된 셀 centroid 집합: $\mathcal{C}_e$.

경보원 $j$에서 구역 $e$까지의 거리:

$$D(e,\, S_j) = \frac{1}{|\mathcal{C}_e|} \sum_{k \in \mathcal{C}_e} d_{\mathrm{BFS}}(k,\, S_j)$$

- $d_{\mathrm{BFS}}(k, S_j)$: 격자 BFS (4방향 인접, 가중치 $c$) 최단거리 (m)
- $|\mathcal{C}_e| = 0$ (셀 없음): Zone centroid와의 직선거리 폴백
- 격자/축척 미설정: 수동 SpatialGraph Dijkstra → 직선거리 순으로 폴백

### 4.2 경보원별 구역 IDR

$$\mathrm{IDR}_{e,j} = \frac{D(e,\, S_j)}{\max\!\left(t_{e,\mathrm{start}} - t_{\mathrm{alarm}},\;\varepsilon\right)} \quad [\text{m/s}]$$

### 4.3 N-origin 평균 IDR

$$\mathrm{IDR}_e = \frac{1}{N} \sum_{j=1}^{N} \mathrm{IDR}_{e,j}$$

- $\varepsilon = 10^{-6}$ s (delay=0 방지)
- 피난 개시 미검출 구역: `status = not_started`, IDR = null
- $N=1$ (단일 경보원): 기존 공식과 동일

---

## 5. 변수 정의

| 기호 | 의미 |
|------|------|
| $e$ | 분석 대상 구역 인덱스 |
| $j$ | 경보 발생원 인덱스 ($1 \ldots N$) |
| $i$ | 탐지 객체 인덱스 |
| $t$ | 현재 시각 |
| $S_j$ | $j$번째 경보 발생원 위치 (맵 px) |
| $c$ | 격자 셀 한 변 크기 (m) — `grid.cell_size_m` |
| $\mathcal{C}_e$ | 구역 $e$ polygon 내 격자 셀 centroid 집합 |
| $d_{\mathrm{BFS}}(k, S_j)$ | 격자 BFS — 셀 $k$ centroid에서 경보원 $S_j$까지 최단거리 (m) |
| $D(e, S_j)$ | 경보원 $j$ → 구역 $e$ 격자 BFS 평균거리 (m) |
| $\mathcal{I}_e(t)$ | 시각 $t$에 구역 $e$에서 탐지된 객체 집합 |
| $N_e(t)$ | $\left\|\mathcal{I}_e(t)\right\|$ |
| $\mathbf{V}_i(t)$ | 객체 $i$의 이동 벡터 |
| $\mathbf{G}_i(t)$ | 객체 $i$ 위치에서의 권장 피난 경로 방향 벡터 |
| $a_i(t)$ | 객체 $i$의 이동 방향 정렬도 (cosine) |
| $v_e(t)$ | 구역 $e$의 평균 이동 속도 |
| $a_e(t)$ | 구역 $e$의 평균 이동 방향 정렬도 |
| $r_e(t)$ | 속도·정렬도 동시 만족 객체 비율 |
| $v_{\mathrm{th}}$ | 속도 임계값 (기본 0.5 m/s) |
| $a_{\mathrm{th}}$ | 정렬도 임계값 (기본 0.7) |
| $r_{\mathrm{th}}$ | 비율 임계값 (기본 0.5) |
| $\Delta t_{\mathrm{hold}}$ | 판정 조건 유지시간 임계값 (기본 3.0 s) |
| $t_{\mathrm{alarm}}$ | 경보 발생 시각 |
| $t_{e,\mathrm{start}}$ | 구역 $e$의 피난 개시 판정 시각 |
| $\mathrm{IDR}_{e,j}$ | 경보원 $j$ 기준 구역 $e$ IDR (m/s) |
| $\mathrm{IDR}_e$ | 구역 $e$ IDR — N-origin 평균 (m/s) |

---

## 6. 출력 (zone_metrics[])

```text
zone_id               : 구역 식별자
evacuation_start_at   : t_e,start (epoch)
response_delay_sec    : t_e,start − t_alarm  (null = 미개시)
graph_distance        : D(e) [m]  — N-origin 거리 평균
idr                   : IDR_e 평균 (null = 미개시)
idr_per_origin        : [IDR_e,1, IDR_e,2, …]  (N-origin 각각)
participant_ratio     : 판정 시점 r_e
status                : "started" | "not_started"
```

SessionLive에도 `zone_metrics[]` 포함 — 세션 진행 중 SSE로 실시간 전달.  
`alarm_origins[]` — 세션에 사용된 경보 발생원 목록.

---

## 7. 시각화

운영뷰 IDR 카드: 구역별 **타임라인 캔버스** (2026-07-15 구현)

```
구역A  |─경보────────────────────────────┤개시|  Δt 12.4s
구역B  |─경보━━━━━━━━━━━━━━━━━━━━━━━━━━━┄     |  판정 중 28s…
```

- 탐지 완료 구역: 경보→개시 구간 초록 채움, 양쪽 점선 마커, Δt 배지 → **이후 고정**
- 미탐지 구역: 경보→현재 회색 채움, 경과초 표시 → SSE마다 갱신
- 모든 구역 동일 시간축 (maxWindow 공유) → 상호 비교 가능

---

## 8. 구현 현황 (2026-07-15)

| 항목 | 상태 | 파일 |
|------|------|------|
| 다중 구역 polygon 정의·저장 | ✅ | `system/config/schema.py:Zone` |
| 구역별 객체 포함 판정 | ✅ | `system/metrics/session.py:point_in_polygon` |
| 속도·정렬도 계산 | ✅ | `system/metrics/engine.py:_obj_kinematics()` |
| 피난개시 판정 (v_e·a_e·r_e·dt_hold) | ✅ | `system/metrics/session.py:_sample()` |
| **N개 경보 발생원** 스키마·맵 도구 | ✅ | `schema.py:AlarmOrigin`, `view_map.js:alarm_origin 도구` |
| **격자 BFS 거리** (Option C) | ✅ | `system/spatial/grid.py:zone_grid_distance_m()` |
| **N-origin IDR 평균** 산출 | ✅ | `session.py:_zone_metric_now()` → `idr_per_origin[]` + `idr` avg |
| 격자 셀 크기 UI 입력·저장 | ✅ | `index.html:gridCellSize`, `view_map.js:save()` |
| 축척 2점 수평·수직 스냅 토글 | ✅ | `view_map.js:scaleSnapOn`, `index.html:scaleSnap` |
| SessionLive alarm_origins 전달 | ✅ | `contracts.py:SessionLive.alarm_origins` |
| 운영뷰 IDR 배지 per-origin 표시 | ✅ | `session.js:renderIdr()` |
| 경보원 설정 시 세션 즉시 시작 UX | ✅ | `session.js:onBtn()` |
| 구역별 타임라인 시각화 | ✅ | `session.js:drawIdrTimeline()` |
| **그리드 셀 대표벡터 field** | ⚠️ | polyline tangent 대체 중 (D-2) |
| CAD API 경로 자동입력 | ❌ | 미구현 (D-2 ②) |
