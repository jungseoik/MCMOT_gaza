#!/usr/bin/env bash
# RT-DETRv4-S(사람/머리 2클래스, CrowdHuman FT) 검출기 셋업 — 가중치부터 TRT fp16 엔진까지.
# 결과: external/weights/trt/rtdetrv4s_person_fp16.engine  (프로파일 rtdetrv4_clipreid 가 사용)
#
# 설계는 tools/setup_rfdetr.sh 와 동일한 원칙:
#   ① ONNX export 만 격리 venv(third_party/.venv-rtdetrv4)에서 — RT-DETRv4 의존성이
#      boosttrack 환경(torch 2.9+cu130 / TRT 10.16)과 충돌할 수 있다.
#   ② 실제 추론은 본 환경 + 엔진 + src/rtdetrv4_trt.py 만으로 (라이브러리 불필요).
#   ③ 엔진은 추론과 같은 TRT 10.16(boosttrack)으로 빌드해 버전 정합.
#
# 가중치 출처: HF Awiros/person_and_head_detection (RT-DETRv4-S + C-RADIOv4 distill,
#   CrowdHuman visible-person mAP 84.10% — 같은 표에서 YOLO26-S 81.63%).
#   C-RADIO 교사는 **학습 때만** 쓰이므로 추론 비용은 순정 RT-DETRv4-S 와 같다.
#
# 사용: bash tools/setup_rtdetrv4.sh
set -euo pipefail
cd "$(dirname "$0")/.."

BOOST_PY="${BOOST_PY:-$HOME/miniconda3/envs/boosttrack/bin/python}"
VENV=third_party/.venv-rtdetrv4
REPO=third_party/RT-DETRv4
ONNX=external/weights/onnx/rtdetrv4s_person.onnx
ENGINE=external/weights/trt/rtdetrv4s_person_fp16.engine
CKPT=third_party/rtdetrv4s_person_head.pth
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
mkdir -p third_party external/weights/onnx external/weights/trt

# 1) 공식 레포 --------------------------------------------------------------
if [ ! -d "$REPO/.git" ]; then
  echo "[rtdetrv4] RT-DETRv4 clone…"
  git clone -q --depth 1 https://github.com/RT-DETRs/RT-DETRv4 "$REPO"
fi

# 2) 격리 venv (ONNX 없을 때만) ---------------------------------------------
if [ ! -f "$ONNX" ] && [ ! -x "$VENV/bin/python" ]; then
  echo "[rtdetrv4] 격리 venv 생성…"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -qU pip
  "$VENV/bin/pip" install -q torch torchvision safetensors onnx onnxsim \
      pyyaml pillow numpy opencv-python-headless calflops transformers tensorboard scipy faster-coco-eval onnxscript onnxruntime
fi

# 3) 가중치 다운로드 + .pth 변환 ---------------------------------------------
if [ ! -f "$ONNX" ] && [ ! -f "$CKPT" ]; then
  echo "[rtdetrv4] CrowdHuman FT 가중치 다운로드…"
  "$BOOST_PY" - "$CKPT" <<'PY'
import os, sys, torch
from huggingface_hub import hf_hub_download
from safetensors.torch import load_file
p = hf_hub_download("Awiros/person_and_head_detection",
                    "ckpts/rt-detrv4s-cradiov4-so400m/best_stg2.safetensors",
                    token=os.environ.get("HF_TOKEN"))
sd = load_file(p)
# 공식 export_onnx.py 는 checkpoint['ema']['module'] 또는 ['model'] 을 기대한다
torch.save({"model": sd}, sys.argv[1])
print(f"[rtdetrv4] 변환 완료 — 텐서 {len(sd)}개 → {sys.argv[1]}")
PY
fi

# 4) 2클래스 추론 config (Awiros README 와 동일 구성) -------------------------
CFG=third_party/rtdetrv4s_person_head.yml
if [ ! -f "$ONNX" ]; then
  BASE="$(cd "$REPO" && pwd)/configs/rtv4/rtv4_hgnetv2_s_coco.yml"
  cat > "$CFG" <<YML
__include__: [
  '$BASE'
]

num_classes: 2
remap_mscoco_category: False

HGNetv2:
  pretrained: False

HybridEncoder:
  distill_teacher_dim: 1152
YML
  echo "[rtdetrv4] config 생성: $CFG"
fi

# 5) ONNX export ------------------------------------------------------------
if [ ! -f "$ONNX" ]; then
  echo "[rtdetrv4] ONNX export…"
  ( cd "$REPO" && "../../$VENV/bin/python" tools/deployment/export_onnx.py \
      -c "../../$CFG" -r "../../$CKPT" --check )
  mv "$(dirname "$CKPT")/$(basename "${CKPT%.pth}").onnx" "$ONNX" 2>/dev/null \
    || mv "${CKPT%.pth}.onnx" "$ONNX"
  echo "[rtdetrv4] ONNX: $ONNX"
fi

# 6) TRT fp16 엔진 (추론과 같은 TRT 로) ---------------------------------------
if [ ! -f "$ENGINE" ]; then
  echo "[rtdetrv4] TRT fp16 엔진 빌드…"
  "$BOOST_PY" - "$ONNX" "$ENGINE" <<'PY'
import sys, tensorrt as trt
onnx_path, eng_path = sys.argv[1], sys.argv[2]
L = trt.Logger(trt.Logger.WARNING)
b = trt.Builder(L); n = b.create_network(0); p = trt.OnnxParser(n, L)
with open(onnx_path, "rb") as f:
    if not p.parse(f.read()):
        for i in range(p.num_errors): print(p.get_error(i))
        raise SystemExit("ONNX 파싱 실패")
c = b.create_builder_config()
c.set_flag(trt.BuilderFlag.FP16)
c.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 4 << 30)
prof = b.create_optimization_profile()
prof.set_shape("images", (1,3,640,640), (1,3,640,640), (1,3,640,640))
prof.set_shape("orig_target_sizes", (1,2), (1,2), (1,2))
c.add_optimization_profile(prof)
eng = b.build_serialized_network(n, c)
if eng is None: raise SystemExit("엔진 빌드 실패")
open(eng_path, "wb").write(eng)
print("[rtdetrv4] 엔진:", eng_path)
PY
fi
echo "[rtdetrv4] 완료 → $ENGINE"
