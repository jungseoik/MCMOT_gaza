#!/usr/bin/env python3
"""RTSP 연동 + 추론 동작 점검 (새 서버 셋업 확인용).

운영 서버(:8900)와 **완전히 별개로** 도는 진단 도구다. RTSP 주소를 받아
실제 검출·추적을 돌리고, 붙었는지·몇 fps 나오는지·사람이 잡히는지를
숫자와 영상으로 남긴다. 새 GPU 서버에 이관한 뒤 "정말 되는가"를 확인하는
용도이며, 제품 코드 경로는 건드리지 않는다.

  bash tools/rtsp_check.py 없이 python 으로 실행:
    python tools/rtsp_check.py rtsp://127.0.0.1:8554/field_16f_n
    python tools/rtsp_check.py rtsp://.../1 rtsp://.../2 --sec 20
    python tools/rtsp_check.py --all-registered            # :8900 등록분 전부
    python tools/rtsp_check.py rtsp://... --no-video       # 숫자만(영상 안 씀)

판정 기준
  연결   첫 프레임 수신 여부
  추론   목표 fps 대비 실제 처리 fps (--fps 기본 5.0, 운영과 동일)
  검출   프레임당 평균 검출 수 · 고유 트랙 수
        (사람이 없는 영상이면 0 이 정상 — 연결·추론은 따로 판정한다)

출력   results/rtsp_check/<이름>_check.mp4 + 콘솔 요약
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# 추적기·ReID 어댑터가 레포 루트를 CWD 로 가정한다(fast_reid 등 상대 import).
# 어느 디렉터리에서 실행하든 되도록 여기서 맞춰준다.
os.chdir(ROOT)

OUT_DIR = ROOT / "results" / "rtsp_check"
API = os.environ.get("MACS_API", "http://127.0.0.1:8900")


def registered_streams() -> list[tuple[str, str]]:
    """운영 서버에 등록된 카메라의 (이름, rtsp) — 없으면 빈 목록."""
    try:
        with urllib.request.urlopen(f"{API}/api/cameras", timeout=8) as r:
            cams = json.load(r)
    except Exception as e:
        print(f"[경고] 등록 카메라를 못 읽음 ({type(e).__name__}) — 주소를 직접 주세요",
              file=sys.stderr)
        return []
    return [(c.get("name") or c["cam_id"], c["rtsp"]) for c in cams]


def mask(url: str) -> str:
    """로그에 계정·비밀번호가 남지 않게."""
    if "://" in url and "@" in url.split("://", 1)[1].split("/", 1)[0]:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***:***@{rest.split('@', 1)[1]}"
    return url


def check_one(inf, name: str, rtsp: str, sec: float, fps: float,
              write_video: bool) -> dict:
    """한 스트림을 sec 초 동안 돌려 결과 dict 를 돌려준다."""
    label = name or rtsp.rsplit("/", 1)[-1]
    print(f"\n▶ {label}   {mask(rtsp)}")
    out_path = OUT_DIR / f"{label.replace('/', '_')}_check.mp4"
    writer = None
    period = 1.0 / fps if fps > 0 else 0.0
    t0 = time.time()
    n_frames = n_det = 0
    ids: set[int] = set()
    first_latency = None
    w = h = 0
    src_fps = 0.0
    next_due = t0

    try:
        for item in inf.stream(rtsp, live=True, draw=True):
            now = time.time()
            if first_latency is None:
                first_latency = now - t0
                w, h, src_fps = item["width"], item["height"], item["fps"] or 0.0
                print(f"   연결됨 · {w}x{h} · 소스 {src_fps:.0f}fps "
                      f"· 첫 프레임 {first_latency:.2f}s")
            if now < next_due:              # 목표 fps 로 솎아냄 (운영과 동일 부하)
                continue
            next_due = now + period
            n_frames += 1
            tg = item["targets"]
            if tg is not None and len(tg):
                n_det += len(tg)
                for t in tg:
                    if len(t) >= 5:
                        ids.add(int(t[4]))
            if write_video:
                if writer is None:
                    OUT_DIR.mkdir(parents=True, exist_ok=True)
                    writer = cv2.VideoWriter(
                        str(out_path), cv2.VideoWriter_fourcc(*"mp4v"),
                        fps, (item["width"], item["height"]))
                writer.write(item["frame"])
            if now - t0 >= sec:
                break
    except Exception as e:
        print(f"   [실패] {type(e).__name__}: {e}")
        return {"label": label, "rtsp": mask(rtsp), "ok": False,
                "error": f"{type(e).__name__}: {e}"}
    finally:
        if writer is not None:
            writer.release()

    dur = max(time.time() - t0, 1e-6)
    got_fps = n_frames / dur
    res = {"label": label, "rtsp": mask(rtsp),
           "ok": first_latency is not None,
           "w": w, "h": h, "src_fps": round(src_fps, 1),
           "first_latency": round(first_latency, 2) if first_latency else None,
           "frames": n_frames, "fps": round(got_fps, 2),
           "det_per_frame": round(n_det / n_frames, 2) if n_frames else 0.0,
           "tracks": len(ids),
           "video": str(out_path) if writer is not None else None}
    if not res["ok"]:
        print("   [실패] 프레임을 못 받음 — 주소·네트워크·코덱 확인")
        return res
    ratio = got_fps / fps if fps > 0 else 1.0
    print(f"   추론 {got_fps:.2f}fps (목표 {fps:.1f} · 달성 {ratio*100:.0f}%) "
          f"· 프레임 {n_frames}")
    print(f"   검출 평균 {res['det_per_frame']:.2f}명/프레임 · 고유 트랙 {res['tracks']}개")
    if res["video"]:
        print(f"   영상 {res['video']}")
    return res


def main() -> int:
    ap = argparse.ArgumentParser(
        description="RTSP 연동 + 추론 동작 점검 (운영서버와 별개)")
    ap.add_argument("rtsp", nargs="*", help="RTSP 주소 (여러 개 가능)")
    ap.add_argument("--all-registered", action="store_true",
                    help=f"{API} 에 등록된 카메라 전부 점검")
    ap.add_argument("--sec", type=float, default=15.0, help="채널당 관측 초 (기본 15)")
    ap.add_argument("--fps", type=float, default=5.0,
                    help="목표 분석 fps — 운영 기본값과 같게 (기본 5)")
    ap.add_argument("--no-video", action="store_true", help="영상 저장 생략(숫자만)")
    ap.add_argument("--detector", choices=["yolox", "rfdetr"], default="yolox")
    a = ap.parse_args()

    targets: list[tuple[str, str]] = [("", u) for u in a.rtsp]
    if a.all_registered:
        targets += registered_streams()
    if not targets:
        ap.error("RTSP 주소를 주거나 --all-registered 를 쓰세요")

    print("추론 엔진 로드 중… (TRT 엔진이 없으면 python src/build_trt.py 먼저)")
    from src.inference_gpu import BoostTrackGPUInference
    # 엔진 경로 기본값이 CWD 상대라 다른 디렉터리에서 실행하면 못 찾는다.
    # 레포 루트 기준으로 절대경로를 넘겨 어디서 실행하든 되게 한다.
    eng = ROOT / "external" / "weights" / "trt"
    kw = {}
    yx, rd = eng / "yolox_mot20_fp16.engine", eng / "fastreid_sbs_s50_fp16.engine"
    if yx.is_file():
        kw["yolox_engine"] = str(yx)
    if rd.is_file():
        kw["reid_engine"] = str(rd)
    try:
        inf = BoostTrackGPUInference(**kw)
    except Exception as e:
        print(f"[중단] 추론 엔진 초기화 실패: {type(e).__name__}: {e}", file=sys.stderr)
        print("  → TRT 엔진 확인: ls external/weights/trt/*.engine", file=sys.stderr)
        return 1

    rows = [check_one(inf, nm, url, a.sec, a.fps, not a.no_video)
            for nm, url in targets]

    print(f"\n{'=' * 74}")
    print(f"{'채널':22} {'연결':>4} {'해상도':>11} {'추론fps':>8} {'검출/f':>7} {'트랙':>5}")
    for r in rows:
        if not r["ok"]:
            print(f"{r['label'][:21]:22} {'✕':>4} {'-':>11} {'-':>8} {'-':>7} {'-':>5}")
            continue
        print(f"{r['label'][:21]:22} {'✔':>4} {r['w']}x{r['h']:<6} "
              f"{r['fps']:>8.2f} {r['det_per_frame']:>7.2f} {r['tracks']:>5}")
    ok_n = sum(1 for r in rows if r["ok"])
    slow = [r["label"] for r in rows
            if r["ok"] and a.fps > 0 and r["fps"] < a.fps * 0.8]
    print(f"\n연결 {ok_n}/{len(rows)}"
          + (f" · 목표fps 미달: {slow}" if slow else " · 전 채널 목표fps 달성"))
    return 0 if ok_n == len(rows) and not slow else 1


if __name__ == "__main__":
    raise SystemExit(main())
