# DeepStream 전환 e2e 출력 유사도 검증 (2026-07-19)

기존 ffmpeg(cv2)+직렬 TRT 경로와 새 DeepStream 경로(`system/ingest_ds/`,
커밋 4af2d2f)는 전처리가 다르다 — letterbox 보간(cv2 uint8 vs GPU float),
색공간 변환(ffmpeg 디코드 vs NVDEC+nvvideoconvert), nvstreammux 1920×1080
stretch. 이 차이가 검출·임베딩·트랙 산출에 실제로 얼마나 영향을 주는지
**결정적 입력으로 프레임 단위 정렬해 정량 측정**하고 채택 가능 여부를 판정한다.

**결론 먼저: 채택 가능.** 검출·트랙은 기준 통과(매칭률 99.4%/99.0%,
IoU 0.976/0.975). ReID 임베딩 cross-경로 cosine은 0.956으로 기준(0.98)에는
못 미치나, 원인이 **디코더+색변환 픽셀 차이**(엔진·crop 방식 아님)로 특정되고
타인 간 cosine(0.226) 대비 판별 마진이 커서 **트랙 산출 동등성에 영향 없음**이
실측으로 확인됐다(§4, §5).

## 1. 실험 설계

| 항목 | 내용 |
|------|------|
| 입력 | `assets/sample1.mp4` (960×540, 25fps, **646프레임**) — 양쪽 동일 파일 |
| 기존 경로 | 호스트 conda `boosttrack`: cv2 디코드 → `dataset.preproc`(cv2 uint8 bilinear letterbox) → TRT YOLOX(fp16, TRT 10.16) → `GPUEmbeddingComputer`(CPU cv2 crop) + TRT FastReID → BoostTrack |
| DS 경로 | 컨테이너 `macs-deepstream:9.0`: `nvurisrcbin`(file://) → nvstreammux 1920×1080 → RGBA zero-copy → GPU float bilinear letterbox → TRT YOLOX(fp16 dynamic, TRT 10.14) → GPU crop TRT FastReID → BoostTrack |
| 프레임 정렬 | DS는 `analyze_fps=0`(게이트 off) + `--lossless`(drop 없는 backpressure, EOS 자동종료) → **양쪽 646/646 전 프레임, drop 0** — seq 1:1 매칭 |
| 공통 조건 | GPU1, det_thresh 0.4, ECC off, `per_instance_ids=True`, max_age 50(25fps×2s), mot20/test_dataset |
| 좌표 정규화 | 양쪽 검출·트랙을 원본 px(960×540)로 환산 후 비교 (DS는 mux px ÷2) |

sample1은 16:9라 mux stretch는 **비율 왜곡 없는 순수 2× 업스케일**이다
(4:3 소스의 가로 왜곡 영향은 이 실험 범위 밖).

## 2. 측정 결과

### 2-1. 검출 bbox (conf≥0.4, 프레임별 헝가리안 매칭, IoU>0.5)

| 지표 | 값 | 기준 | 판정 |
|------|-----|------|------|
| 검출 수 (기존/DS) | 36,564 / 36,677 | — | — |
| 매칭률 | **99.44%** (프레임 평균 98.99%) | >95% | ✅ PASS |
| 매칭쌍 IoU | mean **0.9756** · p5 0.9461 · min 0.6494 | mean>0.9 | ✅ PASS |
| conf 차이 | mean 0.0075 · max 0.2526 | — | 참고 |

### 2-2. ReID 임베딩 (매칭 검출쌍 37,561개 cosine)

| 지표 | 값 | 기준 | 판정 |
|------|-----|------|------|
| cosine | mean **0.9563** · p5 0.8598 · p1 0.7742 · min 0.4563 | mean>0.98 | ⚠️ 기준 미달 |
| (참조) 타인 간 cosine — 동일 경로 내 서로 다른 검출 | mean 0.2260 · p95 0.4465 | — | 판별 마진 충분 |

기준 미달이지만 §4의 원인 분해와 §2-3의 트랙 동등성으로 실효 영향 없음 판단 — §5.

### 2-3. 트랙 산출 (통계적 동등성)

| 지표 | 기존 | DS |
|------|------|-----|
| 유니크 트랙 ID 수 | 99 | 96 |
| 트랙 프레임 총합 | 37,643 | 37,253 |
| 수명(프레임) mean / median / max | 380.2 / 486 / 646 | 388.1 / 512 / 646 |
| 프레임별 트랙 bbox 매칭률 (IoU>0.5) | **98.95%** (매칭 IoU mean 0.9754) | |

ID 시퀀스 완전 일치는 기대하지 않음(부동소수 순서 차이로 분기 가능) —
트랙 수·수명 분포·bbox 위치 모두 동등 수준.

### 2-4. 디코드 프레임 PSNR (앞 5프레임)

| seq | 기존↑업스케일 vs DS mux(1080p) | DS↓다운스케일 vs 원본(540p) |
|-----|------|------|
| 1 | 30.13 dB | 29.36 dB |
| 2 | 30.30 dB | 29.54 dB |
| 3 | 30.23 dB | 29.47 dB |
| 4 | 30.41 dB | 29.68 dB |
| 5 | 30.35 dB | 29.61 dB |

RGB 채널 평균 오프셋(DS − 기존)이 전 프레임에서 일정하게
**R −1.75 / G +0.45 / B −2.34** — 스케일러 차이 외에 **YUV→RGB 색변환
계수/레인지 차이가 체계적으로 존재**함을 시사(단순 BT.601↔709 행렬 스왑
재현으로는 MAE가 오히려 증가 → 표준 행렬 스왑만으로는 설명 안 되는
디코더·컨버터 고유 차이).

## 3. 임베딩 차이 원인 분해

같은 호스트 ReID 엔진에 서로 다른 crop을 넣어 요인별로 격리
(`attrib`/`reid-embed` 서브커맨드, 앞 5프레임 258개 crop):

| 격리 조건 | cosine mean | 해석 |
|-----------|-------------|------|
| TRT 엔진 빌드만 다름 (동일 crop, 호스트 10.16 static vs 컨테이너 10.14 dynamic) | **1.0000** (min 0.99996) | 엔진 차이 **없음** |
| crop 방식만 다름 (동일 픽셀, cv2 uint8 vs GPU float bilinear) | **0.9986** (min 0.9902) | 무시 가능 |
| 픽셀 소스가 다름 (NVDEC+mux 업스케일+색변환, 동일 엔진) | **0.9609** (min 0.6642) | **지배 요인** |
| └ DS 프레임 채널 오프셋 보정 후 | 0.9738 | 색변환 오프셋이 차이의 상당분 |
| └ DS 프레임을 원본 해상도로 다운스케일 후 | 0.9283 | 업스케일 자체는 손해 아님(다운 시 정보 손실) |

즉 총 차이(0.9563)는 사실상 전부 **디코더(NVDEC vs ffmpeg) + YUV→RGB 변환 +
스케일러의 픽셀 수준 차이**에서 온다. README(`system/ingest_ds/README.md`)의
"±1/255 수준" 가정은 letterbox 보간에는 맞지만, **색변환 차이(채널당 ~2/255
체계적 오프셋)가 추가로 존재**한다는 것이 이번 실측의 새 발견.

## 4. 판정

| 단계 | 기준 | 결과 | 판정 |
|------|------|------|------|
| 검출 bbox | 매칭률>95%, IoU>0.9 | 99.44% / 0.976 | ✅ **PASS** |
| ReID 임베딩 | cos>0.98 | 0.956 | ⚠️ 미달 — 단, 아래 근거로 수용 |
| 트랙 산출 | 통계적 동등 | 매칭률 98.95%, ID 수·수명 동등 | ✅ **PASS** |

**임베딩 수용 근거**: ① 차이의 원인이 모델/엔진/crop 로직이 아니라 픽셀
소스(디코더+색변환)로 특정됨 ② 임베딩은 경로 내부에서 일관되게 쓰이므로
(cross-경로 혼용 없음) 절대값 차이보다 판별력이 중요 — 동일인 cross-경로
0.956 ≫ 타인 간 0.226으로 마진 충분 ③ 최종 소비 산출물인 트랙이 실측으로
동등(98.95%).

**종합: DeepStream 경로 채택 가능.** 전처리 차이는 존재하지만 4대 지표의
기반이 되는 트랙 메타 산출 관점에서 기존 경로와 동등하다.

잔여 리스크·추후 확인 사항:
- 4:3 소스의 mux stretch 가로 왜곡은 이번 실험 범위 밖 (README 기재대로 검출률 영향 경미 예상 — 필요시 동일 방법으로 추가 실측).
- 색변환 오프셋을 더 줄이려면 nvvideoconvert/nvstreammux의 colorimetry 설정 매칭을 검토할 수 있으나, 현재 수치로는 불필요.

## 5. 재현 방법

```bash
# 0) 전제: macs-deepstream:9.0 이미지 + external/weights/trt_ds/ 엔진 (system/ingest_ds/README.md)
V=/tmp/verify && mkdir -p $V/ds
printf '[{"cam_id":"sample1","rtsp":"file:///workspace/assets/sample1.mp4","analyze_fps":0}]' > $V/cams_file.json

# 1) 기존 경로 덤프 (호스트, GPU1)
CUDA_VISIBLE_DEVICES=1 conda run -n boosttrack python docs/reports/bench/verify_ds_similarity.py \
    baseline --video assets/sample1.mp4 --out $V/base --max-age 50 --dump-frames 5

# 2) DS 경로 덤프 (컨테이너 — worker.py 검증 플래그: --verify-dump/--lossless/--dump-frames/--max-age)
docker run --rm --network host --gpus device=1 -v "$PWD:/workspace" -v $V:/verify -w /workspace \
    macs-deepstream:9.0 python3 -m system.ingest_ds.worker --cams /verify/cams_file.json \
    --verify-dump /verify/ds --lossless --dump-frames 5 --max-age 50

# 3) 비교 (검출/임베딩/트랙/PSNR)
conda run -n boosttrack python docs/reports/bench/verify_ds_similarity.py compare \
    --base $V/base --ds $V/ds/sample1 --json $V/metrics.json

# 4) 임베딩 차이 원인 분해 (crop 방식·픽셀 소스 격리 + 엔진 격리)
CUDA_VISIBLE_DEVICES=1 conda run -n boosttrack python docs/reports/bench/verify_ds_similarity.py \
    attrib --base $V/base --ds $V/ds/sample1 --save-crops $V/crops.npy
CUDA_VISIBLE_DEVICES=1 conda run -n boosttrack python docs/reports/bench/verify_ds_similarity.py \
    reid-embed --engine external/weights/trt/fastreid_sbs_s50_fp16.engine --crops $V/crops.npy --out $V/emb_host.npy
docker run --rm --gpus device=1 -v "$PWD:/workspace" -v $V:/verify -w /workspace macs-deepstream:9.0 \
    python3 docs/reports/bench/verify_ds_similarity.py reid-embed \
    --engine external/weights/trt_ds/fastreid_sbs_s50_fp16_dyn_b256.engine --crops /verify/crops.npy --out /verify/emb_ds.npy
```

worker.py의 검증 플래그(`--verify-dump`, `--lossless`, `--dump-frames`,
`--max-age`)는 기본 비활성 옵션으로 추가되어 기존(라이브 RTSP) 동작은 불변.
`--lossless`는 drop 대신 backpressure를 거는 **파일 소스 검증 전용** 모드다.

부기: `tests/system` 58건 중 `test_graph_empty_straight_line_fallback` 1건
실패는 이 브랜치 이전부터 존재하는 기존 이슈로 재확인(57 passed / 1 failed,
이번 변경과 무관).
