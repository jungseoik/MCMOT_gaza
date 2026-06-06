# 04 · 프론트엔드

`webui/index.html` 단일 파일(vanilla HTML/CSS/JS) + `webui/static/`(이식한 디자인 시스템).
React/빌드/번들러 없음.

## 디자인 시스템 이식

외부 시안(`sample_ui`, React 목업)에서 **CSS와 폰트만** 가져왔다(React는 안 씀).

- `static/colors_and_type.css` — 색/타입 토큰, Pretendard 폰트 `@font-face`
- `static/app.css` — 레이아웃·컴포넌트 클래스(`.app .rail .topbar .metric .bigcard .ppl .hud` 등)
- `static/fonts/*.ttf` — Pretendard (self-host, 오프라인 OK). Inter만 CDN(없으면 폴백)

`index.html`은 이 둘을 `<link>`로 로드하고, 시안의 클래스를 그대로 쓰는 마크업을 만든다.
아이콘은 시안의 React `<Icon>` 대신 **인라인 SVG**로 대체(의존 제거). 시안에 없던 것
(`.hidden`, 세팅 화면, 캔버스)만 `<style>`에 보충.

> 함정: `.hidden{display:none}`은 어느 CSS에도 없어 직접 정의해야 한다. 안 하면 세 화면이
> 동시에 보인다(06 참고).

## 레이아웃 — 오버플로 해결

시안의 앱 셸이 핵심:

```css
.app { display:grid; grid-template-columns:64px 1fr; grid-template-rows:56px 1fr; height:100vh; }
body { overflow:hidden; }
.dash { width:372px; flex:none; }          /* 대시보드 고정폭 */
.dash-body { overflow-y:auto; }            /* 대시보드 내부만 스크롤 */
.workspace, .stage { min-width:0; min-height:0; }  /* flex 자식 축소 허용 */
```

`100vh` 그리드 + `overflow:hidden` + 대시보드 고정폭 + `min-width:0` 덕분에 **좌/우가
한 화면에 들어가고 페이지가 가로로 삐져나가지 않는다**. (이전 자체 레이아웃은 일반
흐름에 flex+min-width를 써서 오버플로가 났다.)

## 3화면 상태기계

라우터 없이 `show(scr)`가 `.hidden`을 토글:

```
scrUpload (.empty)  →  scrSetup (.setup)  →  scrRun (.workspace + .dash)
```

레일+상단바는 항상 표시. 상단 소스칩/"Change source"는 업로드 화면에선 숨김.

### 화면 1 — 업로드
시안의 "Add a source" 드롭존. 클릭/드래그&드롭 → `POST /upload` →
`{job_id,width,height,first_frame}` 수신 → 세팅 화면으로.

### 화면 2 — ROI/보정 세팅
첫 프레임을 `<canvas>`에 그리고:
- **ROI 4점**: 클릭 4번 → 폴리곤(원본좌표로 환산해 전송)
- **보정선 2점 + 거리(m)**: `ppm = (픽셀길이/scale) / m`
- **지우기**, **측정 시작**(`POST /start/{id}` with `{roi, pixels_per_meter}`)

캔버스는 표시용으로 축소될 수 있어 `scale`(displayW/origW)을 들고, 클릭 좌표를
`/scale`로 **원본 픽셀 좌표**로 환산해 보낸다.

### 화면 3 — 분석
- **좌(영상)**: `<img id=view src=/stream/{id}>` (서버가 박스·속도·ROI를 baked).
  위에 HUD(LIVE, 소스명, 인원 칩, 해상도, fps)와 하단 트랜스포트바(진행률).
- **우(대시보드)**: `/status` 600ms 폴링 → `renderDash(metrics)`로 카드/Occupancy/
  Tracked people 갱신.

## 대시보드 렌더링

- 카드: People(누적 포함)/Density(+혼잡도 pill)/Avg speed(+peak, 스파크라인)/
  Acceleration/Avg dwell/Moving·정지
- **스파크라인**: `avg` 추이를 클라이언트 누적 배열로 인라인 SVG polyline
- **Occupancy 바**: `count` 추이를 `.gauge-row` 막대
- **Tracked people**: `objects[]`를 `.ppl` 행(P-ID·속도·체류)

## 완료 후 대시보드 동기 재생

`status==done`이면 폴링을 멈추고:
1. `/metrics_all/{id}`로 **프레임별 지표 배열**을 받음
2. 영상 스트림을 재접속(루프 0프레임부터)
3. 클라이언트 타이머가 `idx = floor((now-t0)*fps) % total`로 `replay_metrics[idx]`를
   렌더 → **영상과 같은 프레임 인덱스로 대시보드가 같이 흐름**

DB 불필요(인메모리). 한계: 영상(MJPEG)과 클라이언트 타이머가 각자 fps로 도므로 긴
루프에서 수백 ms 드리프트 가능(체감 일치). 프레임 정확 동기가 필요하면 WebSocket으로
프레임 인덱스를 함께 송신하는 구조가 필요.

## 알려진 장식/한계

- 하단 **트랜스포트바의 seek/일시정지는 동작 안 함**(MJPEG 구조). 진행률만 실제.
  진짜 시킹은 H.264 트랜스코딩 후 `<video>`가 필요.
