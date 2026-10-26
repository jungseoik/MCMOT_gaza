# tools/deid — 영상 비식별화(머리 영역)

발표·외부 공유 영상에 들어가는 카메라 화면에서 **사람 머리 영역을 모자이크+블러**로 가린 사본을
만든다. 원본은 건드리지 않고, **프레임 수·fps 를 원본과 똑같이** 유지해 도면·지표와의 시간 동기가
그대로 맞는다.

## 가리는 기준 — "웬만하면 다 가린다"

세 검출의 **합집합**을 가린다. 오탐(사람 아닌 곳이 조금 흐려짐)은 감수하고 놓침을 줄이는 쪽으로
기준을 낮췄다.

| 검출 | 모델 | 기준 | 가리는 영역 |
|---|---|---|---|
| 머리 (주) | `real_head_v0_l_yv11` (PIA, YOLO11-L, class head) | conf ≥ 0.10 | 머리 박스를 사방 30% 키움 |
| 사람 (보조) | `yolo26m` person | conf ≥ 0.12 | 사람 박스 위쪽 32% 띠(좌우 10% 여유) |
| 자세 (보조) | `yolo11x-pose` | conf ≥ 0.12, 키포인트 ≥ 0.20 | 코·눈·귀 둘레(사람 폭 40% 이상) |
| 시간 확장 | — | 앞뒤 ±3 프레임 | 그 프레임들에서 찾은 영역도 함께 |

- 입력 1280 px. 모자이크(최대 6×6 칸) 위에 가우시안 블러를 겹친다.
- 표본 처리(`results/demo/06_비식별화_폐기증빙/`, 사람 0.2·자세 0.2/키포인트 0.3, ±1 프레임)보다
  기준이 넓다.
- 한계: 세 검출이 **모두** 놓치고 앞뒤 3 프레임 안에서도 못 찾은 경우는 가려지지 않는다.

모델 파일(git 미포함):

| 모델 | 위치 |
|---|---|
| real_head_v0_l_yv11.pt | `~/seoik/.cache/pia_models/real_head_v0_l_yv11/` |
| yolo26m.pt | `docs/reports/yolo_compare/weights/` |
| yolo11x-pose.pt | `external/reference/reid/_weights/pose/` |

## 사용법

```bash
V=media/vsource/cj/drill-1022
# 1) 미리보기 — 150초마다 한 장, 왼쪽=검출 영역(초록 머리·주황 사람·분홍 자세) / 오른쪽=가린 결과
python tools/deid/deid_video.py $V/scenario_17F/cam17.mp4 --in-root $V --out-root /tmp/deid --preview 150

# 2) 전 구간 처리 — <in-root> 기준 상대경로 그대로 <out-root> 에 저장 (+ .deid.json 기록)
python tools/deid/deid_video.py $V/scenario_17F/cam*.mp4 --in-root $V --out-root $V/_deid --gpu 0 --skip-existing
```

`.deid.json` 에 원본/출력 프레임 수(`frames_src`·`frames_out` — 같아야 함), 검출원별 영역 수, 파라미터가
남는다. 처리 속도는 1280×720 기준 GPU 하나에 프로세스 6~7개를 띄우면 프로세스당 약 7 fps
(RTX PRO 6000 Blackwell 2장, 13 프로세스 ≈ 90 fps).

## CJ 실훈련(2026-10-22) 비식별판 영상

`results/demo/05_CJ1022/` 의 `_비식별` 판(16층·17층 단독, 전체 3개 층)이 이 사본으로 만든 것이다.

```bash
# 26대 비식별 사본 → media/vsource/cj/drill-1022/_deid/  (git·HF 에 올리지 않음)
# 층별 렌더 — 기존 점판과 같은 인자에 --clip-root 만 추가
python tools/demo_viz.py --session <세션> --floor <층> --metric ALL --package cj-drill-1022 \
  --scenario <시나리오> --tag <tag>_deid --title "<제목>" --th '{"dt_hold":2.0}' --map dots \
  --clip-root media/vsource/cj/drill-1022/_deid --out <폴더>
# 패키징
python tools/cj1022_package.py --variant deid --only 16F_floorend --only 17F_floorend --only 3F_all \
  --report-dir results/demo/05_CJ1022/_리포트
```

`--clip-root` 에 사본이 하나라도 없으면 demo_viz 는 원본으로 대체하지 않고 멈춘다(비식별판에 원본이
섞이지 않게).
