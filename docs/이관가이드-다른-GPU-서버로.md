# 서버 이관 가이드 — 다른 GPU 서버로 옮길 때 체크리스트

> **이 문서를 읽는 대상**: 이 레포를 **다른 서버로 clone해서 새로 띄우는 담당자/에이전트**.
>
> **⚠️ 가장 중요한 전제 — 현재 모든 성능·엔진 기준은 Blackwell 아키텍처에서 나온 것이다.**
> 이 레포의 실측 수치(1GPU 16ch@5fps 등)와 커밋된 설정은 전부
> **RTX PRO 6000 Blackwell (sm_120) ×2 / CUDA 13 계열** 서버에서 측정·빌드됐다.
> **GPU 아키텍처가 다른 서버로 옮기면 아래 항목들을 반드시 재수행**해야 한다 —
> 특히 **TRT 엔진 재빌드**와 **한계 처리량 재측정**은 생략 불가.
>
> 작성 2026-07-23 · 근거: `system/ingest_ds/README.md`(엔진 빌드), `docs/reports/DeepStream-한계처리량-실측.md`(성능 실측).

---

## 0. 왜 아키텍처가 바뀌면 재작업이 필요한가

TRT 엔진(`.engine`)은 빌드 환경에 **이중으로 결합**된다. 어느 한쪽이 바뀌면 재빌드다.

| 결합 | 현재(원본 서버) | 재빌드 트리거 |
|------|----------------|--------------|
| **GPU 아키텍처(SM)** | Blackwell `sm_120` | 다른 GPU 세대(예: RTX 5000 = Ada `sm_89`)로 이식 → **로드 거부/성능 상이** |
| **TRT 버전** | 호스트 conda 10.16 / 컨테이너 10.14 | TRT 버전 변경(드라이버·이미지 업그레이드 포함) |

`.engine` 파일은 **전부 `.gitignore` 대상**이라 git으로 넘어오지 않는다. **원래부터 서버마다 새로 빌드하는 게 정상 절차**다. 재빌드 원천(ONNX·가중치)과 빌드 커맨드는 아래에 있다.

---

## 1. 이관 대상 서버 사양 먼저 확인 (필수)

옮기기 전에 대상 서버에서 확인하고, 아래 값을 기록해둔다:

```bash
nvidia-smi --query-gpu=index,name,memory.total,compute_cap --format=csv
nvidia-smi | grep "CUDA Version"     # 드라이버가 지원하는 CUDA
```

체크 포인트:
- **GPU 아키텍처**: `compute_cap`가 원본(9.0=Blackwell 계열 표기는 환경마다 다름, RTX PRO 6000 Blackwell)과 다르면 → 엔진 재빌드 확정. RTX 5000 = Ada = `8.9`.
- **GPU 장수와 인덱스**: **`GPU_DEVICES`를 `0,1,2,3`으로 가정하지 말 것.** 서버마다 사용 가능한 GPU 인덱스가 다르다(일부는 타 프로젝트 점유일 수 있음). `nvidia-smi`로 **실제로 비어 있는 인덱스**를 확인해 그 목록으로 설정한다. (원본 서버는 GPU0을 타 프로젝트가 써서 `GPU_DEVICES=1`로 운영 중이었다.)
- **GPU 메모리**: DS 워커는 워커당 약 5~11GB 쓴다(Blackwell 실측). 대상 GPU VRAM이 작으면(예: RTX 5000 32GB) `WORKERS_PER_GPU`·채널 수를 그에 맞춰 조정.
- **CUDA/드라이버 호환**: 현재 requirements가 `torch==2.9.0+cu130`(CUDA 13)·`tensorrt-cu12<11`이다. 대상 드라이버가 CUDA 13을 지원하는지 확인. 미지원이면 torch/TRT를 대상 CUDA에 맞는 버전으로 낮춰야 하고, **그 경우 엔진도 그 TRT로 재빌드**.

---

## 2. 이관 절차 (순서대로)

### 2-1. 코드·환경
```bash
git clone <repo> && cd MCMOT_gaza
git checkout feature/deepstream-ingest       # 또는 병합된 브랜치
conda create -n boosttrack python=3.12 && conda activate boosttrack
pip install -r requirements.txt              # ⚠️ CUDA 버전 대상 서버에 맞는지 §1 확인 후
bash install_yolox.sh
```

