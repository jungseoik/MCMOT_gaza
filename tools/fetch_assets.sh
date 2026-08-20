#!/usr/bin/env bash
# fetch_assets.sh — 대용량 자산(가중치·ONNX·CAD 원본)을 HuggingFace에서 일괄 다운로드
#
# git엔 안 올라가는(.gitignore) 대용량 파일을 clone 후 알맞은 경로로 내려받는 멱등 스크립트.
# 이미 있는 파일은 건너뛴다(--force로 강제 재다운로드).
#
# 소스 레포:
#   backseollgi/mot20_sbs_S50.pth      (model, 공개)  → ReID 가중치
#   backseollgi/bytetrack_x_mot20.tar  (model, 공개)  → 검출 가중치
#   backseollgi/MCMOT                  (model)        → ONNX(엔진 재빌드 원천) + CAD 원본
#
# ⚠️ backseollgi/MCMOT 는 현재 **비공개**다. 접근하려면 토큰이 필요하다:
#     export HF_TOKEN=hf_xxxxxxxx        # 절대 커밋하지 말 것
#   (또는 `hf auth login`). 공개로 전환되면 토큰 없이 받아진다.
#   가중치 2개(개별 공개 레포)는 공개라 토큰 없이 받아진다.
#   RTSP 테스트 영상은 backseollgi/MCMOT/videos/ 에 있고 tools/rtsp/setup_rtsp_streams.sh 가 받는다
#   (역할 분리 — 이 스크립트 소관 아님). MCMOT가 비공개라 그쪽도 HF_TOKEN 필요.
#
# 사용:
#   bash tools/fetch_assets.sh                # 전체(가중치+ONNX+CAD)
#   bash tools/fetch_assets.sh --weights      # 가중치만(공개, 토큰 불필요)
#   bash tools/fetch_assets.sh --onnx         # ONNX만
#   bash tools/fetch_assets.sh --cad          # CAD 원본만
#   bash tools/fetch_assets.sh --field        # 현장 화재대피훈련 RTSP 송출본만(1F 3 + 16F 6, 294MB·비공개)
#   bash tools/fetch_assets.sh --manual       # 사용 가이드 docx·pdf·스크린샷(버전별, ~45MB)
#   bash tools/fetch_assets.sh --force        # 이미 있어도 다시 받기
#   HF_TOKEN=hf_xxx bash tools/fetch_assets.sh   # MCMOT(비공개) 접근용
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DO_WEIGHTS=0; DO_ONNX=0; DO_CAD=0; DO_FIELD=0; DO_MANUAL=0; FORCE=0; ANY=0
for a in "$@"; do
  case "$a" in
    --weights) DO_WEIGHTS=1; ANY=1 ;;
    --onnx)    DO_ONNX=1;    ANY=1 ;;
    --cad)     DO_CAD=1;     ANY=1 ;;
    --field)   DO_FIELD=1;   ANY=1 ;;
    --manual)  DO_MANUAL=1;  ANY=1 ;;
    --all)     DO_WEIGHTS=1; DO_ONNX=1; DO_CAD=1; ANY=1 ;;   # field·manual은 무거워서 --all 제외
    --force)   FORCE=1 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "알 수 없는 인자: $a" >&2; exit 2 ;;
  esac
done
# 인자 없으면 전체(현장 영상 field는 무거워서 제외 — 필요 시 --field)
if [ "$ANY" -eq 0 ]; then DO_WEIGHTS=1; DO_ONNX=1; DO_CAD=1; fi

# hf CLI 확인 (conda env 우선)
HF_BIN=""
for c in hf huggingface-cli; do command -v "$c" >/dev/null 2>&1 && { HF_BIN="$c"; break; }; done
if [ -z "$HF_BIN" ]; then
  echo "hf CLI가 없습니다. 설치: pip install -U huggingface_hub" >&2
  echo "(conda면: conda run -n boosttrack pip install -U huggingface_hub)" >&2
  exit 1
fi

