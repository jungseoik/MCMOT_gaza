#!/usr/bin/env python3
"""RTSP 미리보기 워커 — API 프로세스가 subprocess 로 띄운다 (직접 실행 안 함).

운영 API(:8900)는 추론을 올리지 않는다(DeepStream 컨테이너 담당, 프로세스
77MB). 미리보기 때문에 torch/TRT 를 API 에 얹으면 상주 메모리가 GB 단위로
늘고 기동이 느려지므로, **필요할 때만 뜨고 끝나면 사라지는 별도 프로세스**로
분리한다.

워커는 결과를 파일로만 남긴다(파이프/소켓 없음 — 워커가 죽어도 API 는 무사):
    <out>/frame.jpg    최신 프레임 (ID 박스 그려진 JPEG, 원자적 교체)
    <out>/status.json  {running, w, h, src_fps, fps, det, tracks, frames, error}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# 추적기·ReID 어댑터가 레포 루트를 CWD 로 가정한다(fast_reid 상대 import)
os.chdir(ROOT)


def write_status(out: Path, **kw) -> None:
    tmp = out / "status.json.tmp"
    tmp.write_text(json.dumps(kw, ensure_ascii=False))
    tmp.replace(out / "status.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rtsp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=5.0)
    ap.add_argument("--max-sec", type=float, default=600.0)
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    st = {"running": True, "w": 0, "h": 0, "src_fps": 0.0, "fps": 0.0,
          "det": 0.0, "tracks": 0, "frames": 0, "error": "",
          "first_latency": None, "stage": "엔진 로드 중"}
    write_status(out, **st)

    try:
        from src.inference_gpu import BoostTrackGPUInference
        eng = ROOT / "external" / "weights" / "trt"
        kw = {}
        yx, rd = eng / "yolox_mot20_fp16.engine", eng / "fastreid_sbs_s50_fp16.engine"
        if yx.is_file():
            kw["yolox_engine"] = str(yx)
        if rd.is_file():
            kw["reid_engine"] = str(rd)
        inf = BoostTrackGPUInference(**kw)
    except Exception as e:
        st.update(running=False, error=f"추론 엔진 로드 실패 — {type(e).__name__}: {e}",
                  stage="실패")
        write_status(out, **st)
        return 1

    st["stage"] = "연결 중"
    write_status(out, **st)

    period = 1.0 / a.fps if a.fps > 0 else 0.0
    t0 = time.time()
    n = ndet = 0
    ids: set[int] = set()
    next_due = t0
    try:
        for item in inf.stream(a.rtsp, live=True, draw=True):
            now = time.time()
            if st["first_latency"] is None:
                st.update(first_latency=round(now - t0, 2), w=item["width"],
                          h=item["height"], src_fps=round(item["fps"] or 0.0, 1),
                          stage="동작 중")
            if now < next_due:
                continue
            next_due = now + period
            n += 1
            tg = item["targets"]
            if tg is not None and len(tg):
                ndet += len(tg)
                for t in tg:
                    if len(t) >= 5:
                        ids.add(int(t[4]))
            ok, buf = cv2.imencode(".jpg", item["frame"], [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:                                   # 원자적 교체 — 반쯤 쓴 파일 방지
                tmp = out / "frame.jpg.tmp"
                tmp.write_bytes(buf.tobytes())
                tmp.replace(out / "frame.jpg")
            dur = max(now - t0, 1e-6)
            st.update(frames=n, fps=round(n / dur, 2),
                      det=round(ndet / n, 2), tracks=len(ids))
            write_status(out, **st)
            if now - t0 >= a.max_sec:                # 상한 — GPU 를 무한정 붙잡지 않게
                st["error"] = f"최대 시간({a.max_sec:.0f}s) 도달 — 자동 종료"
                break
    except Exception as e:
        st["error"] = f"{type(e).__name__}: {e}"
    finally:
        st.update(running=False, stage="정지")
        write_status(out, **st)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
