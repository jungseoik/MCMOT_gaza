# system/ 계약 명세 (M1 동결 — 2026-07-13 · v1.12)

> **v1.12 개정 (2026-08-21 — CBS 영역 형상·그룹 집계 / SEI 문별 폭·기준)**
> 요지: 4대 지표의 "사람이 넣는 값"을 실제 현장 형상에 맞춘다. CBS는 병목을
> **부채꼴**로 그리고 **묶어서** 보고, SEI는 **문마다** 유효폭·통과기준을 준다.
> - 스키마(`config/schema.py`):
>   · `AreaShape{kind,center,radius,radius_in,a0,sweep,segments}` 신설.
>     `Bottleneck.shape`가 sector면 **로드·저장마다 polygon을 재생성**한다
>     (파라미터가 진실, polygon은 계약상 정본 — 엔진은 polygon만 본다).
>     생성식은 `config/shapes.py:sector_polygon()` 한 곳.
>   · `Bottleneck.group: str` — CBS 집계 그룹 라벨(빈 문자열=미분류).
>   · `ExitLine.width_m` (유효폭 수동값 [m], None=도면 자동) ·
>     `ExitLine.q_design` (문별 [인/분/m], None=사이트 전역값).
>   · **`ExitLine.design_capacity`는 파생 필드로 격하** — `SiteConfig`
>     검증기(`_derive_exit_capacity`)가 `max(1, round(W_eff × q))`로 로드·저장마다
>     채운다. 폭을 못 구하면(축척 없음 + 수동폭 없음) 손대지 않는다.
>     기존 값과 충돌 없음(프론트가 쓰던 식과 동일 — 라이브 설정 재계산 결과 일치).
> - `contracts.py`:
>   · `BottleneckState.cbs: float|None` — 라이브 스냅샷의 병목별 CBS 진행값
>     (세션 없으면 None). 진행 중 선택 합산용.
>   · `BottleneckMetric.group` · **`BottleneckGroupMetric`** 신설
>     `{group, members, count, cbs_sum, cbs_mean, cbs_max, peak_density,
>     over_threshold_sec, risk_level}` → `EvaluationResult.bottleneck_groups[]`
>     (라벨 있는 병목만). 중요도는 이미 $w_k$에 반영돼 있어 재가중하지 않는다.
>   · `ExitMetric.width_m` · `width_manual` · `q_design` — C_j 근거 기록.
> - API: `POST /api/session/{id}/replay` body의 `exits:{id:{...}}`가
>   **`width_m`·`q_design`** 를 받는다(주면 C_j 재파생, `design_capacity`를 직접
>   주면 그 값이 최종). `thresholds.q_design` 변경도 C_j에 반영된다 — v1.12
>   이전에는 스냅샷 C_j가 그대로여서 q_design을 바꿔도 SEI가 안 변했다.
>   세션 CSV export에 `bottleneck_group` 행 추가.
> - **녹화 스키마 2 (버그 수정)**: `recorder.py` `tracks`에 **bbox 4열**
>   (`x1,y1,x2,y2`) 추가. 화면 **영역** 출입구(`ZoneGate`)는 발끝점이 문틀에
>   잘리는 것을 bbox 겹침으로 보정하는데, 리플레이가 bbox를 더미(0,0,0,0)로
>   재생해 그 보정이 죽어 있었다 — 16F 실측 라이브 19명 → 리플레이 6명.
>   bbox 없는 옛 녹화(schema 1)는 더미로 재생(하위호환).
>   회귀: `tests/system/test_replay.py::test_replay_camera_zone_exit_needs_bbox`.
> - 프론트: 맵 설정에 **[병목 부채꼴]** 도구(3클릭: 꼭짓점→반경·시작각→끝각,
>   목록에서 반경·각도 재조절) · 병목 행 **그룹** 입력 · 출입구 행 **W(m)·q**
>   인라인 입력(+↺ 도면 자동 복귀, C_j 실시간 표시). 운영뷰 CBS 카드에
>   **선택 체크박스 + 선택 집계 줄 + 그룹 프리셋 칩**, SEI 카드 출구 행에
>   `폭 3.57m(수동) × 6인/분/m` 근거 표시.
> - 프론트 추가(2026-09-02): CBS 병목 선택 패널을 공용 팩토리 **`CbsBnPanel`**
>   (`session.js` 정의·전역 노출)로 추출, **④ 리플레이 탭**에도 동일 패널 장착 —
>   개별 층·건물 훈련(재생 층 선택 연동) 재계산 결과의 병목별 CBS·초과초를 선택
>   집계(합계·평균·최악)로 보고, 재생 프레임(`_lite_frame.bottlenecks[].density`)을
>   스파크라인으로 쓴다. 서버 무개정 — 기존 리플레이 응답만 사용.
> - 프론트 추가(2026-09-03): 세션·건물 훈련 **결과 모달을 해석형 리포트로** — 종합평(총평 한
>   문장 + ✔ 잘된 점 / ⚠ 개선 지점), 지표별 "무엇을 재나" 설명 + 값의 자동 해석 문장(최악
>   병목/최대 쏠림 출구/최장 지연 구역 지목), 출구별 실제 vs 설계 분포 막대, 층별 상세.
>   등급(우수/보통/미흡 — 80/60, CBS 0.5/10)은 표시용 기준이며 계약·판정 기준이 아니다.
> - 하위호환: `shape`·`group`·`width_m`·`q_design` 모두 미지정 시 v1.11과 동일 동작.