### 2-2. 가중치 (git 미포함 — 별도 반입)
`.gitignore`로 제외된 대용량 파일. 원본 서버 `external/weights/`에서 복사하거나 원출처에서 재다운로드:
- `external/weights/bytetrack_x_mot20.tar` (~793MB, YOLOX-MOT20 검출)
- `external/weights/mot20_sbs_S50.pth` (~337MB, FastReID)

### 2-3. ONNX 수출 (GPU 무관 — 원본에서 복사 가능)
`external/weights/trt/*.onnx`는 GPU 아키텍처와 무관하므로 원본 서버에서 그대로 복사해도 된다. 없으면 재생성:
```bash
CUDA_VISIBLE_DEVICES=<빈GPU> conda run -n boosttrack python docs/reports/bench/build_dynamic_yolox.py
# ReID ONNX는 src/build_trt.py 경로에서 생성
```
필요 파일: `yolox_mot20_dynamic.onnx`(dynamic batch), `fastreid_sbs_s50.onnx`.

### 2-4. ⭐ TRT 엔진 재빌드 (대상 GPU에서 — 아키텍처 결합, 생략 불가)
**반드시 대상 서버의 실제 GPU에서 빌드**한다(trtexec가 그 GPU의 SM으로 커널 컴파일). 상세·복붙 커맨드는 [`system/ingest_ds/README.md` "엔진 빌드 가이드"](../system/ingest_ds/README.md) 참조. 요약:

```bash
# (a) 호스트 conda용 엔진 → external/weights/trt/   (단일영상/webui 경로)
CUDA_VISIBLE_DEVICES=<빈GPU> conda run -n boosttrack python -m src.build_trt --fp16

# (b) DeepStream 컨테이너용 엔진 → external/weights/trt_ds/  (멀티카메라 경로)
docker build -t macs-deepstream:9.0 system/ingest_ds/docker    # 이미지 먼저
docker run --rm --gpus device=<빈GPU> -v "$PWD:/workspace" -w /workspace macs-deepstream:9.0 bash -c '
/usr/src/tensorrt/bin/trtexec --onnx=external/weights/trt/yolox_mot20_dynamic.onnx \
  --saveEngine=external/weights/trt_ds/yolox_mot20_fp16_dyn_b16.engine --fp16 \
  --minShapes=images:1x3x896x1600 --optShapes=images:8x3x896x1600 --maxShapes=images:16x3x896x1600 \
  --memPoolSize=workspace:8192M
/usr/src/tensorrt/bin/trtexec --onnx=external/weights/trt/fastreid_sbs_s50.onnx \
  --saveEngine=external/weights/trt_ds/fastreid_sbs_s50_fp16_dyn_b256.engine --fp16 \
  --minShapes=images:1x3x384x128 --optShapes=images:32x3x384x128 --maxShapes=images:256x3x384x128 \
  --memPoolSize=workspace:4096M'
```
> b32 엔진은 원본 서버에서 실측 후 **이득 없음으로 기각**(b16 유지). 굳이 재빌드 불필요.

### 2-5. 실행 설정
- `GPU_DEVICES`를 **§1에서 확인한 실제 빈 GPU 인덱스**로 설정 (`0,1,2,3` 가정 금지).
- `INGEST_BACKEND=deepstream`(멀티카메라) 또는 미설정(=ffmpeg, 단일/기존).
- `WORKERS_PER_GPU`는 기본 1 권장(원본 실측: 분할은 5fps 한계를 못 늘림).
- 상세: `.env.example`, `tools/run_system_server.sh`.

```bash
INGEST_BACKEND=deepstream GPU_DEVICES=<빈GPU목록> \
  conda run -n boosttrack uvicorn system.api.server:app --host 0.0.0.0 --port 8900
```

