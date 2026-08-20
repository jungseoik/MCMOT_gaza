# 새 GPU 서버/노트북에서 현재 상태 그대로 재현하기

`git clone` + **HF 토큰 하나**로 지금 이 서버(:8900 12채널 · 17F/16F 도면 · CAD 편집기)와
같은 상태를 만드는 절차. 위에서 아래로 그대로 따라가면 된다.

> 이 문서는 **"돌아가게 만드는 것"**이 목표다.
> OS 부트스트랩(드라이버·conda·docker·ffmpeg 설치)은
> [설치-맨서버-부트스트랩.md](설치-맨서버-부트스트랩.md) 를 먼저 끝내고 오는 걸 전제한다.

---

## 0. 전제

| 항목 | 필요 |
|---|---|
| GPU | NVIDIA + 드라이버. **TRT 엔진은 아키텍처별로 다시 빌드해야 한다** (아래 4단계) |
| conda | `boosttrack` 환경 (Python 3.12) |
| docker | DeepStream 인제스트를 쓸 때만 (`INGEST_BACKEND=deepstream`) |
| ffmpeg | RTSP 송출·인코딩 |
| node/pm2 | 서비스 상시 실행 |
| **HF 토큰** | `backseollgi/MCMOT` 이 **비공개**라 필수 |

다른 GPU 아키텍처로 옮길 땐 [이관가이드-다른-GPU-서버로.md](이관가이드-다른-GPU-서버로.md) 도 함께 볼 것.

---

## 1. 클론 + 환경

```bash
git clone https://github.com/jungseoik/MCMOT_gaza.git
cd MCMOT_gaza
git checkout feature/inference-module          # 현재 통합 브랜치

conda env create -f environment.yml            # 또는 requirements.txt
conda activate boosttrack
bash install_yolox.sh
```

## 2. HF 토큰

```bash
export HF_TOKEN=hf_xxxxxxxxxxxxxxxx            # 절대 커밋 금지
# 또는:  hf auth login
```

## 3. 대용량 자산 내려받기

git 에는 없는 것들(가중치·ONNX·CAD 원본·현장 영상)을 한 번에 받는다.

```bash
bash tools/fetch_assets.sh              # 가중치 + ONNX + CAD 원본
bash tools/fetch_assets.sh --field      # 현장 훈련영상 송출본 (294MB, 9채널)
bash tools/fetch_assets.sh --manual     # 사용 가이드 docx·pdf (선택)
```

받아지는 것:

```
external/weights/mot20_sbs_S50.pth          ReID 가중치       (공개, 토큰 불필요)
external/weights/bytetrack_x_mot20.tar      검출 가중치       (공개, 토큰 불필요)
external/weights/trt/*.onnx                 TRT 엔진 원천     (MCMOT, 토큰 필요)
cad/17F.dwg · 17F.dxf · 17F_v2.dwg · …      도면 원본         (MCMOT, 토큰 필요)
field/encoded/1F/*.mp4 · 16F/*.mp4          현장 RTSP 송출본  (MCMOT, 토큰 필요)
```

### 도면 파일 ↔ 층 대응

| 파일 | 쓰임 | 그대로 재현되나 |
|---|---|---|
| `cad/17F_v2.dwg` | **17F·16F 에 적용된 도면** (81k 요소, mm, 124.0×92.1 m) | ✅ 편집기에 올리면 같은 맵 |
| `cad/17F.dwg` · `17F.dxf` | 17F 초기본 | ✅ |
| `cad/A-101_128_각 층 평면도_최종_수정.dwg` | **1층 도면의 출처**(임시본) | ⚠️ 아래 참조 |

> **1층(지상1층) 주의.** A-101 은 여러 층 평면도를 한 시트에 세로로 늘어놓은
> 도면이라 전체 범위가 **551 × 3,326 m**(종횡비 0.166)다. 반면 운영 중인 1층 맵
> `map_floor3.png` 는 **2346×1672 px · 0.039995 m/px = 93.8 × 66.9 m**(종횡비 1.403)
> — **그 시트에서 1층만 잘라낸 영역**이다. 잘라낸 중간본은 레포에 없다.
>
> 따라서 **1층 맵을 편집기로 똑같이 다시 만드는 건 지금 불가능**하다.
> 다만 **완성된 맵 이미지는 git 에 있으므로 운영 재현에는 지장이 없다**
> (`data/seed_versions/v*/map_floor3.png`, 7단계 `restore` 로 복원).
> 1층 도면을 새로 손봐야 할 때만 A-101 에서 해당 층을 다시 잘라내면 된다.

## 4. TRT 엔진 빌드 (GPU마다 필수)

엔진은 **GPU 아키텍처·TRT 버전에 종속**이라 파일로 옮겨 쓸 수 없다. 반드시
그 서버에서 다시 빌드한다. ONNX(3단계에서 받은 것)가 원천이다.

