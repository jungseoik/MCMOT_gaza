# system/ingest_ds — DeepStream 인제스트+추론 워커

기존 경로(`system/ingest` + `system/tracking` — 카메라별 디코드 → 풀해상도
BGR 복사 → 직렬 TRT 추론)를 대체 가능한 **zero-copy 디코드 → 배칭 → GPU 상주
배치 추론** 경로. 기존 코드는 그대로 두고 `INGEST_BACKEND` 스위치로 병행하는
것이 전제라 이 디렉토리 밖은 수정하지 않았다.

```
[컨테이너 macs-deepstream:9.0]                                [호스트 conda]
nvurisrcbin ×N ─ queue(leaky) ─ nvstreammux(batch)
  → nvvideoconvert → RGBA(NVMM, unified) → appsink
  → (zero-copy cupy→torch) analyze_fps 게이트 + GPU letterbox
  → 추론 스레드: YOLOX TRT(dynamic batch) → 카메라별 BoostTrack
      (per_instance_ids, ECC off, GPU crop TRT ReID)
  → TrackedObject dict → ZMQ PUSH ──────────────→ bridge.py PULL
                                                   → on_tracks(cam_id, ts, [TrackedObject])
```

프레임 픽셀은 컨테이너 밖으로 나가지 않는다 — ZMQ에는 트랙 메타만 실린다.

## 파일

| 파일 | 역할 |
|------|------|
| `worker.py` | 컨테이너 메인 — 파이프라인·게이트·배치 추론·트래킹·ZMQ PUSH |
| `bridge.py` | 호스트 측 ZMQ PULL → `on_tracks` 콜백 어댑터 (+ `__main__` 수신 통계). 멀티 엔드포인트 통합 수신 지원 |
| `launcher.py` | **멀티 GPU 런처** — GPU별 워커 컨테이너 기동/중지 + `DsIngestManager`(기존 IngestManager+AnalyzerThread 대체 인터페이스) |
| `trt_infer.py` | TRT 엔진 래퍼(src/inference_trt.py 이식) + dynamic-batch 검출기·ReID |
| `gpu_embedding.py` | GPU 텐서에서 직접 crop하는 ReID 임베더 (GPUEmbeddingComputer의 GPU판) |
| `yolox_post.py` | yolox postprocess 벤더링 (컨테이너에 yolox 패키지 불필요) |
| `run_worker.sh` | docker run 래퍼 (`GPU=1` 기본) |
| `configs/cams_*.json` | 카메라 목록 예시 — `[{cam_id, rtsp, analyze_fps}, ...]` |
| `configs/runtime/` | launcher가 쓰는 GPU별 분할 cams JSON (자동 생성, git 제외) |
| `docker/Dockerfile` | DS 9.0 + pyds + torch cu130 + tensorrt-cu13 + cupy 등 |

## 실행

```bash
# 1) 이미지 빌드 (최초 1회)
docker build -t macs-deepstream:9.0 system/ingest_ds/docker

# 2) 엔진 빌드 (최초 1회 — 아래 '엔진 빌드' 참조)

# 3) 워커 기동 (컨테이너, GPU1)
system/ingest_ds/run_worker.sh --cams system/ingest_ds/configs/cams_4ch.json \
    --batch-size 8 --zmq-bind 'tcp://*:5701'

# 4) 호스트에서 수신 확인 (bridge 단독 통계 모드)
conda run -n boosttrack python -m system.ingest_ds.bridge --connect tcp://127.0.0.1:5701
```

호스트 통합 시에는 `TrackBridge(on_tracks=...)`를 쓰면 된다 —
`system/tracking/analyzer.py`의 `OnTracks`와 동일 시그니처.
호스트 conda 환경에는 `pyzmq`가 필요하다 (`pip install pyzmq`).

worker.py 주요 인자: `--batch-size`(추론 배치 상한, 엔진 max 16) ·
`--gather-ms`(배치 모으기 대기, 기본 100ms) · `--det-thresh`(기본 0.4) ·
`--codec json|msgpack` · `--copy-mode`(zero-copy 끄고 CPU 복사 강제) ·
`--duration N`(N초 후 자동 종료, 테스트용).

