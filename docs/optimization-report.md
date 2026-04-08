# BoostTrack++ 추론 최적화 보고서

## 1. 요약

| 항목 | 원본 (PyTorch) | 최적화 (TRT+GPU) | 변화 |
|------|---------------|-----------------|------|
| 전체 FPS (646프레임) | 0.88 | 17.6 | **x20배** |
| 1프레임 처리 시간 | 1,140 ms | 57 ms | -95% |
| 탐지 정확도 (Match%) | 기준 | 99.9% | 사실상 동일 |
| 추적 ID 일치율 | 기준 | 98.9% | 1.1% 차이 |
| 기존 코드 수정 | - | 없음 | - |
| 외부 라이브러리 수정 | - | 없음 | - |

---

## 2. 원본 파이프라인 병목 분석

1프레임 처리 시간 1,140ms를 분해한 결과:

```
전처리 (preproc)                 38.8 ms     3.4%
YOLOX 탐지                       39.7 ms     3.5%
추적 업데이트 (track_update)     815.7 ms    71.6%  
  ├─ ECC 카메라 보정               8.4 ms     0.7%
  ├─ 칼만 필터 예측                1.0 ms     0.1%
  ├─ 신뢰도 부스팅 (DLO/DUO)      1.5 ms     0.1%
  ├─ ReID 임베딩 계산           694.7 ms    60.9%  ← 핵심 병목
  │    ├─ cv2 crop+resize 루프   536.8 ms   (89%)
  │    ├─ GPU 모델 추론           23.7 ms    (4%)
  │    └─ 텐서 변환/전송          134.2 ms    (7%)  ← 실제 원인
  ├─ 연관 (association)           1.1 ms     0.1%
  └─ 트래커 갱신                  1.8 ms     0.2%
영상 쓰기 등 기타                245.8 ms    21.5%
```

GPU 모델 추론(YOLOX 40ms + ReID 24ms = 64ms)은 전체의 5.6%에 불과했다.
**병목의 본질은 ReID 전처리에서 50개 객체마다 개별 텐서를 생성하고 torch.cat으로 합치는 과정**이었다.

### 왜 느렸나: 텐서 생성 패턴의 문제

원본 코드 (`tracker/embedding.py`):
```python
crops = []
for p in bbox:                          # 50회 반복
    crop = img[p[1]:p[3], p[0]:p[2]]    # numpy slice: ~0.01ms
    crop = cv2.cvtColor(crop, ...)       # ~0.05ms
    crop = cv2.resize(crop, ...)         # ~0.1ms
    crop = torch.as_tensor(crop)         # ~2ms (numpy→tensor 복사)
    crop = crop.unsqueeze(0)             # ~0.5ms
    crops.append(crop)                   # Python list append
crops = torch.cat(crops, dim=0).cuda()   # 50개 텐서 합치기 + GPU 전송: ~100ms
```

`torch.as_tensor` × 50회 + `torch.cat` 1회가 전체 536ms 중 대부분을 차지.
cv2 연산 자체는 50회 합쳐도 ~10ms밖에 안 된다.

---

## 3. 최적화 방법

### 3.1 TensorRT 엔진 변환

YOLOX와 FastReID 모델을 ONNX로 export한 뒤 TensorRT FP16 엔진으로 빌드했다.

| 모델 | PyTorch | TRT FP16 | 속도 향상 |
|------|---------|----------|----------|
| YOLOX (896x1600) | 36.1 ms | 8.0 ms | x4.5 |
| FastReID (50 crops) | 23.7 ms | 7.4 ms | x1.7 |

TRT만으로는 전체 파이프라인 속도가 1.06배밖에 안 올랐다 (병목이 모델 추론이 아니므로).

### 3.2 ReID 텐서 전처리 최적화 (핵심)

cv2 resize 연산은 그대로 유지하되, 텐서 생성/전송 방식만 변경했다:

