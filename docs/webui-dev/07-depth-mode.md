# 07 · Depth 자동 모드 (Depth-Anything-3)

수동 실측 없이 **메트릭 km/h**를 뽑는 모드. 시작 시 1회 깊이를 떠서 바닥 기하를
자동 추정하고, 그 결과를 **사용자가 미리보기로 확인한 뒤** 측정으로 넘어간다.

## da3 conda env 설치 (1회)

```bash
# 1. da3 전용 환경 생성 (Python 3.11 — DA3 의존성이 3.12와 충돌)
conda create -n da3 python=3.11 -y
conda activate da3

# 2. DA3 호환 PyTorch (cu128 계열) 설치
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

# 3. Depth-Anything-3 및 의존성 설치
pip install depth-anything-v3
pip install xformers open3d

# 4. 모델 자동 다운로드 확인 (Hugging Face 토큰 필요)
# 첫 실행 시 depth-anything/DA3METRIC-LARGE 가 자동 다운로드됨 (~2 GB)
# HF 인증: huggingface-cli login

# 5. 서버에서 da3 파이썬 경로 지정 (기본값이 아닌 경로면 명시)
# export DA3_PYTHON=~/miniconda3/envs/da3/bin/python
```

> **왜 별도 env인지는 아래 참조.**

## 왜 별도 env / subprocess인가

Depth-Anything-3는 `numpy<2`, `torch cu128`, `xformers`, `open3d`, `pycolmap` 등을
요구해 boosttrack env(numpy 2.x, torch cu130)와 **공존 불가**. 그래서 전용 conda env
`da3`를 만들고, 서버(boosttrack)는 `subprocess`로 그 env의 파이썬을 호출한다.

- env 경로: `DA3_PYTHON`(기본 `~/miniconda3/envs/da3/bin/python`)
- 모델: `depth-anything/DA3METRIC-LARGE` (Apache-2.0, 메트릭 깊이, intrinsics는 미제공)

## 파이프라인

```
첫 프레임 → da3_depth.py(da3 env, subprocess) → 깊이맵 .npy(미터) + 컬러 .png
          → 검출기로 사람 박스(boosttrack) → depth_ground.estimate():
              focal = median(h_px · Z / 1.7)        # 사람키 1.7m 앵커
              ground plane = RANSAC(역투영된 바닥 픽셀)
              H(image→ground meters) = 4점 ray-plane 교차로 호모그래피
          → SpeedEstimator(homography=H)  # Phase 1 경로 그대로 재사용
```

핵심: depth는 **수동 4점 호모그래피를 자동 생성**하는 역할. 그래서 속도/밀도/체류 등
나머지 로직은 전혀 안 바뀐다. 스케일은 사람키 1.7m로 고정 → **추정값**(UI에 명시).

## 엔드포인트 / UI 흐름

1. UI에서 **Depth 자동** 선택 + (선택)ROI → "측정 시작"
2. `POST /prepare_depth/{id}` (≈모델로드 후 <1s, 최초 ~40s) → da3 깊이 + 평면/호모그래피
   생성, `{ok, vis, focal, inlier, people}` 반환
3. UI가 `GET /depthvis/{id}`(컬러 깊이맵)를 띄우고 정보 표시 → **사용자 확인**
4. "이 깊이로 측정 시작" → `POST /start {mode:"depth"}` (prepare에서 만든 H 사용)

> 그냥 넘어가지 않고 깊이 품질을 눈으로 확인시키는 단계가 핵심(요청사항).

## 코드

- `webui/da3_depth.py` — da3 env에서 도는 깊이 추출 CLI(`--image/--out-depth/--out-vis`)
- `webui/depth_ground.py` — focal(사람키)·RANSAC 평면·호모그래피·ROI 면적(m²)
- `webui/server.py` — `/prepare_depth`, `/depthvis`, `start(mode=depth)`

## 검증 결과(sample1.mp4)

- focal 307.8px(hfov~115°), 평면 inlier 0.88, 사람 49
- 사람 최근접 간격 중앙값 **0.75m**(p10 0.47/p90 2.6) → 스케일이 현실적
- depth 모드 실행 시 km/h 정상, 밀도 ~0.27 명/m²

## 한계 (정직하게)

- **추정값**: GT 없음. 스케일은 사람키 가정 + DA3 깊이 정확도에 의존. UI에 "추정" 표기.
- DA3METRIC은 **intrinsics 미제공** → focal은 사람키로 역산(서 있는 보행자 가정).
  앉음/아이/카트가 많으면 노이즈 → 중앙값으로 완화.
- 바닥 평면 가정(비평면/계단 장면엔 부정확). 가림 많으면 평면 표본 줄어듦.
- 셋업당 da3 모델 로드 비용(최초 ~40s). 시작 시 1회라 실측정엔 영향 없음.
- 라이선스: metric/mono(Apache) = 상업 가능 / giant·nested(CC BY-NC) = 비상업.
- 검증용으로 설치한 playwright·alsa-lib는 런타임 불필요(개발 의존).