### 4-1. 호스트 엔진 (단일영상·미리보기용)

```bash
python src/build_trt.py                 # YOLOX + FastReID → external/weights/trt/
bash tools/setup_rfdetr.sh              # RF-DETR (선택)
```

### 4-2. DeepStream 컨테이너 (다채널 운영용)

12채널 구성은 DeepStream 백엔드를 쓴다. **이미지는 43.5GB 라 배포하지 않고
레포에서 빌드**한다(GPU 1장 기준 빌드 수십 분).

```bash
docker build -t macs-deepstream:9.0 system/ingest_ds/docker
```

컨테이너 TRT(10.14.1)와 호스트 conda TRT(10.16.1)는 **서로 호환되지 않는다.**
그래서 배치 엔진은 컨테이너 안 trtexec 로 따로 빌드해 `trt_ds/` 에 둔다:

```bash
mkdir -p external/weights/trt_ds
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

9채널 이상이면 b32 엔진도 만든다(엔진당 ~1.5분):

```bash
docker run --rm --gpus device=1 -v "$PWD:/workspace" -w /workspace \
    macs-deepstream:9.0 bash docs/reports/bench/build_b32_engines.sh
cp external/weights/trt_ds/yolox_mot20_fp16_dyn_b32o32.engine \
   external/weights/trt_ds/yolox_mot20_fp16_dyn_b32.engine
```

세부·트러블슈팅은 `system/ingest_ds/README.md`.

> **docker 권한**: pm2 데몬이 docker 그룹 없이 떠 있으면 워커가
> `permission denied … docker.sock` 으로 죽는다. `tools/run_system_server.sh`
> 가 `sg docker` 로 보충하지만, `usermod -aG docker $USER` 후 재로그인이 확실하다.

## 5. RTSP 송출 띄우기 (12채널 소스)

> **현장 NVR 을 직접 붙일 거면 이 단계는 건너뛴다.** 여기서 송출하는 건
> 데모·검증용 영상이다. 현장에서는 NVR 주소를 6단계 이후 UI 에서 등록하고,
> 세션 부담 때문에 **로컬 mediamtx 허브**를 두는 것을 권한다 →
> [현장-NVR-RTSP-수집-대응계획.md](현장-NVR-RTSP-수집-대응계획.md)

**전제: mediamtx 가 떠 있어야 한다** (`:8554`). 설치·기동은
[RTSP-송출서버-구성.md](RTSP-송출서버-구성.md) 참조. 확인:

```bash
ss -tlnp | grep 8554     # 리스닝하면 OK
```

현재 구성은 **RTSP 12채널**이다. 소스는 두 묶음.

```bash
# ① 공개 테스트 영상 3채널 (1_v1 · 2_v1 · 3_v1) — 17F 에 매핑돼 있음
bash tools/rtsp/setup_rtsp_streams.sh