검증 전용 인자(기본 비활성 — 기존 동작 불변): `--verify-dump DIR`(프레임별
검출/임베딩/트랙 npz 덤프) · `--lossless`(drop 없는 backpressure + EOS 자동
종료, **file:// 소스 전용**) · `--dump-frames N`(mux RGBA 프레임 npy 덤프) ·
`--max-age N`(트래커 max_age 강제). 사용법과 결과는
`docs/reports/bench/verify_ds_similarity.py` ·
`docs/reports/DeepStream-전환-유사도-검증.md` 참조.

## 멀티 GPU·멀티 워커 스케일 (launcher.py)

GPU 1장/2장/N장 환경에서 **같은 코드·설정**으로 동작한다 — (GPU, 워커)
슬롯별 워커 컨테이너 + 호스트 중앙 수신(bridge) 구조.

```bash
# 단독 실행(검증) — GPU_DEVICES × WORKERS_PER_GPU 슬롯에 카메라 자동 분할
GPU_DEVICES=1 WORKERS_PER_GPU=2 conda run -n boosttrack python -m \
    system.ingest_ds.launcher --cams system/ingest_ds/configs/cams_12ch.json
# 잔여 컨테이너 정리 (이름 프리픽스 기준 전부 — 워커 수 설정 무관)
conda run -n boosttrack python -m system.ingest_ds.launcher --stop
```

- **`WORKERS_PER_GPU`** (기본 `1` — 기존 단일 워커 동작 그대로, **DS 권장 2**):
  같은 GPU에 워커 프로세스를 N개 띄워 카메라를 나눈다. 1GPU 한계 실측
  (`docs/reports/DeepStream-한계처리량-실측.md`)에서 병목이 GPU가 아니라
  워커 프로세스 1개의 파이썬 직렬화(GIL — appsink 콜백 vs 추론 스레드
  경합)로 판정되어, 프로세스 분할로 GIL을 분리하는 옵션이다.
  워커당 엔진 메모리(~5GB)가 워커 수만큼 늘어난다.
- **분할**: 채널의 `analyze_fps` 합 기준 greedy 부하 균등 — (GPU, 워커)
  슬롯 전체에 적용. 입력이 같으면 결과도 같다(결정적) — 재시작해도 배정이
  흔들리지 않는다.
- **포트 컨벤션 (하위호환)**: 슬롯 (K, j)의 워커는
  `tcp://*:{5701 + K + 100*j}`에 PUSH, bridge PULL 소켓 1개가 전 엔드포인트를
  fair-queuing으로 통합 수신. **j=0 포트는 기존 단일 워커 포트(5701+K)와
  항상 같다** — 기존 문서·스크립트의 `--connect tcp://127.0.0.1:570(1+K)`가
  그대로 유효. (예: GPU1 2분할 → w0=5702, w1=5802)
- **컨테이너**: `WORKERS_PER_GPU=1`이면 기존과 동일한 `macs-ds-worker-gpu{K}`,
  ≥2면 `macs-ds-worker-gpu{K}-w{j}` (`docker run -d --rm --gpus device=K
  --network host`). 슬롯별 cams JSON은 `configs/runtime/`에 자동 생성.
- **batch-size**: 미지정 시 슬롯 담당 채널 수 기준 `min(16, N_slot)` —
  분할하면 워커별 배치가 자연히 작아진다.
- **`DsIngestManager`**: 기존 `IngestManager`+`AnalyzerThread` 조합과 동일한
  외부 인터페이스(`start(cams)/stop()/states()/add·remove·update_camera/
  set_enabled` + `on_tracks(cam_id, ts, tracks)` 콜백) — server.py에서
  `INGEST_BACKEND=deepstream` 스위치로 교체하는 것을 전제로 한다.
  `cams`는 `CameraConfig`(pydantic)든 dict든 받는다.
- **hot add/remove(최소 구현)**: 해당 카메라가 배정된 **슬롯의 워커만**
  cams JSON 갱신 후 컨테이너 재시작 — **다른 슬롯 워커는 무영향**(docker
  StartedAt 불변 + 수신 fps 유지로 검증). 워커 재시작 비용은 엔진 로드 포함
  ~50초. DS 파이프라인 동적 소스 add/remove는 다음 단계.
- **`get_snapshot()`은 None**: 프레임 픽셀이 컨테이너 밖으로 나오지 않는
  구조라 스냅샷 미지원 — 셋업 UI가 필요하면 ffmpeg 단발 캡처를 따로 쓴다.
- **states()**: 수신 슬라이딩 윈도(10초) 기반 `fps_in`,
  `running`(최근 수신) / `reconnecting`(컨테이너 생존·수신 없음) /
  `disconnected`(컨테이너 사망) / `disabled`. 워커 내부 큐 드랍은 컨테이너
  로그(`docker logs macs-ds-worker-gpuK[-wj]`)의 STATS로 관찰.

## 엔진 빌드 (컨테이너 안에서 — TRT 버전 일치 필수)

컨테이너 TRT는 **10.14.1**, 호스트 conda TRT는 10.16.1 —
`external/weights/trt/*.engine`(호스트 빌드)은 컨테이너에서 재사용 불가라
`external/weights/trt_ds/`에 컨테이너 trtexec로 따로 빌드한다 (`*.engine`은
`.gitignore`로 커밋 제외).

```bash
# dynamic-batch YOLOX ONNX 수출 (호스트 conda — 최초 1회, 산출물이 이미 있으면 생략)
CUDA_VISIBLE_DEVICES=1 conda run -n boosttrack python docs/reports/bench/build_dynamic_yolox.py  # ONNX만 필요

# 컨테이너에서 trtexec fp16 빌드
docker run --rm --gpus device=1 -v "$PWD:/workspace" -w /workspace macs-deepstream:9.0 bash -c '
/usr/src/tensorrt/bin/trtexec --onnx=external/weights/trt/yolox_mot20_dynamic.onnx \
  --saveEngine=external/weights/trt_ds/yolox_mot20_fp16_dyn_b16.engine --fp16 \
  --minShapes=images:1x3x896x1600 --optShapes=images:8x3x896x1600 --maxShapes=images:16x3x896x1600 \
  --memPoolSize=workspace:8192M
/usr/src/tensorrt/bin/trtexec --onnx=external/weights/trt/fastreid_sbs_s50.onnx \
  --saveEngine=external/weights/trt_ds/fastreid_sbs_s50_fp16_dyn_b256.engine --fp16 \
  --minShapes=images:1x3x384x128 --optShapes=images:32x3x384x128 --maxShapes=images:256x3x384x128 \
  --memPoolSize=workspace:4096M'
```

## 제약·주의

- **TRT 버전 결합**: 엔진은 빌드한 TRT 버전에서만 로드된다. 이미지의
  `tensorrt-cu13` pip 버전(10.14.1.48)과 trtexec 버전이 같아야 하며, 이미지를
  올리면 `trt_ds/` 엔진도 재빌드해야 한다.
- **unified memory 필수**: dGPU에서 pyds로 NvBufSurface에 접근하려면
  nvstreammux·nvvideoconvert에 `nvbuf-memory-type=3`(NVBUF_MEM_CUDA_UNIFIED)이
  필요하다. zero-copy 실패 시 워커가 CPU 복사 폴백으로 자동 전환하고 통계
  로그(`gpumap=OFF(copy)`)에 표시한다.
- **GPU 선택**: GPU0은 타 프로젝트 사용 중 — 테스트는 `GPU=1`(기본값)로.
- **전처리 수치**: `dataset.preproc`(cv2 uint8 bilinear)를 GPU float 보간으로
  재현 — 보간 차이는 ±1/255 수준. 단 NVDEC+nvvideoconvert의 YUV→RGB 변환이
  ffmpeg와 채널당 ~2/255 체계적으로 다르다(실측). e2e 영향은 검출 매칭률
  99.4%·트랙 매칭률 99.0%로 동등 —
  `docs/reports/DeepStream-전환-유사도-검증.md` 참조.
- **트래커 전역 설정**: worker는 `GeneralSettings`를 mot20/ECC-off로 설정한다.
  컨테이너 프로세스 전용이라 호스트 단일영상 PoC와 충돌하지 않는다.
- **analyze_fps 게이트**: 디코드는 풀레이트로 돌고 추론만 게이트한다.
  게이트는 wall-clock 기준 등간격(기본 5fps)이다.
- **재접속**: RTSP 끊김은 nvurisrcbin 내장 `rtsp-reconnect-interval=10`(무한
  재시도)로 복구한다. 별도 워치독 없음 — 통계 로그의 카메라별 fps로 관찰.
- **좌표계**: nvstreammux가 모든 소스를 1920×1080으로 스케일(비율 무시 stretch)
  하므로 트래킹은 mux px에서 수행되고, 출력 직전에 `source_frame_width/height`로
  **원본 카메라 px로 역스케일**해 TrackedObject 계약(카메라 프레임 px)을 지킨다.
  4:3 소스는 검출 입력이 가로로 늘어나는 왜곡이 있다(검출률 영향은 경미).