> **v1.11 개정 (2026-08-18 — 건물 드릴: 전 층 공유 세션·4대지표 롤업·전 층 리플레이)**
> 설계: `docs/architecture/06-건물-드릴-세션-4대지표-롤업-설계.md`. 요지: 훈련 1건 =
> **전 층에 걸친 하나의 드릴**(공유 `t_alarm` → 전 층 동일 `session_id`). 층별 세션은
> 유지하고 그 위에 건물 오케스트레이션·롤업을 얹는다.
> - **시작 게이트(D2)**: **참여(카메라 매핑) 전 층에 경보 원점 ≥1개**가 있어야 시작.
>   미충족 시 `409 {msg, missing_floors:[…]}`. 원점은 **매 드릴마다 UI에서 새로 지정**.
> - **집계(§3)**: EPFI=전 층 전원 평균, CBS=전 층 병목 합, SEI=전 층 출구 통합분포
>   재계산, IDR=**구역별 유지**(건물 단일평균 없음). + 추가요약(총 통과·최대혼잡층·층별 개시).
> - 스키마(`contracts.py`) 신설: `DrillResult{session_id, alarm_ts, floors,
>   building{epfi_avg,cbs_total,sei,idr_by_floor}, summary{total_passed,max_cbs_floor,
>   floor_start_ts}, per_floor[{floor_id, result:EvaluationResult}]}`. 저장 스키마(층별
>   `EvaluationResult`)는 **무개정** — 같은 `session_id`를 전 층에서 모아 조립.
> - API 신설: **`POST /api/drill/start`**(body `{floor_origins:{floor:[[x,y]…]}, t_alarm?}`)
>   · **`POST /api/drill/stop`**(→`DrillResult`) · **`GET /api/drill/{id}/result`**(→`DrillResult`)
>   · **`GET /api/drills`**(이력 요약 — `alarm_ts`·`has_record`) ·
>   **`GET /api/drill/{id}/export?format=json|csv`** ·
>   **`POST /api/drill/{id}/replay`**(전 층 `.db`를 같은 오버라이드로 리플레이·재산출 →
>   `{drill:DrillResult, frames_by_floor, site_by_floor}`. 원본 저장물 불변).
> - 프론트: 운영 뷰 "🔔 건물 전체 경보 시작"(전 층 원점 게이트·층별 현황 칩·롤업 리포트),
>   **④ 리플레이 탭 "건물 드릴" 모드**(이력·건물지표·층 선택 2D 재생·임계값 재계산·리포트).
> - 하위호환: 단일 층 사이트는 참여 1개 층으로 기존과 동일. 층별 `POST /api/session/*` 유지.

