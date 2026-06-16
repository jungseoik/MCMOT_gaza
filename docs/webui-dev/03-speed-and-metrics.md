# 03 · 속도 추정 & 대시보드 지표

`webui/speed.py` — `SpeedEstimator` 클래스 + `annotate()`.
**슬라이딩 윈도우 + 실세계 km/h 보정 + 지표 확장**으로 px/s 기반 속도를 추정한다.

## 입력/출력

- 입력: 프레임마다 `targets` (ndarray `N×≥5`, `[x1,y1,x2,y2,id,...]`)
- 출력: `update()` → `{id: speed}` (ROI 내 객체), `metrics(present)` → 대시보드 dict

## 생성자

```python
SpeedEstimator(fps, pixels_per_meter=None, homography=None, roi=None,
               frame_size=None, world_area_m2=None, reference_vec=None,
               window_sec=1.0, min_move_px=2.0)
```

- `pixels_per_meter`(ppm) **또는** `homography` 중 하나라도 있으면 **km/h**, 둘 다 없으면
  **px/s** (`unit` 프로퍼티 = `metric = (H is not None) or (ppm is not None)`로 결정)
- `homography`: 이미지 발끝점 → 지면 미터 변환(원근보정, ROI 실측/Depth 모드)
- `roi`: 4점 폴리곤(원본 px 좌표). 없으면 전체 프레임
- `world_area_m2`: 호모그래피 모드에서 ROI의 실제 면적(밀도 명/m² 산출용)
- `reference_vec`: (선택) 권장 피난 방향 `[[tx,ty],[hx,hy]]`(이미지 px, 꼬리→머리). 주면
  **방향성 정렬도**를 계산(opt-in, 안 주면 기존 동작 그대로) → [13-alignment](13-alignment.md)

> ⚠️ 세 번째 위치인자는 `roi`가 아니라 **`homography`**다. 서버는 혼동을 막으려고
> `SpeedEstimator(job.fps, pixels_per_meter=…, homography=…, roi=…, …)`처럼 전부 키워드
> 인자로 호출한다(`webui/server.py`).
- `window_sec`: 속도 산출 슬라이딩 윈도우(기본 1초 = `round(fps*1)` 프레임)
- `min_move_px`: 이보다 작은 이동은 노이즈로 보고 0 처리

## 속도 계산 (슬라이딩 윈도우)

객체별로 **발끝점**(bbox bottom-center — 지면에 닿는 점)의 시간 큐 `deque[(t, fx, fy)]`를
유지하고(시간 `t`는 **초**), `window_sec`보다 오래된 샘플은 버린 뒤 큐의 처음↔끝으로 산출:

```
dt   = t_last - t_first                  # 윈도우의 실제 경과 시간(초)
# km/h (ppm 또는 homography 모드): 발끝점을 미터로 변환 후 거리
dist_m = ||world(foot_last) - world(foot_first)||   # homography 또는 dist_px/ppm
speed  = (dist_m / dt) * 3.6                         # m/s → km/h
# px/s (둘 다 없음): 픽셀 거리 그대로
speed  = ||foot_last - foot_first|| / dt
```

> `t`는 벽시계 초다. 파일 모드는 `frame_idx/fps`를, RTSP는 `time.monotonic()`을 넘긴다
> (프레임 스킵이 불균일해도 dt가 정확 → [08](08-speed-and-calibration.md)).
> 원형의 "3초마다 시작점 리셋" 배치 대신 매 프레임 윈도우로 산출해 대시보드가 부드럽다.

## ROI 필터

`cv2.pointPolygonTest(roi, foot) >= 0`인 객체(발끝점 기준)만 측정. ROI를 벗어나면 그 객체의
이력(속도/체류/가속 상태)을 즉시 폐기(`_forget`). ROI 없으면 전체 객체.

## 거리 보정 (km/h)

ppm(미터당 픽셀)은 프론트의 **보정선 2점 + 실제거리(m)** 로 계산:
`ppm = (보정선 픽셀길이[원본해상도]) / 미터`. 서버는 이 값을 받아 위 공식에 사용.
보정을 안 하면 `unit="px/s"`로 폴백.

## 지표 (`metrics(present)` 반환 키)

| 키 | 의미 | 산식 |
|----|------|------|
| `unit` | 속도 단위 | ppm **또는** homography 있으면 `km/h`, 둘 다 없으면 `px/s` |
| `count` | 현재 인원 | ROI 내 present 수 |
| `cumulative` | 누적 인원 | 지금까지 본 고유 ID 수(`seen_ids`) |
| `avg` / `max` | 평균 / 최고 속도 | present 속도들 |
| `accel` | 평균 가속도 크기 | 객체별 \|Δspeed\|/Δt 의 평균 |
| `moving` / `stationary` | 이동 / 정지 수 | 속도 > `move_thresh`(km/h:0.5, px/s:3) |
| `moving_ratio` | 이동 비율 % | moving/count×100 |
| `density` + `density_unit` | 밀도 | count ÷ 면적. 보정 시 `명/m²`(면적=ROI/전체 px → m²), 미보정 시 `명/Mpx` |
| `level` / `level_kr` | 혼잡도 | 명/m² 기준 <0.4 Low/여유, <1.0 Normal/보통, ≥1.0 High/혼잡 |
| `avg_dwell` / `max_dwell` | 체류시간(s) | `(현재프레임 - 첫등장프레임)/fps` |
| `has_align` | 정렬도 활성 | `reference_vec` 줬을 때만 true (UI 표출 게이트) |
| `avg_align` | 평균 정렬도 | 이동 객체의 코사인 평균(정지 제외), 없으면 `null` |
| `ref_dir` | 기준 단위벡터 | 맵 기준 화살표용 `[dx,dy]`(객체와 같은 좌표계), 없으면 `null` |
| `objects[]` | 객체별 | `{id, speed, dwell, mx, my, dirx, diry, align}` (속도 내림차순) |

면적 산출: ROI 있으면 `cv2.contourArea(roi)`, 없으면 `width*height` (px²). 보정 시
`m² = px² / ppm²`.

> 모든 수치는 JSON 직렬화를 위해 순수 `float`/`int`로 캐스팅한다(np.float64 → 500 에러
> 방지). 06 참고.

## 오버레이 — `annotate(frame, targets, present, estimator)`

- ROI 폴리곤(파란선)
- present 객체만: ID별 색 박스 + `ID:n` + `x.x km/h` 라벨
- (정렬도 활성 시) 기준 방향 화살표(EVAC DIR) + 박스 모서리 정렬 점(녹=정렬/황/적=역류)
- **집계(count/avg)는 영상에 안 그린다** → 대시보드를 단일 기준으로(영상↔대시보드 불일치 방지)

`color`는 `src.inference._get_color(track_id)`를 재사용해 기존 파이프라인과 색 일관성 유지.