# ② 현장 훈련영상 9채널 — 16F 6대 + 지상1층 3대
for f in field/encoded/16F/*.mp4; do
  n=$(basename "$f" .mp4)
  pm2 start tools/rtsp/field_relay.sh --name "$n" -- "$(pwd)/$f" "$n"
done
for f in field/encoded/1F/*.mp4; do
  n=$(basename "$f" .mp4)
  pm2 start tools/rtsp/field_relay.sh --name "$n" -- "$(pwd)/$f" "$n"
done
pm2 save
```

확인:

```bash
ffprobe -v error rtsp://127.0.0.1:8554/field_16f_n   # 스트림이 잡히면 OK
```

## 6. 서비스 기동

```bash
# 멀티카메라 시스템 (:8900)
#   GPU_DEVICES 는 **그 서버의 실제 인덱스**로 바꿀 것 (nvidia-smi -L 로 확인).
#   여기 1 은 이 서버 기준이며, GPU 1장짜리 서버라면 0 이다.
INGEST_BACKEND=deepstream GPU_DEVICES=1 \
  pm2 start tools/run_system_server.sh --name macs-system

# CAD 도면 편집기 (:8910)
pm2 start "conda run --no-capture-output -n boosttrack \
  uvicorn cad.CAD_API_Based_Optimal_Evacuation_Route_Algorithm.evac.web:app \
  --host 0.0.0.0 --port 8910" --name evac-editor

pm2 save
```

`INGEST_BACKEND` 를 빼면 ffmpeg-NVDEC 경로로 돈다(채널 수 적을 때). 자세한 건 `system/README.md`.

## 7. 사이트 설정 복원 ← **이게 "그대로"의 핵심**

층·맵·카메라·매핑·구역이 전부 들어 있는 스냅샷을 통째로 되돌린다.

```bash
python tools/seed_version.py list          # 보관된 버전 확인
python tools/seed_version.py show v5       # 내용 미리보기
python tools/seed_version.py restore v5 --apply
```

`v5` 가 현재 운영 구성이다 (= `data/seed/` 와 동일. 서버 첫 기동 시 자동 복사되므로
7단계는 확인용이고, 실험 후 원복할 때 쓴다):

```
default  17F     3400x3207  0.02391 m/px (수동 2점)  경로2 구역3 병목2 출입구2
floor2   16F     2000x1887  0.03662 m/px (CAD 자동)  경로8 구역9 병목4 출입구2
floor3   지상1층  2346x1672  0.04    m/px (CAD 자동)  요소 없음

cam01~cam03  17F   매핑 O   (1_v1 · 2_v1 · 3_v1)
cam04~cam09  16F   매핑 O   (field_16f_*)
cam10~cam12  지상1층 미매핑·비활성 (field_1f_*)
유효 ROI 없음 — 대응점 컨벡스 헐이 자동으로 투영 게이트가 된다
```

| 버전 | 내용 |
|---|---|
| `v1` | 초기 시드 — 17F·19F 시드맵, 카메라 3대 |
| `v2` | 16F CAD 적용 + 현장 6채널 매핑 (17F 도 CAD 상태) |
| `v3` | 17F 는 시드 도면 3채널, 16F 는 CAD·현장 6채널 |
| `v4` | ROI 전부 제거 + 16F 병목 4·수동경로 r1~r3 |
| `v5` | **현재** — 화면 통과선 기능 도입 시점 · cam06 재매핑 |

`restore` 는 되돌리기 직전 상태를 `auto-<타임스탬프>` 로 자동 보관한다.
세부는 [data/seed_versions/README.md](../data/seed_versions/README.md).

## 8. 확인

```bash
curl -s localhost:8900/api/floors | python -m json.tool      # 층 3개
curl -s localhost:8900/api/cameras | python -c "
import json,sys
for c in json.load(sys.stdin):
    print(c['cam_id'], c['name'], c['state']['status'],
          '매핑O' if c.get('mapping') else '매핑X')"
curl -s localhost:8900/api/map/state | python -m json.tool | head -20
```

브라우저: `http://<서버IP>:8900` (접속코드 `macs`)

DS 워커 부하 확인:

```bash
docker logs macs-ds-worker-gpu1 --tail 3
# STATS fps={...} batch_avg=11.7 infer_avg=118ms qdrop=0 zmqdrop=0
```

---

## 원격(터널)으로 쓸 때 주의

VS Code SSH 포트포워딩으로 쓸 경우 **:8900 과 :8910 을 모두 포워딩**해야 한다.
:8900 만 뚫으면 화면은 뜨지만 [CAD 도면에서 만들기] 버튼이 동작하지 않는다 —
편집기 창을 `<접속주소>:8910` 로 여는데 그 포트가 안 뚫려 있기 때문.
(차단되면 화면에 대체 링크가 뜨도록 해뒀다.)

## GPU 용량 기준

실측 기준선이다. 넘기면 fps 가 떨어진다.

```
GPU 1장당 총 처리량 상한   ≈ 75~78 fps
실무 설계선               채널수 × fps ≤ 70
현재 구성                 12ch × 5fps = 60 fps  (여유 있음)
```

## 막히면 볼 곳

| 증상 | 문서 |
|---|---|
| OS 레벨(드라이버·conda·docker·ffmpeg·node) 부터 필요 | [설치-맨서버-부트스트랩.md](설치-맨서버-부트스트랩.md) |
| GPU 아키텍처가 다름 · 처리량 재측정 | [이관가이드-다른-GPU-서버로.md](이관가이드-다른-GPU-서버로.md) |
| DS 워커가 안 뜸 · 배치·엔진 문제 | `system/ingest_ds/README.md` |
| RTSP 송출·mediamtx | [RTSP-송출서버-구성.md](RTSP-송출서버-구성.md) |
| 현장 NVR(H.265·VBR·세션) | [현장-NVR-RTSP-수집-대응계획.md](현장-NVR-RTSP-수집-대응계획.md) |
| 서비스 실행·환경변수 전반 | `system/README.md` |

## 재현되지 않는 것 (알고 있을 것)

| 항목 | 이유 |
|---|---|
| TRT 엔진 | GPU 아키텍처 종속 — 4단계에서 재빌드 |
| 세션 녹화본(`data/sites/*/sessions/`) | 실행 이력이라 옮기지 않음 |
| `field/raw`, `field/infer` | 원본 avi·추론 산출물(4.9GB) — 원본 서버에만 있음. 송출엔 `encoded` 로 충분 |
| 카메라 실시간 상태 | 스트림을 띄워야 `running` 이 된다(5단계) |
