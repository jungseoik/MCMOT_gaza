#!/usr/bin/env python3
"""RT-DETRv4 TRT 엔진 빌더 — 입력이 **2개**라 tools/build_trt_engine.py 로는 안 된다.

RT-DETRv4 ONNX 는 images(1,3,640,640) + orig_target_sizes(1,2) 를 받는다.
build_trt_engine.py 는 --input 하나만 다루므로 전용 빌더를 둔다.
호스트(conda TRT)와 DS 컨테이너(TRT 버전 다름) 양쪽에서 같은 코드로 굽는다.

    python tools/build_rtdetrv4_engine.py --onnx <onnx> --engine <out> [--fp16]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import tensorrt as trt


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--engine", required=True)
    ap.add_argument("--fp16", action="store_true")
    ap.add_argument("--workspace", type=int, default=4096, help="MiB")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    out = Path(a.engine)
    if out.is_file() and not a.force:
        print(f"[rtdetrv4] 있음 — 생략: {out}")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)

    L = trt.Logger(trt.Logger.WARNING)
    b = trt.Builder(L)
    net = b.create_network(0)
    parser = trt.OnnxParser(net, L)
    with open(a.onnx, "rb") as f:
        if not parser.parse(f.read()):
            for i in range(parser.num_errors):
                print(parser.get_error(i))
            raise SystemExit("ONNX 파싱 실패")

    cfg = b.create_builder_config()
    if a.fp16:
        cfg.set_flag(trt.BuilderFlag.FP16)
    cfg.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, a.workspace << 20)
    prof = b.create_optimization_profile()
    prof.set_shape("images", (1, 3, 640, 640), (1, 3, 640, 640), (1, 3, 640, 640))
    prof.set_shape("orig_target_sizes", (1, 2), (1, 2), (1, 2))
    cfg.add_optimization_profile(prof)

    plan = b.build_serialized_network(net, cfg)
    if plan is None:
        raise SystemExit("엔진 빌드 실패")
    out.write_bytes(plan)
    print(f"[rtdetrv4] 엔진: {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
