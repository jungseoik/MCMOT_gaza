# results/rehearsal_sessions — 리허설 운영 뷰 녹화

운영 뷰(:8900)에서 리허설을 돌리며 **브라우저 화면을 그대로 담은** 영상.
맵의 점과 영상 속 사람이 같이 보여야 설득이 되므로, 카메라 그리드를 옆에 붙인
`*_with_grid.mp4` 판을 같이 둔다.

## 다시 뽑기

```bash
# ① 운영 뷰 녹화 (리허설 재생 + 화면 녹화를 한 번에)
python tools/record_opsview.py --scenario pkg:cj-rehearsal:scenario_15_full \
       --floor floor10 --origin 935,870 \
       --off zones --on uniColor --dens card --zoom 2.0 \
       --label "CJ 10F 전체 (시나리오 01~14 연속)" \
       --out results/rehearsal_sessions --name scenario_15_full.mp4

# ② 카메라 그리드를 옆에 붙인다 (싱크는 ①이 남긴 사이드카 기준)
python tools/compose_grid.py --rec results/rehearsal_sessions/scenario_15_full.mp4 \
       --clips media/vsource/cj/rehearsal/scenario_15_full --shift 2.0
```

`record_opsview.py` 는 `<영상>.json` 에 **클립 t=0 이 녹화 몇 초 지점인지**를 남긴다.
그게 없으면 그리드 싱크를 눈대중으로 맞추게 된다.

## 2026-10-01 판 (현재)

| 파일 | 규격 | 길이 |
|---|---|---|
| `scenario_15_full.mp4` | 1600×900 @25fps · 27MB | 468s |
| `scenario_15_full_with_grid.mp4` | 2240×720 @25fps · 16MB | 452s |

세션 `sess-1790823396679` · 10F · 추론 프로파일 `yolo26_clipreid`.
화면 설정: **구역 끔 · 이름표 끔 · 단일색 켬 · 지표 카드(2×2) · 맵 ×2.0 중앙 확대**.

종료 시점 SEI 70.9 · EPFI 91.8 · CBS 0.0 · IDR 4.25 (출구 e1 25명 / e2 67명).
CBS 0.0 은 10F 병목 임계밀도가 2.0명/m² 로 높아서다 — 2026-08-28 판도 같았다.

## 싱크 — 눈대중 금지

`clip_t0_at_rec_sec` 를 그대로 쓰면 **2초 어긋났다**. 그 값은 `/api/vsource/start` 가
**응답한** 시각이라, 실제 첫 프레임이 나오기까지의 시간이 빠져 있다.

좌측 배지(`리허설 재생 중 · Ns`)를 출력 시각과 대조해 실측했다:

| 출력 t | 20 | 60 | 120 | 200 | 260 | 340 | 400 |
|---|---|---|---|---|---|---|---|
| 보정 전 배지 | 18 | 58 | 118 | 198 | 258 | 338 | 398 |
| **보정 후(`--shift 2.0`)** | — | **60** | — | **200** | — | **340** | — |

차이가 전 구간 일정한 2초였다(끝단 제외) — **드리프트가 없다는 건 재생이 정확히
1:1 이었다는 뜻**이다. 그래서 상수 보정으로 충분하다.

## 빠지기 쉬운 함정

- **빈 칸을 채우는 `color=` 소스는 끝나지 않는다.** 14대를 4×4 에 깔면 2칸이 비는데,
  `color` 에 `d=` 를 안 주면 xstack 이 무한히 돌아 파일이 끝없이 커진다
  (실측: 1시간 45분 동안 1.1GB). 클립 길이로 `d=` 와 출력 `-t` 를 못 박는다.
- **`/api/vsource/status` 의 키는 `running`** 이다. `playing`/`active` 를 보면 첫
  바퀴에 바로 끝나 8초짜리 토막이 나온다.
- **`?floor=` 쿼리는 운영 뷰에 안 먹는다.** 층 셀렉터(`#floorSel`)를 직접 바꾸고
  값까지 확인해야 한다 — 안 그러면 리허설은 10F 에서 도는데 화면은 17F 를 보여준다.
- **로그인 게이트**가 클릭을 전부 가로챈다. `#lgForm` 에 submit 을 쏴서 먼저 넘는다.
- 탭 전환이 한 번에 안 먹을 때가 있다 — `#viewLive` 가 보일 때까지 다시 누른다.

## _v1_2026-08-28/ — 이전 판

| 파일 | 비고 |
|---|---|
| `scenario_15_full_with_grid.mp4` | 원본 그리드판(2240×720) — 무손상 |
| `scenario_15_full_result.png` | 원본 결과 스크린샷 — 무손상 |
| `scenario_15_full_opsview_1280x720.mp4` | 그리드판 좌측을 잘라낸 것. 원본 운영 뷰(1600×900)를 재녹화 중 실수로 덮어써서, **1280×720 으로 축소된 상태**로만 남아 있다 |

이전 판은 `표시` 툴바 한 줄 시절 UI 이고 **구역·이름표가 켜져 있다**. 지금 판과
화면 구성이 다른 건 그 때문이다.