### 2-6. ⭐ 한계 처리량 재측정 (Ada 등 다른 아키텍처면 필수)
현재 "1GPU 16ch@5fps"는 **Blackwell 실측치**다. Ada(RTX 5000)는 검출 커널이 더 느릴 수 있어 **GPU당 채널 수용량이 다르다.** 대상 서버에서 재측정:
```bash
CUDA_VISIBLE_DEVICES=<빈GPU> conda run -n boosttrack python \
  docs/reports/bench/bench_ds_limit.py --tag rtx5000_gpuX --channels 8,12,16,20,24
```
결과로 **대상 서버의 5fps 유지 최대 채널 수**를 확정하고, 50~60채널 목표 대비 필요한 GPU 장수를 산정한다. (원본: 50~60ch = Blackwell GPU 3~4장. Ada는 재측정 결과로 판단 — 더 필요할 수 있음.)

### 2-6b. RTSP 테스트 소스 구성 (MACS 입력 = RTSP)
MACS는 RTSP 입력으로 동작하므로 테스트하려면 송출 소스가 필요하다. 테스트 3채널 영상은
HuggingFace 데이터셋(`backseollgi/mot_dataset`)에 있고, 스크립트로 재현한다:
```bash
# hf CLI 필요(pip install -U huggingface_hub). 비공개면 토큰 환경변수로만(커밋 금지)
HF_TOKEN=hf_xxx bash tools/rtsp/setup_rtsp_streams.sh
# → rtsp://<이서버IP>:8554/{1_v1,2_v1,3_v1} 송출 → MACS 카메라 등록 주소로 사용
```
> 인코딩 조건·수동 절차·새 영상 추가는 [RTSP 송출 서버 구성](RTSP-송출서버-구성.md) 참조.
> **주의**: 시드(`data/seed/default`)의 카메라 RTSP 주소가 **송출 서버 IP**를 가리키는지 확인
> (다른 IP면 UI에서 카메라 주소 수정). mediamtx가 MACS와 다른 호스트면 IP를 그 호스트로.

### 2-7. 검증
```bash
conda run -n boosttrack python -m pytest tests/system -q   # GPU 무관 도메인 테스트(85 passed 기대, 기존 실패 1건 제외)
curl -s localhost:8900/api/status                          # 기동 확인
```
- webui(:8900) 접속 → 카메라 등록·매핑·운영뷰 확인.
- 다중 도면(N층) 쓰면 도면 등록·층 전환 동작 확인. (시드에 17F/19F 2층·매핑·구역·통과선 포함 — clone 후 자동 복원)

---

## 3. 이관 시 흔한 함정

| 증상 | 원인 | 조치 |
|------|------|------|
| 엔진 로드 실패/`serialization` 에러 | 다른 GPU 아키텍처의 엔진을 씀 | §2-4 대상 GPU에서 재빌드 |
| 엔진 로드 실패/TRT 버전 에러 | 빌드 TRT ≠ 런타임 TRT | 런타임과 같은 TRT로 재빌드(컨테이너는 이미지 TRT 고정) |
| 채널당 fps가 16ch에서 5 못 미침 | Ada가 Blackwell보다 느림 | §2-6 재측정 → 채널 수/GPU 장수 재산정 |
| OOM | 대상 GPU VRAM이 작음(RTX 5000 32GB) | 채널 수↓ 또는 `WORKERS_PER_GPU`·배치 조정 |
| 카메라가 특정 GPU에만 몰림 | `GPU_DEVICES` 누락/오설정 | 실제 빈 GPU 인덱스 전부 나열 |
| CUDA init segfault (GPU 없는 환경) | GPU/드라이버 자체 부재 | 실 GPU 서버에서 실행(개발용 GPU-less는 미지원) |
| pm2 워커 docker 권한 오류 | pm2 데몬에 docker 그룹 없음 | `tools/run_system_server.sh`의 `sg docker` 경로 참고 |

---

## 4. 참고 문서
- **엔진 빌드 상세**: [`system/ingest_ds/README.md`](../system/ingest_ds/README.md) (TRT/아키텍처 결합, 배치 프로파일, 파일명 규칙)
- **성능 실측(Blackwell 기준)**: [`docs/reports/DeepStream-한계처리량-실측.md`](reports/DeepStream-한계처리량-실측.md)
- **아키텍처 결정**: [`docs/architecture/04-DeepStream-zero-copy-인제스트-전환.md`](architecture/04-DeepStream-zero-copy-인제스트-전환.md)
- **환경변수**: `.env.example` · **실행**: `tools/run_system_server.sh`, `system/README.md`