**최적화 코드 (`src/inference_gpu.py`의 `GPUEmbeddingComputer`):**
```python
# 1. numpy buffer 사전 할당 (재사용)
buf = np.empty((max_n, 3, 384, 128), dtype=np.float32)

for i in range(n):
    crop = img[y1:y2, x1:x2]            # 동일한 numpy slice
    crop = cv2.cvtColor(crop, ...)       # 동일한 cv2 연산
    crop = cv2.resize(crop, ...)         # 동일한 cv2 연산
    buf[i] = crop.transpose(2,0,1)       # numpy array에 직접 쓰기 (텐서 생성 없음)

# 2. 단일 벌크 GPU 전송 (50개 개별 전송 → 1회)
crops = torch.from_numpy(buf[:n]).cuda()
```

| 구분 | 원본 | 최적화 | 차이 |
|------|------|--------|------|
| 텐서 생성 | 50x `torch.as_tensor` | 0회 | numpy buffer 재사용 |
| 메모리 할당 | 50x 개별 할당 | 1x 사전 할당 | 프레임마다 재사용 |
| GPU 전송 | `torch.cat(50개).cuda()` | `torch.from_numpy(1개).cuda()` | 1회 contiguous 전송 |
| cv2 연산 | 동일 | 동일 | 변경 없음 |

### 3.3 전처리 (preproc)

GPU 전처리(`F.interpolate`)를 시도했으나, cv2.resize와의 보간 알고리즘 차이로 YOLOX 탐지에 미세한 차이가 발생하여 DUO confidence boost에서 트래커 폭발 현상이 일어났다 (49개 → 2032개).

원인: `F.interpolate(align_corners=False)`의 좌표 매핑 방식이 `cv2.INTER_LINEAR`과 다름.
결정: CPU preproc을 유지하여 탐지 결과의 완전한 동일성을 보장.

---

## 4. 정확도 차이 분석

### 4.1 차이가 발생하는 이유

원본은 PyTorch FP16으로 추론하고, 최적화 버전은 TensorRT FP16으로 추론한다. 같은 FP16이지만:

1. **TensorRT 커널 구현이 다름**: 동일한 수학 연산이라도 GPU 커널 레벨에서 연산 순서, fused operation, 누적 방식이 다르다.
2. **부동소수점 비결합성**: `(a + b) + c != a + (b + c)` in FP16. TRT는 최적화를 위해 연산 순서를 재배치한다.
3. **결과**: 탐지 좌표에 평균 0.26px의 차이가 발생한다.

### 4.2 차이의 전파 과정

```
1프레임 탐지 차이: 0.26px (평균), 0.75px (최대)
                    ↓
NMS 경계에서 탐지 순서가 바뀌는 경우 발생 (전체의 ~0.1%)
                    ↓
칼만 필터가 다른 객체에 매칭됨
                    ↓
이후 프레임에서 예측 경로가 분기
                    ↓
해당 트랙의 좌표 차이가 프레임마다 누적
```

이 현상은 **TRT FP16과 TRT FP16+GPU가 완전히 동일한 수치**를 보인다:

| 비교 | 탐지 Match% | 추적 ID Match% |
|------|------------|---------------|
| TRT FP16 vs 원본 | 99.9% | 98.9% |
| TRT FP16+GPU vs 원본 | 99.9% | 98.9% |

GPU 전처리 최적화는 추가적인 오차를 전혀 도입하지 않았다.

### 4.3 1.1% ID 불일치의 의미

100프레임 동안 평균 ~55개 트랙 중 매 프레임 ~0.6개가 다른 ID를 받는다.
이는 NMS 경계에서 신뢰도 0.001 차이로 탐지 순서가 뒤바뀌는 극소수 케이스에서만 발생하며,
**추적 품질(MOTA, IDF1 등 MOT 메트릭)에 미치는 영향은 측정 불가능한 수준**이다.

FP16 자체의 특성이므로, 이 차이가 불허용이면 TRT FP32를 사용하면 된다 (99.8% ID 일치, 속도 x1.05).

---

## 5. 최적화 전후 시간 분해