> **v1.10 개정 (2026-08-04 — 세션 녹화·리플레이·지표 재계산)**
> 설계: `docs/architecture/05-세션-녹화-리플레이-지표재계산-설계.md`. 요지: 경보 세션의
> 입력 트랙을 세션별 SQLite로 녹화해두고, 결정적 리플레이로 **다른 임계값(4대 지표
> 세팅값)에 대해 지표를 재산출**한다(도면·호모그래피 동일 전제 — '역방향 재파라미터화').
> - **녹화층**: `system/metrics/recorder.py`(`SessionRecorder`). 세션 시작 시
>   `data/sites/<site>/sessions/[<floor>/]<session_id>.db` 생성 — `tracks`(프레임별
>   raw 입력: call_seq·ts·cam_id·local_id·u·v·conf, **min_conf 필터 이전**) + `meta`
>   (그 층 공간요소 SiteConfig 뷰·카메라·경보원·alarm_ts 스냅샷). 엔진 `on_tracks`가
>   투영 이전에 raw 트랙을 기록(예외 격리 — 녹화 실패가 라이브를 죽이지 않음).
>   env `SESSION_RECORD`(기본 on, `0`이면 끔=기존과 동일·롤백).
> - **재계산**: `system/metrics/replay.py`(`run_replay`) — 녹화 db를 헤드리스
>   MetricsEngine에 call_seq 순서로 재생. 기존 집계 `<session_id>.json`은 **불변**.
> - API 신설: **`POST /api/session/{session_id}/replay?floor=`** — body(모두 선택)
>   `{thresholds:{v_th,a_th,r_th,dt_hold,d_allow,min_conf,q_design}, rho_crit(전역 병목
>   임계), bottlenecks:{id:{rho_crit,weight}}, exits:{id:{design_capacity}}, fps}`.
>   resp `{result, timeline, frames(2D 재생용 경량 MapState), site(세션 당시 공간요소),
>   meta}`. 미지정 임계값은 세션 스냅샷 값 유지.
> - API 개정: `GET /api/sessions`·`GET /api/sessions/{id}`에 `has_record`(bool) 추가.
> - 스키마(`contracts.py`) 무개정. `EvaluationResult` 등 기존 계약 그대로.
> - 결정성 보증: 같은 임계값 재계산 = 원본 result와 `generated_at` 제외 완전 일치
>   (`tests/system/test_replay.py`).
> - 프론트: **④ 리플레이 탭**(`webui/static/main/view_replay.js`) — 세션 이력 선택 →
>   2D 재생(재생/일시정지·배속·시크) + 임계값 조정 → 재계산.

> **v1.9 개정 (2026-07-30 — CAD 도면 적용 시 공간요소 재세팅, D-2)**
> - API 신설: **`PUT /api/site/floor-elements?floor=`** — 한 층의 공간요소를 새 CAD
>   도면 기준으로 재세팅하는 통합 부분 반영 엔드포인트(다층 붕괴 위험 없음).
>   새 CAD 도면으로 맵이 바뀌면 옛 공간요소 좌표(옛 맵 px 기준)가 새 맵과 안 맞으므로,
>   편집기가 아는 것(피난경로·출입구)만 CAD 기준으로 새로 세팅하고, 모르는 것
>   (구역·병목)은 옛것을 남기지 않고 비워 새 맵 위에 다시 그리게 한다.
>   body(모두 선택, 키가 있을 때만 반영) `{routes?, replace?:"auto"|"all",
>   exits?, clear_zones?:bool, clear_bottlenecks?:bool}`:
>   `routes`는 `PUT /api/site/routes`와 동일 규칙(auto=자동경로만 교체·수동 보존, all=전체),
>   `exits`는 그 층 exits **전체 교체**(CAD 기준 재세팅),
>   `clear_zones`/`clear_bottlenecks`가 true면 그 층 zones/bottlenecks를 빈 리스트로.
>   그 층(floor)만 수정하고 `PUT /api/site`로 floors 통째 전송하지 않는다. reload_engine.
>   CAD 편집기(:8910) apply가 맵 POST 성공 시 이 엔드포인트로 routes+exits(clear=true)를
>   함께 전송. exits inside(안쪽 반평면)는 Exit 중점→도면 bounds 중심 방향으로 추론.
> - 스키마·`contracts.py` 무개정(기존 `Route`·`ExitLine` 재사용).

> **v1.8 개정 (2026-07-28 — CAD 최단경로→피난경로 자동 반영, D-2 ①)**
> - API 신설: **`PUT /api/site/routes?floor=`** — 한 층의 `routes`만 교체하는
>   부분 반영 엔드포인트(다층 붕괴 위험 없음). CAD 도면 편집기(:8910)의 apply가
>   worst-N 최단경로를 맵 원본 px polyline으로 변환해 EPFI 기준경로로 반영한다.
>   자동경로 id 접두 `auto-evac-`; `replace="auto"`(기본)는 자동경로만 교체하고
>   사용자가 손으로 그린 route는 보존. 맵 mm→px 변환은 편집기 렌더 좌표계와
>   1:1(선형, y축 뒤집힘) — 방금 올린 맵이라 :8900 층 map.w/h 와 정합.
> - 스키마·`contracts.py` 무개정(기존 `Route` 재사용).

