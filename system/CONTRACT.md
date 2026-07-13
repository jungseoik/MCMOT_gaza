# system/ 계약 명세 (M1 동결 — 2026-07-13 · v1.2 — 4대 지표 세션 계층)

> **v1.2 개정 (2026-07-13, P1 착수)**
> - 스키마: `SiteConfig.graph`(SpatialGraph — IDR 공간그래프, 수동 정의), `Zone.node_id`
> - contracts: `EvaluationResult`·`ZoneMetric`·`PersonMetric`·`BottleneckMetric`·`ExitMetric`·`TimelinePoint`·`SessionLive`, `MapState.session`
> - API: `/api/session/*` 6종 (아래 표)
> - 엔진 인터페이스(B 소유): `MetricsEngine.start_session(origin_xy, t_alarm) -> SessionLive` ·
>   `stop_session() -> EvaluationResult` · `session_live() -> SessionLive|None` ·
>   `session_result() -> EvaluationResult|None` · `session_timeline() -> list[TimelinePoint]`.
>   start 시 내부 reset(v1.1 예약분 이행). 지표 수식·예외는 요구사항 문서 §2 그대로
>   (SEI insufficient_data → sei=None 등).

> **v1.1 개정 이력 (2026-07-13, 3트랙 완료 후 재동결)**
> 1. `MapObject.in_bounds: bool = True` 추가 — 맵 경계 밖 투영 표시 (트랙 B 제안)
> 2. `MetricsEngine.reset()` 예약 — 4대 지표 평가 세션 경계용, 후속 단계 구현 (트랙 B 제안)
> 3. `GET /api/site/map` 추가 — 맵 이미지 서빙 (트랙 C 제안)
> 4. `MapSpec` 축척 미지정 상태 허용 — resolve_m_per_px()→None, 실단위 지표 None 산출, 운영 전 축척은 UI 강제 (트랙 C 제안)
> 5. 운영 제약: **AnalyzerThread와 기존 webui PoC는 같은 프로세스 동시 구동 금지**(전역 GeneralSettings 충돌) → 실서버는 별도 프로세스 (트랙 A 보고)

> 세 트랙(A: ingest/tracking · B: config/spatial/metrics · C: frontend/mock)의 공통 계약.
> **원천은 코드**: [`system/config/schema.py`](config/schema.py) · [`system/contracts.py`](contracts.py) · [`system/config/store.py`](config/store.py).
> 변경이 필요하면 코드를 직접 고치지 말고 담당 트랙이 결과 보고에 "계약 변경 제안"으로 명시 → 메인 세션이 조정·재동결.

## 1. 좌표·시간 규약

| 항목 | 규약 |
|------|------|
| 공간 요소(경로·구역·병목·출입구) | **맵 원본 px** 저장·전송. canvas 표시 배율과 분리 |
| 카메라 측(valid_roi, cctv_pts) | 카메라 프레임 px |
| 실단위 환산 | `MapSpec.resolve_m_per_px()` 하나로만 — spatial 층 단일 지점 |
| 시간 | wall-clock epoch 초(float). 프레임 인덱스 가정 금지 |
| 객체 표시 키 | `gid = "{cam_id}:{local_track_id}"` (글로벌 ID 병합 전, D-1) |

## 2. 런타임 인터페이스 (A→B)

- `FrameItem{cam_id, ts, frame(BGR ndarray), seq}` — ingest → tracking
- `TrackedObject{cam_id, local_track_id, foot_uv, bbox_xyxy, conf, ts}` — tracking → spatial/metrics.
  **맵 투영 전 카메라 px** — 호모그래피 적용은 spatial 소유.
- tracking은 프레임당 `list[TrackedObject]`를 콜백(`on_tracks(cam_id, ts, tracks)`)으로 전달.

## 3. 설정 파일 (B 소유, 전 트랙 읽기)

`data/sites/<site_id>/site.json` + `cameras/<cam_id>.json` + `map.png` — 스키마는 `schema.py`, 입출력은 `SiteStore`. 저장 시 version 자동 +1, 원자적 쓰기.

## 4. REST API (C가 mock으로 선행 구현, 통합 시 실서버 대체)

베이스: FastAPI. 에러는 `{"detail": str}` + 4xx/5xx.

