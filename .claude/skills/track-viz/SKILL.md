---
name: track-viz
description: >-
  영상을 BoostTrack++(TRT)로 추론해 "좌: ID 추적 오버레이 / 우: 2D 맵 이동 방향벡터"를
  좌우로 합친 concat 시각화 mp4를 만든다. "이 영상 추론해서 시각화해줘", "ID 추적 + 2D 맵
  concat 만들어줘", "샘플 영상들 시각화 영상 만들어줘", "추적 결과 영상으로 뽑아줘", "여러 영상
  배치로 시각화", "결과 한눈에 몽타주로 보여줘" 같은 요청에 사용. tools/concat_viz.py(추론·concat)
  와 tools/montage.sh(전체 한장 요약)을 다룬다. (tracking visualization, BoostTrack, 2D map, concat)
---

# 추적 시각화 concat (ID 오버레이 ↔ 2D 맵)

영상을 주면 추론해서 **좌(ID 추적 박스+색) / 우(2D 맵 위 점+이동 방향벡터)** 를 좌우로 합친
mp4를 만든다. 같은 ID는 좌우 같은 색이라 매칭이 한눈에 된다. 이미 만들어 둔 스크립트 위에서
동작한다 — **새로 구현하지 말고 아래 명령을 쓸 것.**

## 환경 (이미 준비됨, 재설치 금지)
- **파이썬**: `~/miniconda3/envs/boosttrack/bin/python` (torch+TensorRT+CUDA).
- **TRT 엔진**: `external/weights/trt/yolox_mot20_fp16.engine`, `fastreid_sbs_s50_fp16.engine` — 이미 빌드됨. `BoostTrackGPUInference()`가 기본 경로로 로드. (검출기 투트랙 — RF-DETR 쓰려면 `rfdetr_base_fp16.engine`을 `bash tools/setup_rfdetr.sh`로 준비 후 `detector="rfdetr"`.)
- **GPU 1장 직렬 사용**: 모델 1개를 한 번 로드해 영상들을 순차 처리.

## 핵심 파일
- `tools/concat_viz.py` — 추론 → 좌/우 패널 → hconcat → H.264 재인코딩(어디서나 재생).
- `webui/map_render.py` — 2D 맵 렌더러(`index.html` drawMap의 Python/OpenCV 포팅, 재사용 가능).
- `tools/montage.sh` — 결과 폴더의 mp4들에서 대표 프레임 1장씩 뽑아 **한 장 몽타주**로 요약.

## 시각화 영상 만들기
```bash
PY=~/miniconda3/envs/boosttrack/bin/python

# 파일 1~여러 개
$PY tools/concat_viz.py video1.mp4 video2.mp4

# 폴더 통째 (그 안의 *.mp4 전부)
$PY tools/concat_viz.py /path/to/folder

# 출력 폴더·패널 폭 지정 (기본 out=results/clab_concat, 패널폭=960px)
$PY tools/concat_viz.py vids/ --out results/myrun --max-width 1280

# 인자 없이 실행하면 기본 2개 샘플(sample_example)로 스모크 테스트
$PY tools/concat_viz.py

# 방향성 정렬도(alignment) 시각화: 출구 방향 기준벡터(꼬리->머리, 원본 px)
$PY tools/concat_viz.py vid.mp4 --align "tx,ty,hx,hy"

# 검출기 투트랙: 기본 yolox. RF-DETR로 바꾸려면(고소·어안 화각에 강함) 엔진 준비 후 --detector rfdetr
#   bash tools/setup_rfdetr.sh   # 1회(모델다운→ONNX→TRT). 근거: docs/reports/RF-DETR-TRT-변환-사용법.md
$PY tools/concat_viz.py vid.mp4 --detector rfdetr
```
`--align` 를 주면 우측 맵의 객체 화살표가 정렬색(녹=정렬/황=가로질러/적=역류)으로
칠해지고 평균 정렬도가 표기된다. 안 주면 기존대로 동작(opt-in).
`--detector rfdetr` 는 검출기만 RF-DETR로 교체(트래커·ReID 동일). 안 주면 기존 YOLOX.
- 출력: `<out>/<영상명>_concat.mp4`. 좌우 패널 각 `max-width` 이하로 다운스케일(4K 부담↓).
- 배치가 오래 걸리면 **백그라운드로 실행**하고 `> <out>/_batch.log 2>&1` 로 로그를 남긴다.

## 결과 한눈에 보기 (몽타주)
```bash
tools/montage.sh results/clab_concat                       # -> results/clab_concat/overview_montage.png
tools/montage.sh results/myrun overview.png 5              # 출력경로·열수 지정
```
출력 시 `인덱스 → 영상명` 매핑을 같이 찍어준다. 사용자가 "한눈에/전체 결과 보여줘" 하면 이걸 만들어 보낸다.

## 검증 (빠르게)
```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,nb_frames -of csv=p=0 <out>.mp4
# 중간 프레임 확인:
ffmpeg -y -loglevel error -ss 3 -i <out>.mp4 -vframes 1 /tmp/check.png   # 그 뒤 이미지로 확인
```

## 주의 / 알아둘 것
- **캘리브레이션(ROI/호모그래피) 없이 돌리면** 우측 맵은 `image plane` 좌표(원근 보정 X). 방향벡터·ID·상대 위치는 정상이고, true top-down 미터맵이 아님. (데모용으론 충분)
- **세로형(portrait) 원본**은 결과가 세로로 길게 나온다(예: 1920×1708) — 버그 아님.
- 4K는 `--max-width`로 다운스케일된다. 더 크게 보려면 값을 키운다.
- OpenCV 텍스트는 ASCII만 → 패널 라벨은 영문(`ID TRACKING` / `2D MAP`).
- 표준 출력 폴더는 `results/` 아래. 원본 영상(NAS 등)은 건드리지 않는다.
- ffmpeg 필요(재인코딩·몽타주). 없으면 mp4v로만 저장되고 경고가 뜬다.
