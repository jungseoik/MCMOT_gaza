#!/usr/bin/env python3
"""ONNX → TensorRT 엔진 빌더 (범용) — 신규 모델(YOLO26 / CLIP-ReID)용.

기존 src/build_trt.py 는 YOLOX·FastReID **체크포인트 → ONNX export → 엔진**까지
한 몸이라 재사용이 어렵다. 이 스크립트는 **이미 있는 ONNX만 받아** 프로파일을
명시해 엔진을 굽는다. 호스트(conda TRT)와 DS 컨테이너(TRT 버전 다름) 양쪽에서
같은 명령으로 쓴다 — 엔진은 GPU 아키텍처·TRT 버전마다 따로 구워야 한다.

    python tools/build_trt_engine.py \
        --onnx external/weights/onnx/yolo26l_v6.3.onnx \
        --engine external/weights/trt/yolo26l_v6.3_fp16_b16.engine \
        --input images --min 1x3x640x640 --opt 4x3x640x640 --max 16x3x640x640 --fp16
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import tensorrt as trt


def _shape(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in s.lower().replace(",", "x").split("x") if x != "")


def build(onnx: Path, engine: Path, input_name: str,
          mn: tuple, opt: tuple, mx: tuple,
          fp16: bool = True, workspace_mib: int = 4096) -> Path:
    logger = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(logger)
    flags = 0
    if hasattr(trt.NetworkDefinitionCreationFlag, "EXPLICIT_BATCH"):   # TRT<=9 호환
        flags = 1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH)
    network = builder.create_network(flags)
    parser = trt.OnnxParser(network, logger)
    if not parser.parse(onnx.read_bytes()):
        errs = "\n".join(str(parser.get_error(i)) for i in range(parser.num_errors))
        raise RuntimeError(f"ONNX 파싱 실패 {onnx}:\n{errs}")

    cfg = builder.create_builder_config()
    cfg.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, workspace_mib << 20)
    if fp16:
        cfg.set_flag(trt.BuilderFlag.FP16)
    profile = builder.create_optimization_profile()
    profile.set_shape(input_name, mn, opt, mx)
    cfg.add_optimization_profile(profile)

    print(f"[build] {onnx.name} → {engine.name}  min={mn} opt={opt} max={mx} "
          f"fp16={fp16}  (TRT {trt.__version__})", flush=True)
    t0 = time.time()
    ser = builder.build_serialized_network(network, cfg)
    if ser is None:
        raise RuntimeError(f"엔진 빌드 실패: {onnx}")
    engine.parent.mkdir(parents=True, exist_ok=True)
    tmp = engine.with_suffix(engine.suffix + ".part")
    tmp.write_bytes(ser)
    tmp.rename(engine)
    print(f"[build] 완료 {engine} ({engine.stat().st_size/2**20:.1f} MiB, {time.time()-t0:.0f}s)")
    return engine


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--engine", required=True)
    ap.add_argument("--input", default="images", help="ONNX 입력 텐서 이름")
    ap.add_argument("--min", required=True)
    ap.add_argument("--opt", required=True)
    ap.add_argument("--max", required=True)
    ap.add_argument("--fp16", action="store_true")
    ap.add_argument("--workspace", type=int, default=4096, help="MiB")
    ap.add_argument("--force", action="store_true", help="이미 있어도 다시 빌드")
    a = ap.parse_args()

    eng = Path(a.engine)
    if eng.is_file() and not a.force:
        print(f"[skip] 이미 존재: {eng}")
        return 0
    build(Path(a.onnx), eng, a.input, _shape(a.min), _shape(a.opt), _shape(a.max),
          fp16=a.fp16, workspace_mib=a.workspace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
