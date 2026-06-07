# 03 · 속도 추정 & 대시보드 지표

`webui/speed.py` — `SpeedEstimator` 클래스 + `annotate()`.
**슬라이딩 윈도우 + 실세계 km/h 보정 + 지표 확장**으로 px/s 기반 속도를 추정한다.

## 입력/출력

- 입력: 프레임마다 `targets` (ndarray `N×≥5`, `[x1,y1,x2,y2,id,...]`)
- 출력: `update()` → `{id: speed}` (ROI 내 객체), `metrics(present)` → 대시보드 dict

## 생성자

```python
SpeedEstimator(fps, pixels_per_meter=None, roi=None, frame_size=None,
               window_sec=1.0, min_move_px=2.0)
```

- `pixels_per_meter`(ppm): 있으면 **km/h**, 없으면 **px/s** (`unit` 프로퍼티가 결정)
- `roi`: 4점 폴리곤(원본 px 좌표). 없으면 전체 프레임
- `window_sec`: 속도 산출 슬라이딩 윈도우(기본 1초 = `round(fps*1)` 프레임)
- `min_move_px`: 이보다 작은 이동은 노이즈로 보고 0 처리

## 속도 계산 (슬라이딩 윈도우)

객체별로 최근 `window+1` 프레임의 중심점 큐를 유지하고, 큐의 처음↔끝으로 산출:

```
dt   = (f_last - f_first) / fps          # 윈도우의 실제 경과 시간(초)
dist = ||center_last - center_first||    # 픽셀 이동거리
px_per_s = dist / dt
speed = (px_per_s / ppm) * 3.6   if ppm  # m/s → km/h
      = px_per_s                  otherwise
```

> 원형의 "3초마다 시작점 리셋" 배치 대신, 매 프레임 윈도우로 산출해 대시보드가
> 부드럽게 갱신된다.

## ROI 필터

`cv2.pointPolygonTest(roi, center) >= 0`인 객체만 측정. ROI를 벗어나면 그 객체의
이력(속도/체류/가속 상태)을 즉시 폐기(`_forget`). ROI 없으면 전체 객체.

## 거리 보정 (km/h)

ppm(미터당 픽셀)은 프론트의 **보정선 2점 + 실제거리(m)** 로 계산:
`ppm = (보정선 픽셀길이[원본해상도]) / 미터`. 서버는 이 값을 받아 위 공식에 사용.
보정을 안 하면 `unit="px/s"`로 폴백.

## 지표 (`metrics(present)` 반환 키)

| 키 | 의미 | 산식 |
|----|------|------|
| `unit` | 속도 단위 | ppm 있으면 `km/h`, 없으면 `px/s` |
| `count` | 현재 인원 | ROI 내 present 수 |
| `cumulative` | 누적 인원 | 지금까지 본 고유 ID 수(`seen_ids`) |
| `avg` / `max` | 평균 / 최고 속도 | present 속도들 |
| `accel` | 평균 가속도 크기 | 객체별 \|Δspeed\|/Δt 의 평균 |
| `moving` / `stationary` | 이동 / 정지 수 | 속도 > `move_thresh`(km/h:0.5, px/s:3) |
| `moving_ratio` | 이동 비율 % | moving/count×100 |
| `density` + `density_unit` | 밀도 | count ÷ 면적. 보정 시 `명/m²`(면적=ROI/전체 px → m²), 미보정 시 `명/Mpx` |
| `level` / `level_kr` | 혼잡도 | 명/m² 기준 <0.4 Low/여유, <1.0 Normal/보통, ≥1.0 High/혼잡 |
| `avg_dwell` / `max_dwell` | 체류시간(s) | `(현재프레임 - 첫등장프레임)/fps` |
| `objects[]` | 객체별 | `{id, speed, dwell}` (속도 내림차순) |

면적 산출: ROI 있으면 `cv2.contourArea(roi)`, 없으면 `width*height` (px²). 보정 시
`m² = px² / ppm²`.

> 모든 수치는 JSON 직렬화를 위해 순수 `float`/`int`로 캐스팅한다(np.float64 → 500 에러
> 방지). 06 참고.

## 오버레이 — `annotate(frame, targets, present, estimator)`

- ROI 폴리곤(파란선)
- present 객체만: ID별 색 박스 + `ID:n` + `x.x km/h` 라벨
- **집계(count/avg)는 영상에 안 그린다** → 대시보드를 단일 기준으로(영상↔대시보드 불일치 방지)

`color`는 `src.inference._get_color(track_id)`를 재사용해 기존 파이프라인과 색 일관성 유지.