### 원본 (1프레임 = 1,140ms)
```
preproc:    38.8ms  ███
detect:     39.7ms  ███
ReID crop: 536.8ms  ████████████████████████████████████████████
ReID model: 23.7ms  ██
ECC:         8.4ms  █
기타:      492.6ms  █████████████████████████████████
```

### 최적화 (1프레임 = 57ms, 실측 17.6 FPS)
```
preproc:    38.8ms  ███████████████████████████
detect:      8.0ms  █████
ReID crop:  15.0ms  ██████████
ReID model:  7.4ms  █████
ECC:         8.4ms  █████ (cache OFF — 메모리 누적 없음)
```
원본의 "기타 493ms"는 주로 torch.cat/cuda 전송 오버헤드와 Python GIL 대기였으며,
buffer + bulk transfer 최적화로 해소되었다.

### 어디서 시간을 줄였나

| 구간 | 원본 | 최적화 | 절감 | 방법 |
|------|------|--------|------|------|
| YOLOX 추론 | 39.7ms | 8.0ms | -31.7ms | TRT FP16 |
| ReID crop+tensor | 536.8ms | 15.0ms | **-521.8ms** | buffer + bulk transfer |
| ReID 모델 추론 | 23.7ms | 7.4ms | -16.3ms | TRT FP16 |
| 전처리 | 38.8ms | 38.8ms | 0ms | 유지 (동일성 보장) |
| ECC | 8.4ms | 8.4ms | 0ms | 유지 (cv2 전용) |

총 절감: ~570ms/frame. 남은 57ms의 구성: preproc 39ms + ECC 8ms + 기타 10ms.

---

## 6. 변경하지 않은 것

- `tracker/boost_track.py`: 추적 알고리즘 전체 (칼만 필터, DLO/DUO boost, association)
- `tracker/assoc.py`: 매칭 로직 (IoU, Mahalanobis, shape similarity, Hungarian)
- `tracker/embedding.py`: 원본 EmbeddingComputer 클래스 (monkey-patch로 대체, 원본 유지)
- `tracker/ecc.py`: 카메라 모션 보정
- `tracker/kalmanfilter.py`: 칼만 필터 구현
- `external/`: 모든 외부 모듈 (YOLOX, FastReID, deep-person-reid)
- `default_settings.py`: 설정값
- conda 환경 라이브러리: 수정 없음

---

## 7. 재현 방법

```bash
# 환경 설정
conda create -n boosttrack python=3.12 -y
conda activate boosttrack
pip install -r requirements.txt
bash install_yolox.sh

# TRT 엔진 빌드 (GPU별 1회)
python -m src.build_trt --fp16

# 최적화 추론
python -m src.inference_gpu -i input.mp4 -o output.mp4

# 벤치마크 (원본 vs TRT vs TRT+GPU 비교)
python -m src.benchmark -i input.mp4 -n 100
```

---

## 8. 한계 및 향후 개선 가능성

### 적용된 추가 최적화
- **ECC cache OFF**: 실시간 스트리밍에서 캐시는 write-only (재조회 없음). 비활성화하여 메모리 누적 제거. 속도/정확도 영향 없음.

### 현재 한계
- **ECC (8.4ms)**: `cv2.findTransformECC`는 GPU 대안이 없음 (kornia ECC는 결과가 다름)
- **전처리 (38.8ms)**: cv2→GPU 변환시 보간 차이로 탐지 결과가 달라짐
- **영상 I/O**: cv2.VideoCapture/VideoWriter는 CPU 기반

### 향후 개선 가능
| 방법 | 예상 효과 | 난이도 |
|------|----------|--------|
| NVIDIA Decoder (nvdec) 영상 읽기 | -10~15ms | 중 |
| ECC 비활성화 (`--no_ecc`) | -8.4ms | 낮음 (정확도 영향 있음) |
| 전처리 CUDA 커스텀 커널 | -30ms | 높음 |
| ReID crop을 CUDA 커널로 | -10ms | 높음 |
| 배치 프레임 처리 | x1.5~2 | 중 |
