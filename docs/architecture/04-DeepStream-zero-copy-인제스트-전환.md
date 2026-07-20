# DeepStream zero-copy 인제스트 전환 (ADR)

- 작성일: 2026-07-19
- 상태: **채택** — `INGEST_BACKEND` 스위치로 기존 경로와 병행 (기본값 ffmpeg = 기존 동작)
- 관련: [01-기술스택-결정.md](01-기술스택-결정.md) L1("장기 DeepStream — PoC 후") 후속 ·
  구현 [system/ingest_ds/](../../system/ingest_ds/README.md) ·
  실측 [DeepStream-한계처리량-실측](../reports/DeepStream-한계처리량-실측.md) ·
  [DeepStream-전환-유사도-검증](../reports/DeepStream-전환-유사도-검증.md)

## 1. 배경 — 기존 ffmpeg 경로의 한계

기존 경로(`system/ingest` + `system/tracking`)는 카메라별 ffmpeg-NVDEC 디코드
→ **풀해상도 BGR을 CPU로 복사** → 호스트 파이썬 단일 스레드가 프레임 1장씩
직렬 TRT 추론하는 구조다. 라이브 RTSP 스윕 실측(P1,
[bench/results_e2e_limit_ffmpeg_gpu1.json](../reports/bench/results_e2e_limit_ffmpeg_gpu1.json)) 결과:

- **4ch@5fps가 한계** — 6ch부터 채널당 fps가 목표 미달(6ch 4.21 → 16ch 1.56)
- 총 처리량이 **~21fps에서 포화** — 병목은 GPU가 아니라 "프레임당 PCIe 복사
  + 전처리 + 배치 없는 직렬 추론"의 호스트 단가

시스템 목표는 16ch@5fps(=80fps)이므로 기존 구조로는 GPU를 늘려도 도달 불가.

## 2. 결정

**DeepStream 9.0 기반 zero-copy 인제스트+배치 추론 워커**(`system/ingest_ds/`)를
도입하고, `system/api/server.py`에 **`INGEST_BACKEND` 환경변수 스위치**
(기본 `ffmpeg`)로 두 경로를 병행한다.

```
[컨테이너 macs-deepstream:9.0 — GPU당 1개]              [호스트 conda]
nvurisrcbin ×N → nvstreammux(batch) → RGBA(NVMM)
  → zero-copy(cupy→torch) → analyze_fps 게이트
  → YOLOX TRT(dynamic batch) + 카메라별 BoostTrack     server.py
  → TrackedObject 메타만 ZMQ PUSH ──────────────→ bridge → MetricsEngine.on_tracks
```

- 디코드~트래킹까지 컨테이너 안에서 GPU 상주로 처리 — **프레임 픽셀이
  컨테이너 밖으로 나가지 않는다** (ZMQ에는 트랙 메타만).
- 멀티 GPU는 launcher가 GPU별로 워커를 분할(analyze_fps 합 greedy 균등),
  호스트 bridge 1개가 통합 수신 — GPU 1/2/N장 동일 코드.
- `WORKERS_PER_GPU`(기본 1)로 GPU당 워커 프로세스 수를 늘릴 수 있다(P8 구현).
  단 P9 재실측 결과 5fps 한계(16ch)는 분할과 무관하게 동일하고, 과부하
  영역(24ch+)에서만 총 처리량 +25~36% — 병목이 GPU가 아니라 프레임당
  직렬 단가라서다(§6). **운영 기본값은 1 권장.**
- 기존 코드는 수정 없이 유지 — 스위치를 안 건드리면 100% 기존 동작.

## 3. 실측 근거

### 처리량 (GPU1 단독, [한계 실측](../reports/DeepStream-한계처리량-실측.md))

| 경로 | 한계(채널당 5fps 유지) | 총 처리량 정점 |
|------|----------------------|---------------|
| 기존 ffmpeg | **4ch** | ~21fps 포화 |
| DeepStream | **16ch** (4.93fps/ch, 12ch는 드랍 0) | **78.7fps** (약 4배) |

이후 저하 곡선은 24ch≈3.2 → 32ch≈2.4 → 64ch≈0.8fps로 완만하며, oldest-drop
큐 덕에 지연은 폭주하지 않는다(lag p95 0.5~0.9s). 64ch에서도 OOM·에러 0건.
1GPU 병목은 GPU 하드웨어(SM 39~54%, NVDEC ≤9%)가 아니라 **워커 파이썬 프로세스
1개의 직렬 처리 능력**(~12.7ms/frame) — 추가 스케일은 GPU 증설(=워커 증설)로 해결.

### 출력 동등성 ([유사도 검증](../reports/DeepStream-전환-유사도-검증.md), 646프레임 정렬 비교)

- 검출 bbox 매칭률 **99.44%**(IoU mean 0.976) · 트랙 bbox 매칭률 **98.95%**(IoU 0.975)
- 트랙 수 99 vs 96, 수명 분포 동등 — **채택 가능 판정**
- ReID cosine 0.956(기준 0.98 미달)은 디코더+색변환 픽셀 차이로 원인 특정,
  타인 간 cosine 0.226 대비 판별 마진이 커서 트랙 산출에 영향 없음

### 통합 검증 (P7, 2026-07-19 — :8902 임시 서버, SITE_ID=ds-verify)