> **v1.7 개정 (2026-07-22 — 다중 도면(N개 층) 지원, D-11)**
> 하위호환 원칙: 층 미지정·기존 단일도면 사이트는 "default" 층 1개로 그대로 동작.
> - 스키마(`schema.py`): `Floor(BaseModel)` 신설 — `id·name·map·routes·zones·bottlenecks·exits·graph·alarm_origins·grid`(공간요소, 타입은 SiteConfig 동명 필드와 동일). `SiteConfig.floors: list[Floor]=[]` 추가(정본). **기존 top-level map/routes/... 필드는 유지**하되, `@model_validator`가 floors 비었을 때 top-level을 `Floor(id="default", name="기본")` 하나로 승격 → 로드 후 항상 floors≥1. `CameraConfig.floor_id: str|None=None`(None=default 층). 헬퍼: `get_floor()·floor_id_of_camera()·as_floor_view()`(한 층의 공간요소를 top-level에 실은 SiteConfig 뷰 — 엔진/세션 내부 로직 무변경으로 소비).
> - store(`store.py`): `map_path(site_id, floor_id="default")` — default=`map.png`(기존 경로 유지), 그 외=`map_<floor_id>.png`.
> - API: 아래 엔드포인트에 **`?floor=` 쿼리(기본 "default")** 추가 — `GET/POST /api/site/map`, `GET /api/map/state`, `GET /api/map/stream`, `POST /api/session/start`, `POST /api/session/stop`, `GET /api/session[/result|/timeline|/person_series]`, `GET /api/sessions[/{id}]`, `GET /api/session/export`, `GET /api/debug/tracks`. 층마다 `MapState`를 하나씩 반환(계약 `contracts.py`는 **무개정** — MapState에 floor_id 미도입). `PUT /api/cameras/{id}/mapping` body에 **`floor_id`** 추가(그 층 맵 px 기준 H 산출·소속 지정). **층 CRUD**: `GET /api/floors`·`POST /api/floors`·`DELETE /api/floors/{id}`.
> - 서버 런타임: `Runtime.engine`(단일) → `Runtime.engines: dict[floor_id→MetricsEngine]`. 층마다 그 층 카메라만 받는 엔진 1개. 세션 저장 디렉토리 `sessions/<floor_id>/`(default는 `sessions/` 유지).