| 메서드·경로 | 요청 | 응답 |
|-------------|------|------|
| `GET /api/site` | — | `SiteConfig` (없으면 404) |
| `PUT /api/site` | `SiteConfig`(routes/zones/bottlenecks/exits/thresholds 갱신) | 저장된 `SiteConfig`(version+1) |
| `POST /api/site/map` | multipart: `image`(png), 선택 `meta`(cad-convert JSON) | `MapSpec` (meta 있으면 m_per_px 자동) |
| `GET /api/site/map` | — | image/png (업로드된 맵 이미지, 없으면 404) |
| `GET /api/cameras` | — | `list[CameraConfig]` + 런타임 상태 병합 `[{...cfg, state: CameraState}]` |
| `POST /api/cameras` | `{name, rtsp, analyze_fps?}` | `CameraConfig` (cam_id 서버 발급 `cam01..`) |
| `PUT /api/cameras/{id}` | `CameraConfig` 부분 갱신(enabled 토글 포함) | `CameraConfig` |
| `DELETE /api/cameras/{id}` | — | `{"ok": true}` |
| `POST /api/cameras/{id}/test` | — | `{ok, width, height, snapshot_b64}` (첫 프레임. **snapshot_b64는 `data:image/jpeg;base64,…` data URL 형식** — v1.1 명문화) |
| `GET /api/cameras/{id}/snapshot` | — | image/jpeg 1장 (온디맨드, 스트림 아님) |
| `PUT /api/cameras/{id}/mapping` | `{cctv_pts: [[u,v]×4+], map_pts: [[x,y]×4+], valid_roi: [[u,v]...] \| null}` | `CameraConfig` — **H는 서버가 cv2.findHomography로 산출·저장**. valid_roi는 **요청 값으로 전체 교체**(null/3점 미만=제거 — v1.3 명확화) |
| `POST /api/session/start` | `{origin: [x,y], t_alarm?: float}` (t_alarm 생략 시 now) | `SessionLive`. 시작 시 카운터·debounce reset. 진행 중이면 409 |
| `POST /api/session/stop` | — | `EvaluationResult` (최종 산출·보존) |
| `GET /api/session` | — | `SessionLive` (없으면 404) |
| `GET /api/session/result` | — | 마지막 `EvaluationResult` (없으면 404) |
| `GET /api/session/timeline` | — | `list[TimelinePoint]` (1초 샘플 — 시간대별 시각화) |
| `GET /api/session/export?format=json\|csv` | — | EvaluationResult 파일 다운로드 (FR-09) |
| `GET /api/sessions` | — | 세션 이력 요약 목록(최신순) — v1.3, `data/sites/<site>/sessions/*.json` 영속화 기반 |
| `GET /api/sessions/{id}` | — | 저장된 `{result, timeline}` (v1.3). result/timeline 단건 API는 재시작 후 최신 저장본 폴백 |
| `GET /api/map/state` | — | `MapState` (최신 스냅샷, 세션 중 `.session=SessionLive`) |
| `GET /api/map/stream` | — | **SSE**, 1초 간격 `event: state\ndata: <MapState JSON>` |
| `GET /api/status` | — | `{pipeline: {...}, cameras: list[CameraState]}` |

## 5. MapState (B→C)

스키마·예시는 `contracts.py`의 `MapState`·`mapstate_example()` 참조. SSE 주기 1초(설정 가능). 프론트는 이 JSON만으로 운영 뷰 canvas를 전부 그린다 — 카메라 영상 표출 없음.

## 6. 트랙별 소유 경계 (재확인)

| 트랙 | 소유 | 금지 |
|------|------|------|
| A backend-pipeline | `system/ingest`, `system/tracking`, tracker 전역상태 리팩토링 | spatial/metrics/config 수정, webui/ |
| B backend-domain | `system/config`(스키마 유지보수), `system/spatial`, `system/metrics`, `tests/system/` | ingest/tracking, webui/ |
| C frontend-map-ui | `webui/static/main/`, `system/api/mock_server.py` | system/그 외, webui/server.py, 기존 index.html 화면 |
| 메인 세션 | `system/api/`(실서버), `webui/server.py`·`index.html` 통합, 계약 중재 | — |