- `INGEST_BACKEND=deepstream`으로 기동 → API로 4ch 등록 → 전 채널 5fps
  drop 0, `/api/status`·`/api/map/stream` SSE가 기존과 동일 스키마로 흐름
  (운영뷰 스크린샷: [reports/img/deepstream-운영뷰-4ch-검증.png](../reports/img/deepstream-운영뷰-4ch-검증.png))
- 스냅샷(`/api/cameras/{id}/test`)은 ffmpeg 단발 캡처 폴백으로 정상 동작
- hot-remove 후 워커 자동 재기동 → 잔여 2ch 5fps 유지
- `INGEST_BACKEND=ffmpeg` 재기동(롤백) → 기존 경로 2ch@5fps 정상, 회귀 없음

## 4. 제약 (운영 시 주의)

| 제약 | 내용 · 대응 |
|------|------------|
| **TRT 버전 결합** | 컨테이너 TRT 10.14 ≠ 호스트 10.16 — 엔진은 `external/weights/trt_ds/`에 **컨테이너 trtexec로 별도 빌드**(빌드법: [system/ingest_ds/README.md](../../system/ingest_ds/README.md)). 이미지 업그레이드 시 재빌드 필수 |
| **스냅샷 미지원** | 픽셀이 컨테이너 밖으로 안 나옴 → `get_snapshot()`은 항상 None. server.py `grab_frame`이 ffmpeg(cv2) 단발 캡처로 폴백 — 셋업 UI 스냅샷은 그대로 동작 |
| **hot add/remove = 워커 재시작** | 카메라 추가/제거/수정 시 해당 GPU 워커 컨테이너를 재시작(엔진 로드 포함 **~50초 공백**, 다른 GPU 워커는 무영향). DS 동적 소스 add/remove는 다음 단계 |
| **GPU 공유 간섭** | 실측은 vLLM이 GPU1 메모리 84GB 상주(유휴) 조건 — 타 워크로드가 SM을 쓰면 처리량 저하. 16ch 목표는 GPU 전유 전제 |
| **mux stretch 왜곡** | nvstreammux가 전 소스를 1920×1080으로 stretch — 4:3 소스는 가로 왜곡(검출 영향 경미). 좌표는 원본 px로 역스케일해 계약 준수 |
| **선행 조건** | docker + `macs-deepstream:9.0` 이미지(43.5GB) + `trt_ds/` 엔진 + 호스트 `pyzmq`. GPU_DEVICES 기본값이 갈림 — ffmpeg `"0,1"` / deepstream `"1"` |

## 5. 사용법 · 롤백

```bash
# DeepStream 경로로 전환
INGEST_BACKEND=deepstream GPU_DEVICES=1 pm2 restart macs-system --update-env

# 롤백 1 — 환경변수 (권장, 즉시): 기본값이 ffmpeg이므로 제거만으로 복귀
INGEST_BACKEND=ffmpeg pm2 restart macs-system --update-env

# 롤백 2 — git 레벨 (코드 자체를 되돌릴 때)
git checkout pre-deepstream-baseline   # 태그: DS 작업 이전 마지막 커밋(b705750)
# DS 잔여물 정리(컨테이너)
conda run -n boosttrack python -m system.ingest_ds.launcher --stop
```

`INGEST_BACKEND` 미지정(=ffmpeg)이면 DS 관련 import 자체가 일어나지 않아
docker·pyzmq 없는 환경에서도 기존과 동일하게 동작한다.

## 6. 추기 (2026-07-20, P11) — b32 dynamic 엔진 검토: 기각

1GPU 한계(16ch@5fps) 돌파 후보로 b32 dynamic 엔진(min1/opt32/max32)을
빌드·재스윕했다. **운영 채택하지 않는다 — 검출 엔진 기본값은 b16 유지.**

- **가설**(한계 실측 §7.4): 배치 고정비가 지배(b8≈b16 벽시계)이므로 b32로
  프레임 단가 절반 → 이론 ~29ch@5fps.
- **실측 기각**: 순수 커널은 배치에 선형(4.6~4.9ms/장 — 고정비 없음)이고,
  e2e 프레임 단가는 배치를 키울수록 오히려 증가(12.6ms@b16 → 15.8ms@b32),
  32ch 총 처리량 74.8 → 63.5fps(−15%). 5fps 한계는 16ch로 불변, 1fps
  도달점은 ~54ch. 근거·원자료: [DeepStream-한계처리량-실측 §9](../reports/DeepStream-한계처리량-실측.md).
- **병목 최종 진단**: GPU 커널이 아니라 **프레임당 직렬 단가 ~12.6ms**
  (검출 커널 4.9 + 트래킹 CPU·ReID·동기화 ~7.7). 다음 레버는 트래킹 CPU
  절감·검출 해상도 축소·콜백 경량화·양자화 순.
- **남는 것**: 배치 상한 인자화(워커가 엔진 프로파일에서 자동 클램프,
  `DS_ENGINE_MAX_BATCH`·`DS_DET_ENGINE` 환경변수), b32 엔진 빌드 스크립트와
  배치 프로파일 도구 — 후속 실험 재현용
  ([system/ingest_ds/README §엔진 빌드 가이드](../../system/ingest_ds/README.md)).