> **v1.4 개정 (2026-07-14 — EPFI 근거 시계열·설정 스냅샷 표시)**
> - `SessionLive.config_version` 추가 — 세션이 고정한 설정 버전(사이트 버전과 다르면 UI가 "다음 세션부터 적용" 경고)
> - 객체별 `d_i(t)` 1초 시계열: 엔진 `session_person_series()` · `GET /api/session/person_series` ·
>   세션 저장 JSON에 `person_series` 포함 — EPFI 지연 표출·역추적(요구사항 FR-05 보강)
> - `PUT mapping`의 valid_roi 전체 교체 의미 명문화(v1.3) · 세션 이력 API(v1.3)

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
| `PUT /api/site/routes?floor=` | `{routes: [{id, name, points:[[px,px]...]}], replace?: "auto"\|"all"}` | `{floor, routes, auto, manual}` — **그 층 routes만 교체**(다층 붕괴 없음). `replace="auto"`(기본): id가 `auto-evac-`로 시작하는 자동경로만 교체·수동경로 보존. `"all"`: 전체 교체. CAD 편집기(:8910)의 worst-N 최단경로를 EPFI 기준경로로 자동 반영 (D-2 ①, v1.8) |
| `PUT /api/site/floor-elements?floor=` | `{routes?, replace?:"auto"\|"all", exits?:[{id,name,line:[[px,px],[px,px]],inside:[px,px],design_capacity?}], clear_zones?:bool, clear_bottlenecks?:bool}` | `{floor, routes, exits, zones, bottlenecks, auto_routes}` — **그 층 공간요소만 재세팅**(다층 붕괴 없음). CAD 도면 적용 시: `routes`(routes와 동일 규칙)·`exits`(전체 교체)를 CAD 기준으로 새로 세팅, `clear_zones`/`clear_bottlenecks`=true면 그 층 zones/bottlenecks를 비움(옛 맵 px 좌표가 새 맵과 불일치하므로 다시 그리게). 키 없는 요소는 무변경 (D-2, v1.9) |
| `GET /api/cameras` | — | `list[CameraConfig]` + 런타임 상태 병합 `[{...cfg, state: CameraState}]` |
| `POST /api/cameras` | `{name, rtsp, analyze_fps?, min_conf?}` | `CameraConfig` (cam_id 서버 발급 `cam01..`) |
| `PUT /api/cameras/{id}` | `CameraConfig` 부분 갱신(enabled·min_conf 등). `min_conf`: 0~1이면 그 카메라 오버라이드, `null`이면 사이트값 상속. 범위 밖은 422 | `CameraConfig` |
| `DELETE /api/cameras/{id}` | — | `{"ok": true}` |
| `POST /api/cameras/{id}/test` | — | `{ok, width, height, snapshot_b64}` (첫 프레임. **snapshot_b64는 `data:image/jpeg;base64,…` data URL 형식** — v1.1 명문화) |
| `GET /api/cameras/{id}/snapshot` | — | image/jpeg 1장 (온디맨드, 스트림 아님) |
| `PUT /api/cameras/{id}/mapping` | `{cctv_pts: [[u,v]×4+], map_pts: [[x,y]×4+], valid_roi: [[u,v]...] \| null, floor_id?: str}` | `CameraConfig` — **H는 서버가 cv2.findHomography로 산출·저장**. valid_roi는 **요청 값으로 전체 교체**(null/3점 미만=제거 — v1.3). `floor_id` 지정 시 카메라 소속 층 저장(v1.7, map_pts는 그 층 맵 px) |
| `GET /api/floors` | — | 층 목록 요약 `[{id, name, has_map, map, camera_count}]` (v1.7) |
| `POST /api/floors` | `{id?: str, name?: str}` | 추가된 층 요약. id 생략 시 서버 발급(`floor2..`), id 중복 409 (v1.7) |
| `DELETE /api/floors/{id}` | — | `{"ok": true}`. `default` 삭제 422, 최소 1개 층 보장. 소속 카메라는 default로 재배정 (v1.7) |
| `POST /api/session/start` | `{origin: [x,y], t_alarm?: float}` (t_alarm 생략 시 now) | `SessionLive`. 시작 시 카운터·debounce reset. 진행 중이면 409 |
| `POST /api/session/stop` | — | `EvaluationResult` (최종 산출·보존) |
| `GET /api/session` | — | `SessionLive` (없으면 404) |
| `GET /api/session/result` | — | 마지막 `EvaluationResult` (없으면 404) |
| `GET /api/session/timeline` | — | `list[TimelinePoint]` (1초 샘플 — 시간대별 시각화) |
| `GET /api/session/person_series?floor=` | — | 객체별 `d_i(t)` 1초 시계열 `{gid: [...]}` (진행 중이면 현재까지, 종료 후엔 마지막/저장본) — EPFI 지연 표출·역추적 (v1.4) |
| `GET /api/session/export?format=json\|csv` | — | EvaluationResult 파일 다운로드 (FR-09) |
| `GET /api/sessions` | — | 세션 이력 요약 목록(최신순) — v1.3, `data/sites/<site>/sessions/*.json` 영속화 기반 |
| `GET /api/sessions/{id}` | — | 저장된 `{result, timeline}` (v1.3). result/timeline 단건 API는 재시작 후 최신 저장본 폴백 |
| `GET /api/map/state` | — | `MapState` (최신 스냅샷, 세션 중 `.session=SessionLive`) |
| `GET /api/map/stream` | — | **SSE**, 1초 간격 `event: state\ndata: <MapState JSON>` |
| `GET /api/status` | — | `{pipeline: {...}, cameras: list[CameraState]}` |
| `GET /api/debug/tracks?floor=` | — | 트래커 진단 — gid별 `foot_uv`·맵 투영 좌표·카메라 해상도·커버리지(좌표가 카메라 원본 해상도 범위 안인지 확인용) |

## 5. MapState (B→C)

스키마·예시는 `contracts.py`의 `MapState`·`mapstate_example()` 참조. SSE 주기 1초(설정 가능). 프론트는 이 JSON만으로 운영 뷰 canvas를 전부 그린다 — 카메라 영상 표출 없음.

## 6. 트랙별 소유 경계 (재확인)

| 트랙 | 소유 | 금지 |
|------|------|------|
| A backend-pipeline | `system/ingest`, `system/tracking`, tracker 전역상태 리팩토링 | spatial/metrics/config 수정, webui/ |
| B backend-domain | `system/config`(스키마 유지보수), `system/spatial`, `system/metrics`, `tests/system/` | ingest/tracking, webui/ |
| C frontend-map-ui | `webui/static/main/`, `system/api/mock_server.py` | system/그 외, webui/server.py, 기존 index.html 화면 |
| 메인 세션 | `system/api/`(실서버), `webui/server.py`·`index.html` 통합, 계약 중재 | — |