# fetch <repo> <repo_type> <repo_path> <dest_local_path>
#   HF 캐시로 받고(중복 dedup) 목표 경로로 복사. 이미 있으면(--force 아니면) 건너뜀.
fetch() {
  local repo="$1" rt="$2" rpath="$3" dest="$4"
  if [ -f "$dest" ] && [ "$FORCE" -eq 0 ]; then
    echo "  = 있음, 건너뜀: $dest"
    return 0
  fi
  echo "  ↓ $repo :: $rpath  →  $dest"
  mkdir -p "$(dirname "$dest")"
  local src
  src="$("$HF_BIN" download "$repo" "$rpath" --repo-type "$rt" | tail -n 1)" || {
    echo "  [실패] $repo/$rpath — 비공개 레포면 HF_TOKEN 필요(위 주석 참조)" >&2
    return 1
  }
  # huggingface_hub 1.x 의 hf CLI 는 경로를 "path=/..." 로 출력한다(0.x 는 맨경로).
  # 이 접두를 안 벗기면 cp 가 실패하는데도 위 || 가 안 걸려 ✔ 로 보인다 — 실측 확인.
  src="${src#path=}"
  [ -f "$src" ] || {
    echo "  [실패] $repo/$rpath — 다운로드 경로를 못 찾음: $src" >&2
    return 1
  }
  cp -f "$src" "$dest"
  echo "    ✔ $(du -h "$dest" | cut -f1)  $dest"
}

RC=0

if [ "$DO_WEIGHTS" -eq 1 ]; then
  echo "== 가중치 (공개 개별 레포 — assets/__init__.py 자동다운로드와 동일 소스) =="
  fetch "backseollgi/mot20_sbs_S50.pth"     model "mot20_sbs_S50.pth"     "external/weights/mot20_sbs_S50.pth"     || RC=1
  fetch "backseollgi/bytetrack_x_mot20.tar" model "bytetrack_x_mot20.tar" "external/weights/bytetrack_x_mot20.tar" || RC=1
fi

if [ "$DO_ONNX" -eq 1 ]; then
  echo "== ONNX (TRT 엔진 재빌드 원천 — backseollgi/MCMOT) =="
  fetch "backseollgi/MCMOT" model "onnx/yolox_mot20_dynamic.onnx" "external/weights/trt/yolox_mot20_dynamic.onnx" || RC=1
  fetch "backseollgi/MCMOT" model "onnx/fastreid_sbs_s50.onnx"    "external/weights/trt/fastreid_sbs_s50.onnx"    || RC=1
  # RF-DETR(투트랙 검출기) — 이 ONNX가 있으면 tools/setup_rfdetr.sh가 rfdetr venv 없이 엔진만 빌드.
  fetch "backseollgi/MCMOT" model "onnx/rfdetr-base.onnx"         "external/weights/onnx/rfdetr-base.onnx"        || RC=1
  # 신규 추론 프로파일(yolo26_clipreid) 원천 — 엔진 빌드는 tools/build_profile_engines.sh
  fetch "backseollgi/MCMOT" model "onnx/yolo26l_v6.3.onnx"        "external/weights/onnx/yolo26l_v6.3.onnx"       || RC=1
  fetch "backseollgi/MCMOT" model "onnx/clipreid_person.onnx"     "external/weights/onnx/clipreid_person.onnx"    || RC=1
fi

