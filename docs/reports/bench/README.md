# 다채널 확장성 벤치마크 — 재현 가이드

본 폴더는 [다채널 확장 타당성 보고서](../2026-06-07_다채널확장-타당성-및-기술리스크-보고서.md)와
[검출 해상도 스케일링 보고서](../2026-06-07_검출해상도-스케일링-실측-보고서.md)의 **모든 수치를
재현**하기 위한 스크립트와 원자료(JSON)입니다. 추론 로직은 레포 원본(`src/inference_gpu.py`
등)을 **호출만** 하며 수정하지 않습니다.

## 0. 사전 준비

```bash
conda activate boosttrack            # 또는 ~/miniconda3/envs/boosttrack/bin/python
# TRT 엔진(기본 896×1600)이 빌드돼 있어야 함:
python -m src.build_trt --fp16       # external/weights/trt/*.engine 생성(최초 1회)
```

- **GPU 선택**: 이 호스트는 GPU 0을 다른 작업이 점유할 수 있어, 측정은 **빈 GPU(기본 1번)** 에서
  수행한다. `CUDA_VISIBLE_DEVICES=1` (또는 `BENCH_GPU=1`)로 지정.
- **입력 영상**: `assets/sample1.mp4` (960×540, 공항 혼잡 장면).
- 빌드되는 엔진/ONNX는 `.gitignore` 대상(`external/weights/`)이라 커밋되지 않음 → 아래로 재생성.

## 1. 스크립트 ↔ 측정 항목 ↔ 보고서 섹션

| 스크립트 | 측정 | 출력 JSON | 보고서 |
|----------|------|-----------|--------|
| `build_dynamic_yolox.py` | YOLOX 동적배치 엔진 빌드(배치곡선용 사전작업) | — | — |
| `bench_batch.py` | ①검출 배치 1~15 지연 ②ReID crop 배치 ③CPU 전처리 ④단일 스트림 | `results.json` | 3.A~3.D |
| `bench_pipeline_channels.py` | 검출(배치)+추적 전체를 1~15채널 라운드(1프로세스·1GPU) | `results_pipeline.json` | 3.E |
| `bench_multiproc.py` | 채널별 독립 프로세스 **2GPU 병렬** 실효 fps | `results_multiproc.json` | 3.G |
| `build_resolution_engines.py` | 해상도별 고정배치 FP16 엔진 빌드 | — | (해상도 보고서) |
| `bench_resolution.py` | 해상도별 단일/병렬 fps(단일 GPU) | `results_resolution.json` | (해상도 보고서) |
| `bench_mps.py` | 단일 GPU 멀티채널 fps (MPS off/on 비교용) | `results_mps_{tag}.json` | 3.H |

## 2. 실행 순서 (전체 재현)

```bash
cd <repo-root>
PY=~/miniconda3/envs/boosttrack/bin/python

# (A) 모델 단위 배치 곡선 + 단일 스트림  → 3.A~3.D
CUDA_VISIBLE_DEVICES=1 $PY docs/reports/bench/build_dynamic_yolox.py
CUDA_VISIBLE_DEVICES=1 $PY docs/reports/bench/bench_batch.py

# (B) 전체 파이프라인 다채널(1프로세스)  → 3.E
CUDA_VISIBLE_DEVICES=1 $PY docs/reports/bench/bench_pipeline_channels.py

# (C) 병렬 다채널(2GPU)  → 3.G   (GPU를 rank%2로 분산)
$PY docs/reports/bench/bench_multiproc.py

# (D) 검출 해상도 스케일링  → 해상도 보고서
CUDA_VISIBLE_DEVICES=1 $PY docs/reports/bench/build_resolution_engines.py
BENCH_GPU=1 $PY docs/reports/bench/bench_resolution.py

# (E) MPS 효과  → 3.H
#   E-1) MPS 없이 (baseline)
BENCH_GPU=1 $PY docs/reports/bench/bench_mps.py nompS
#   E-2) MPS 데몬을 GPU1에 기동 (물리 GPU1을 server가 device 0으로 재인덱싱)
mkdir -p /tmp/mps_gpu1 /tmp/mps_gpu1_log
export CUDA_MPS_PIPE_DIRECTORY=/tmp/mps_gpu1 CUDA_MPS_LOG_DIRECTORY=/tmp/mps_gpu1_log
CUDA_VISIBLE_DEVICES=1 nvidia-cuda-mps-control -d
#   ⚠️ 함정: 데몬을 CUDA_VISIBLE_DEVICES=1로 띄우면 클라이언트는 device 0으로 접속해야 함
#       (안 그러면 CUDA error 100). 그래서 BENCH_GPU=0 로 실행:
BENCH_GPU=0 $PY docs/reports/bench/bench_mps.py mps
#   E-3) 데몬 종료(원상복구)
echo quit | CUDA_MPS_PIPE_DIRECTORY=/tmp/mps_gpu1 nvidia-cuda-mps-control
```

## 3. 측정 방법 공통 규약

- **워밍업** 후 고정 프레임 수 평균(엔진/캐시 안정화). `time.perf_counter` + `torch.cuda.synchronize`.
- **fps 정의**: `처리프레임수 / 처리시간`. 단일 스트림은 1프로세스, 병렬은 프로세스별 fps를 모아
  평균(per-channel)·합(aggregate)으로 보고.
- **전처리(letterbox)** 는 `bench_batch.py`에서 별도 측정(29ms/frame). `bench_pipeline_channels.py`는
  전처리를 제외(사전 패딩)하고 검출+추적만 측정 — 보고서에서 그렇게 명시.
- **밀도 의존성**: sample1은 혼잡 장면(~40~50명). 밀도가 낮으면 추적/ReID 비용이 줄어 수치가 더 좋아짐.

## 4. 핵심 결과 (요약, 본 머신 RTX PRO 6000 Blackwell)

- 단일 스트림: ~12.5 fps (혼잡). 검출 배치는 처리량 거의 무효(+9%), 병목은 추적.
- 다채널(2GPU 병렬, 현재 코드): 15채널 = **채널당 ~7fps / 총 106fps**.
- per-GPU 천장(실측): MPS off ~54fps, MPS on ~65~70fps (이론 ~100엔 CPU 오버헤드로 미달).
- MPS 효과: 8채널에서 +27%(51→65fps). 해상도↓: ×1.2~1.5. 둘 다 완만 — 병목은 추적.
- 150채널 → 채널당 ~1~1.3fps(2GPU). 유용 fps엔 추적 경량화 + GPU 증설 필요.
- 상세는 `results_resolution.json` / `results_mps_*.json` 및 두 보고서 참조.
