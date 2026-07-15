# 15. 운영뷰 객체 위치 클라이언트 사이드 보간 렌더링

> 작성일: 2026-07-14  
> 대상 파일: `webui/static/main/view_live.js`  
> 관련 화면: 화면 3 — 운영뷰 (2D 맵)

---

## 1. 배경 및 문제

운영뷰의 2D 맵은 서버에서 SSE(`/api/map/stream`)로 전송되는 `state` 이벤트를 수신할 때마다 캔버스를 재렌더링한다. SSE 전송 주기는 트래킹 파이프라인 처리 속도에 따라 약 1fps 수준으로, 화면에 나타나는 객체 점들이 뚝뚝 끊기는 현상이 발생한다.

**한계**
- SSE 주기 ≈ 1초 → 렌더링 fps ≈ 1
- 객체 위치가 순간이동처럼 점프 → 시각적 불량
- 서버 처리 속도를 올리면 GPU 부하 증가 — 비효율

---

## 2. 해결 방법: 클라이언트 사이드 선형 보간

SSE 수신 빈도는 그대로 유지하되, 브라우저 측에서 **requestAnimationFrame(RAF) 루프**를 독립적으로 돌리고, 직전 위치(T_n-1)와 현재 위치(T_n) 사이를 선형 보간(Lerp)하여 부드러운 이동을 구현한다.

```
SSE  ─── T_n-1 ─────────────────── T_n ─────────────────── T_{n+1}
                    ↑ α=0.3       ↑ α=0.7       ↑ α=1.0
RAF               렌더           렌더           렌더(스냅)
```

### 2.1 핵심 수식

```
α = clamp((now − T_n 도착시각) / 측정된 SSE 간격, 0, 1)

display_pos = T_{n-1}_pos + α × (T_n_pos − T_{n-1}_pos)
```

- α = 0 : T_n 막 도착, 이전 위치에서 출발
- α = 1 : 다음 SSE 도착 직전, 현재 위치에 도달
- SSE 간격은 수신 시마다 실측값으로 갱신 (`interpDuration`)

### 2.2 보간 vs 외삽 선택 이유

| 항목 | 보간 (채택) | 외삽 |
|------|------------|------|
| 방식 | T_n-1 → T_n 사이를 렌더 | T_n 이후를 예측 |
| 딜레이 | 1 SSE 주기 (≈1초) | 없음 |
| 안정성 | 항상 실측 범위 내 | 방향 전환 시 위치 튐 |
| 복잡도 | 단순 | 속도·가속도 추정 필요 |

피난 모니터링은 살짝 딜레이가 있어도 무방하므로 보간을 채택한다.

---

## 3. 구현 세부

### 3.1 추가된 상태 변수

```javascript
let prevObjects = {};      // {gid: {x,y}} — T_n-1 위치
let currObjects = {};      // {gid: {x,y}} — T_n 위치 (최신 SSE)
let interpStart = 0;       // T_n 도착 시각 (performance.now())
let interpDuration = 1000; // 측정된 SSE 간격 (ms)
let lastSseTime = 0;       // 이전 SSE 도착 시각
let rafId = null;          // requestAnimationFrame handle
const FPS_OPTS = [10, 20, 30];
let fpsCursor = 1;         // 기본 20fps
```

### 3.2 SSE 수신 시 처리 흐름

```javascript
// SSE 도착
const now = performance.now();
interpDuration = Math.max(200, Math.min(3000, now - lastSseTime));  // 실측 간격
lastSseTime = now;
interpStart = now;                    // 보간 α=0 리셋

prevObjects = {...currObjects};       // T_n-1 ← T_n
currObjects = {};                     // T_n ← 새 수신
newState.objects.forEach(o => currObjects[o.gid] = {x:o.x, y:o.y});

state = newState;
// 대시보드·패널은 T_n 즉시 업데이트
updatePanels(); renderCams(); renderObjects();
// ★ mc.render()는 RAF 루프가 담당
```

### 3.3 보간 렌더링 (overlay)

```javascript
const alpha = Math.min(1, (performance.now() - interpStart) / interpDuration);
state.objects.forEach((o) => {
  const prev = prevObjects[o.gid];
  const rx = prev ? prev.x + (o.x - prev.x) * alpha : o.x;  // 신규 객체는 현재 위치
  const ry = prev ? prev.y + (o.y - prev.y) * alpha : o.y;
  // TX(rx), TY(ry) 로 캔버스 렌더
});
```

### 3.4 RAF 렌더 루프

```javascript
function startRenderLoop() {
  let lastFrame = 0;
  function loop(ts) {
    if (!active) { rafId = null; return; }
    rafId = requestAnimationFrame(loop);
    if (ts - lastFrame < 1000 / renderFps()) return;  // 목표 fps 프레임 스킵
    lastFrame = ts;
    if (mc) mc.render();
  }
  rafId = requestAnimationFrame(loop);
}
```

`requestAnimationFrame`은 탭 비활성화 시 자동 일시정지 → 불필요한 CPU 사용 없음.

---

## 4. 대시보드 싱크 정책

| 요소 | 업데이트 시점 | 근거 |
|------|-------------|------|
| 2D 맵 객체 위치 | RAF 매 프레임 (보간) | 시각적 부드러움 |
| 구역·병목·출입구 카운트 | SSE T_n 즉시 | 지표값은 서버 계산 기준이 정답 |
| 세션 카드 (CBS·EPFI 등) | SSE T_n 즉시 | 동일 |
| 카메라 상태 | SSE T_n 즉시 | 동일 |

위치 시각화와 지표 수치 사이 최대 디싱크 = SSE 1주기(≈1초). 허용 범위.

---

## 5. FPS 설정

툴바 [20fps] 버튼 클릭으로 10 → 20 → 30fps 순환 전환.

| fps | 프레임 간격 | 용도 |
|-----|-----------|------|
| 10fps | 100ms | 저사양 환경·배터리 절약 |
| 20fps | 50ms | 기본값, 보간 부드러움과 부하 균형 |
| 30fps | 33ms | 고사양 환경·더 부드러운 표시 |

SSE 간격(≈1초)보다 렌더 fps가 높아도 α가 이미 1.0에 클램프되어 추가 계산 부담 없음.

---

## 6. 리소스 영향

- **CPU**: Canvas에 점 수십 개 lerp + drawCircle — 무시 가능 수준
- **GPU/브라우저**: `requestAnimationFrame`이 브라우저 합성 파이프라인과 동기화 — 효율적
- **서버**: 변경 없음. SSE 전송 주기 동일
- **메모리**: `prevObjects` / `currObjects` — 객체 수 × 2 × `{x,y}` 오브젝트. 수십 객체 기준 수 KB 이하

---

## 7. 한계 및 향후 개선

- **직선 보간만 지원**: 곡선 경로(예: 회전 구간)는 살짝 안쪽 단축. 실용상 문제 없음
- **SSE 유실 시**: `interpDuration`이 길어져도 α=1 클램프로 마지막 위치 유지 — 안전
- **객체 소멸 시**: `state.objects`에서 사라지면 즉시 렌더 중단 (1프레임 내)
- **향후**: 속도 벡터(vx, vy)를 이용한 물리 기반 보간(외삽+복원)으로 업그레이드 가능 — 현재 수준에서 불필요