if [ "$DO_CAD" -eq 1 ]; then
  echo "== CAD 원본 (도면 추출·편집 재현 — backseollgi/MCMOT) =="
  fetch "backseollgi/MCMOT" model "cad/17F.dwg"                       "cad/17F.dwg"                       || RC=1
  fetch "backseollgi/MCMOT" model "cad/17F.dxf"                       "cad/17F.dxf"                       || RC=1
  fetch "backseollgi/MCMOT" model "cad/17F_Egress Review(Sample).dwg" "cad/17F_Egress Review(Sample).dwg" || RC=1
  # 여러 층을 한 시트에 늘어놓은 도면(전체 551x3326 m). 지상1층 맵의 출처지만
  # 운영 맵은 여기서 1층만 잘라낸 것(93.8x66.9 m)이라 그대로는 재현되지 않는다.
  fetch "backseollgi/MCMOT" model "cad/A-101_128_각 층 평면도_최종_수정.dwg" "cad/A-101_128_각 층 평면도_최종_수정.dwg" || RC=1
  # 현재 17F·16F 에 적용되어 있는 도면 — 같은 맵을 재현하려면 이걸 편집기에 올린다
  fetch "backseollgi/MCMOT" model "cad/17F_v2.dwg"                     "cad/17F_v2.dwg"                     || RC=1
  fetch "backseollgi/MCMOT" model "cad/17F_v2.dxf"                     "cad/17F_v2.dxf"                     || RC=1
  # AI hub(AI지원센터) 도면 일습 — 125파일·156MB라 파일별 fetch 대신 폴더째 받는다.
  # AutoCAD 자동백업(.bak)·락(.dwl*)은 업로드 대상이 아니라 여기에도 없다.
  CADD="$(mktemp -d)"
  if "$HF_BIN" download backseollgi/MCMOT --repo-type model \
       --include "cad/ai_hub_cad/**" --local-dir "$CADD" >/dev/null 2>&1 \
     && [ -d "$CADD/cad/ai_hub_cad" ]; then
    mkdir -p cad/ai_hub_cad
    cp -r "$CADD/cad/ai_hub_cad/." cad/ai_hub_cad/
    echo "  ↓ cad/ai_hub_cad/** → cad/ai_hub_cad/  ($(du -sh cad/ai_hub_cad 2>/dev/null | cut -f1))"
  else
    echo "  [실패] cad/ai_hub_cad — 비공개 MCMOT 접근 토큰(HF_TOKEN) 필요" >&2; RC=1
  fi
  rm -rf "$CADD"
fi

if [ "$DO_FIELD" -eq 1 ]; then
  echo "== 현장 영상 (실제 화재대피훈련 0521 — backseollgi/MCMOT/field, 비공개·개인정보) =="
  # HF 에는 **RTSP 송출본(encoded/, H.264 mp4)** 만 올려둔다 — 9채널 송출에 필요한 최소 집합.
  # 원본 avi(raw/)·추론 산출물(infer/)은 4.9GB 라 원본 서버에만 둔다.
  if "$HF_BIN" download backseollgi/MCMOT --repo-type model --include "field/**" --local-dir . >/dev/null; then
    echo "  ↓ field/** → ./field/  ($(du -sh field 2>/dev/null | cut -f1))"
  else
    echo "  [실패] field — 비공개 MCMOT 접근 토큰(HF_TOKEN) 필요" >&2; RC=1
  fi
fi

if [ "$DO_MANUAL" -eq 1 ]; then
  echo "== 사용 가이드 (backseollgi/MCMOT/manual — docx·pdf·스크린샷) =="
  # 버전 폴더째 다운로드 → docs/manual/<버전>/ (git 무시). 원고.md·README.md는 git 소관.
  TMPD="$(mktemp -d)"
  trap 'rm -rf "$TMPD"' EXIT
  if "$HF_BIN" download backseollgi/MCMOT --repo-type model \
       --include "manual/**" --local-dir "$TMPD" >/dev/null 2>&1 \
     && [ -d "$TMPD/manual" ]; then
    mkdir -p docs/manual
    cp -r "$TMPD/manual/." docs/manual/
    echo "  ↓ manual/** → docs/manual/  ($(du -sh docs/manual 2>/dev/null | cut -f1))"
  else
    echo "  [실패] manual — 비공개 MCMOT 접근 토큰(HF_TOKEN) 필요" >&2; RC=1
  fi
fi

echo
if [ "$RC" -eq 0 ]; then
  echo "== 완료 =="
else
  echo "== 일부 실패 (위 [실패] 확인 — 대개 비공개 MCMOT 접근 토큰 누락) =="
fi
echo "가중치는 이 스크립트 없이도 추론/추적 코드가 assets 를 import할 때 자동 확보된다"
echo "(공개 레포). ONNX·CAD 는 이 스크립트(또는 원본 서버 복사)로만 확보된다."
echo "RTSP 테스트 영상은 backseollgi/MCMOT/videos/ 에 있고 tools/rtsp/setup_rtsp_streams.sh 소관 —"
echo "  송출까지 함께 하므로 여기서는 다루지 않는다(역할 분리). MCMOT 비공개라 토큰 필요."
exit "$RC"
