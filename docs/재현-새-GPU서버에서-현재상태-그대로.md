# 새 GPU 서버/노트북에서 현재 상태 그대로 재현하기

`git clone` + **HF 토큰**으로 지금 이 서버(:8900 멀티카메라 시스템 · :8910 CAD 편집기 ·
건물 3개/층 10개 · 리허설 패키지 4개)와 같은 상태를 만드는 절차. 위에서 아래로 그대로 따라가면 된다.

> **재현 범위(2026-11-24 기준).** 재현·검증 대상은 **:8900 과 :8910** 이다.
> 단일영상 `webui/`(:8000)·`src/` 추론 코드는 레포에 있지만 재현 검증 대상이 아니다.
> 추론 프로파일은 `yolox_fastreid` · `yolo26_clipreid` **두 개**다(RF-DETR·RT-DETRv4 는 제거됨).
> 진행 중 동작 확인은 `python tools/smoke_ports.py` (8단계).

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

conda create -n boosttrack python=3.12 -y
conda activate boosttrack
pip install -r requirements.lock.txt           # 운영 서버와 같은 버전(정확히 고정)
bash install_yolox.sh                          # yolox 는 빌드 때 torch 가 필요해 따로
```

## 2. HF 토큰

토큰은 **2개**다. 계정이 달라 서로의 레포를 못 본다.

| 변수 | 계정 | 쓰임 |
|---|---|---|
| `HF_TOKEN` | `backseollgi` | **필수.** 비공개 `backseollgi/MCMOT` — 모델·ONNX·CAD·영상·세션·매뉴얼 전부 |
| `HF_TOKEN_PIA` | `PIA-SPACE` | 선택. 촬영 원본 데이터셋 `PIA-SPACE/C-lab*` 를 다시 받을 때만 |

```bash
cp .env.example .env && $EDITOR .env           # 두 값을 채운다. 절대 커밋 금지
```

`tools/fetch_assets.sh` 가 `.env` 를 자동으로 읽는다. `hf auth login`(backseollgi)으로 저장해도 된다.

## 3. 대용량 자산 내려받기

git 에는 없는 것들(가중치·ONNX·CAD 원본·현장 영상)을 한 번에 받는다.

```bash
bash tools/fetch_assets.sh              # 가중치 + ONNX + CAD 원본
bash tools/fetch_assets.sh --field      # 현장 훈련영상 송출본 (294MB, 9채널)
bash tools/fetch_assets.sh --manual     # 사용 가이드 docx·pdf (선택)
bash tools/fetch_assets.sh --deid       # 비식별(tools/deid) 모델 3개 (선택)
bash tools/fetch_assets.sh --seeds      # git 에서 뺀 과거 seed 버전 13개 (선택)
```

### HF `backseollgi/MCMOT` 에 있는 것 (한 곳에서 관리)

| HF 경로 | 내용 | 받는 법 |
|---|---|---|
| `weights/` | ReID·검출 가중치(`mot20_sbs_S50.pth` · `bytetrack_x_mot20.tar`) | 기본 실행 / import 시 자동 |
| `onnx/` | TRT 엔진 원천 ONNX 4개(YOLOX·FastReID·YOLO26-L·CLIP-ReID) | 기본 실행 |
| `deid/` | 비식별 모델 3개 | `--deid` |
| `cad/` | 도면 원본·추출물 | 기본 실행 |
| `media/vsource/` | 리허설 패키지 4개 영상(약 43GB) | `--rehearsal` |
| `field/encoded/` · `field/raw/` | 현장 훈련영상 송출본 · 원본 avi | `--field` |
| `videos/` | RTSP 테스트 영상 12개 | `tools/rtsp/setup_rtsp_streams.sh` |
| `data/sites/default/sessions/` | 세션 녹화본 전체(2026-11-24 스냅샷, 10GB) | 아래 "세션 녹화본" |
| `data/seed_versions/` | 과거 seed 버전 | `--seeds` |
| `results/` | 발표·시각화 산출물, MOT GT | 아래 "세션 녹화본" 과 같은 방식 |
| `manual/` | 사용 가이드 | `--manual` |

**다른 곳에서 다시 받을 수 있는 것**(HF MCMOT 에 없어도 됨): DeepStream 베이스 이미지(NGC),
pip 패키지(PyPI·PyTorch·NVIDIA 인덱스), 촬영 원본(`PIA-SPACE/C-lab` · `C-lab-AIHub` ·
`C-lab-AIHub-2026-10-22`). **TRT 엔진은 어디에도 두지 않는다** — GPU 종속이라 4단계에서 빌드한다.

받아지는 것:

```
external/weights/mot20_sbs_S50.pth          ReID 가중치       (MCMOT/weights — 실패 시 공개 개별 레포 폴백)
external/weights/bytetrack_x_mot20.tar      검출 가중치       (〃)
external/weights/trt/*.onnx                 TRT 엔진 원천     (MCMOT, 토큰 필요)
cad/17F.dwg · 17F.dxf · 17F_v2.dwg · …      도면 원본         (MCMOT, 토큰 필요)
field/encoded/1F/*.mp4 · 16F/*.mp4          현장 RTSP 송출본  (MCMOT, 토큰 필요)
```

### 도면 파일 ↔ 층 대응

| 파일 | 쓰임 | 그대로 재현되나 |
|---|---|---|
| `cad/17F_v2.dwg` | **17F·16F 에 적용된 도면** (81k 요소, mm, 124.0×92.1 m) | ✅ 편집기에 올리면 같은 맵 |
| `cad/17F.dwg` · `17F.dxf` | 17F 초기본 | ✅ |
| `cad/A-101_128_각 층 평면도_최종_수정.dwg` | **1층 도면의 출처**(지하5~지상20층 통짜) | ⚠️ 아래 참조 |
| `cad/cj_cad/CJ제일제당센터_1F.dxf` | **A-101에서 추출한 지상1층 전용 벡터**(벽·문·창·기둥·계단) | ✅ 재추출 가능 |

> **1층(지상1층) 주의.** A-101 은 여러 층 평면도를 한 시트에 세로로 늘어놓은
> 도면이라 전체 범위가 **551 × 3,326 m**(종횡비 0.166)다. 반면 운영 중인 1층 맵
> `map_floor3.png` 는 **2346×1672 px · 0.039995 m/px = 93.8 × 66.9 m**(종횡비 1.403)
> — **그 시트에서 1층만 잘라낸 영역**이다.
>
> **(2026-10-14 갱신)** 과거엔 잘라낸 벡터 중간본이 레포에 없어 1층 맵 재편집이
> 불가능했으나, 이제 **A-101에서 지상1층만 잘라낸 전용 벡터 `cad/cj_cad/CJ제일제당센터_1F.dxf`**
> (83.6×70.7 m, 남서코너 원점·mm, WALL/DOOR/GLASS/COLUMN/STAIR 레이어 분리)가 확보돼 있다.
> 추출 과정·레이어 정책·재생성은 **`cad/cj_cad/README.md`** 참조, 파이프라인은 `cad/cj_cad/*.py`.
> 단 이 DXF는 A-101 원좌표계라 기존 `map_floor3.png`와 **범위·스케일이 자동 정합되지 않으므로**,
> `:8900` 편집기에 올려 **구역·출구·경로를 새로 캘리브**해야 운영 투입된다.
>
> 운영 재현만 할 거면 **완성된 맵 이미지가 git 에 있어 지장 없다**
> (`data/seed_versions/v*/map_floor3.png`, 7단계 `restore` 로 복원). 대용량(dxf·png·변환본)은
> HF `backseollgi/MCMOT/cad/cj_cad/**` → `bash tools/fetch_assets.sh --cad` 로 받는다.

### 리허설 패키지 영상 (ADR 09)
```bash
bash tools/fetch_assets.sh --rehearsal    # backseollgi/MCMOT/media/vsource/** → ./media/vsource/ (약 43GB)
```
패키지 4개가 한 번에 내려온다: `aihub/rehearsal`(5.3GB) · `aihub/drill-1f3f`(22GB) ·
`cj/rehearsal`(2.5GB) · `cj/drill-1022`(12.7GB). 패키지 정의(`rehearsal.json` — 시나리오·카메라·**매핑**)는
git 에 들어 있으므로 clone 만으로 따라온다. 영상만 HF 에서 받는다.

> `python tools/rehearsal_prep.py <패키지>` 는 점검 결과를 `rehearsal.json` 의 `prep` 에 **덮어쓴다**
> (git 변경이 생긴다). 확인만 할 거면 돌린 뒤 `git checkout -- media/vsource` 로 되돌린다.

### 세션 녹화본·결과물 (선택)
```bash
set -a; . ./.env; set +a
hf download backseollgi/MCMOT --repo-type model --include "data/sites/default/sessions/**" --local-dir .
hf download backseollgi/MCMOT --repo-type model --include "results/**" --local-dir .
```
세션은 ④ 리플레이·지표 재계산의 입력이고, `results/` 는 그 세션으로 만든 발표·시각화 영상이다.

## 4. TRT 엔진 빌드 (GPU마다 필수)

엔진은 **GPU 아키텍처·TRT 버전에 종속**이라 파일로 옮겨 쓸 수 없다. 반드시
그 서버에서 다시 빌드한다. ONNX(3단계에서 받은 것)가 원천이다.

### 4-1. 호스트 엔진 (단일영상·미리보기용)

```bash
python src/build_trt.py                 # YOLOX + FastReID → external/weights/trt/
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

### 4-3. 운영 추론 프로파일 (YOLO26-L + CLIP-ReID)

**현재 운영 서버가 쓰는 스택**이다(`:8900 [① 설정 → 추론 모델]`). 엔진이 없으면 UI에
"엔진 없음"으로 뜨고 선택되지 않으니 여기서 함께 구워 둔다.
호스트·컨테이너 엔진을 한 번에 만든다(각 1분 남짓):

```bash
bash tools/fetch_assets.sh --onnx            # 3단계에서 이미 받았으면 생략
bash tools/build_profile_engines.sh --ds     # --ds 없으면 호스트 엔진만
```

코드 기본값은 기존 스택(`yolox_fastreid`)이다. 운영 서버와 같게 하려면 UI에서
YOLO26-L + CLIP-ReID 를 고르거나 `INFER_PROFILE=yolo26_clipreid` 환경변수를 준다
(UI에서 고른 값은 `data/infer_profile.json`에 남으며 git에는 올라가지 않는다).
설계·실측 근거: `docs/architecture/07-추론-프로파일-교체구조.md`.

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
python tools/seed_version.py list           # 보관된 버전 확인
python tools/seed_version.py show v24       # 내용 미리보기
python tools/seed_version.py restore v24 --apply
```

**`v24` 가 현재 운영 구성이다**(2026-11-24 라이브 스냅샷). `data/seed/`([Reset] 대상)는 그보다
이전 상태(층 7개)라, 운영 서버와 같게 만들려면 반드시 `restore v24 --apply` 를 한다.

```
건물 cj     CJ제일제당센터      default(17F) · floor2(16F) · floor3(지상1층) · floor10(10F)
건물 aihub  AI-hub             floor4(AI hub 3층) · floor5(AI hub 1층) · floor6(3층 1차 리허설)
건물 cjpoc  최종CJ제일제당PoC   floor7(17층) · floor8(16층) · floor9(1층)   ← CJ 실훈련 1022

cam01~cam03  17F     매핑 O   (1_v1 · 2_v1 · 3_v1)
cam04~cam09  16F     매핑 O   (field_16f_*)
cam10~cam12  지상1층  미매핑·비활성 (field_1f_*)
```

| 버전 | 내용 |
|---|---|
| `v1` · `v5` | 초기 3층 구성 · 화면 통과선 도입 시점 |
| `v11` | AI hub 1층·3층 추가 |
| `v17` | 건물 개념 + 출구 카운팅 GT 14/14 세팅 |
| `v21`~`v23` | IDR 판정식 v1.13 · EPFI d_allow 조정 |
| `v24` | **현재** — 건물 3개·층 10개, CJ 실훈련 1022 포함 |

git 에 없는 과거 버전(`v2~v4` · `v6~v10` · `v12` · `v13` · `v18` · `v20`)은 `bash tools/fetch_assets.sh --seeds`.

`restore` 는 되돌리기 직전 상태를 `auto-<타임스탬프>` 로 자동 보관한다.
세부는 [data/seed_versions/README.md](../data/seed_versions/README.md).

## 7-b. 리허설 시연 (RTSP 불필요 — 파일 모드)
리허설 패키지는 **RTSP 송출 없이** 영상 파일을 직접 읽어 프레임 잠금 동기로 분석한다
(ADR 09 §11). 5절의 송출 서버가 없어도 시연된다. 사이트 seed 는 7단계의 **v24** 그대로면 된다(4개 패키지의 층이 모두 들어 있다).
:8900 → ⑤ 리허설 → 📦 CJ제일제당센터 리허설 → 시나리오 선택 → [층] 10F → **[▶ 준비 (파일)]**
→ (매핑은 패키지에 이미 저장돼 있음) → ③ 운영 뷰에서 경보 발생원 [+ 추가] → **[🎬 리허설 훈련 시작]**
→ 27~33초 재생 · 4대 지표 · 세션 결과. 호스트 GPU 에 추론 프로파일 엔진(4-3절)이 있어야 한다.

## 8. 확인

```bash
python tools/smoke_ports.py                                  # :8900·:8910 GET 경로 + RTSP 송출 병렬 확인
curl -s localhost:8900/api/floors | python -m json.tool      # 층 10개
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
| `field/infer` | 검출기 비교 산출물 — 2026-11-24 삭제(비교 종료) |
| 비식별 사본(`media/**/_deid/`) | 재생성 가능 — `tools/deid/deid_video.py` |
| 재인코딩 전 원본(`_orig_high_profile`) | 출처가 `PIA-SPACE/C-lab*` 데이터셋이라 거기서 다시 받는다 |
| RTSP 송출 스크립트(`~/rtsp-stream/`) · pm2 등록 | 레포 밖 — 5단계 스크립트가 다시 만든다 |
| 카메라 실시간 상태 | 스트림을 띄워야 `running` 이 된다(5단계) |
