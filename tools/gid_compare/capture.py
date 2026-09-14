"""글로벌ID 비교 하네스 — 1단계: 우리 파이프라인으로 다중카메라 시나리오를 1회
추론해 프레임별 관측(트랙+임베딩+맵좌표)을 파일로 남긴다.

이 캡처를 우리 resolve() 와 zip assign() 양쪽에 동일하게 흘려(2단계 render.py)
검출·임베딩이 같은 상태에서 **글로벌ID 로직 차이만** 비교하기 위한 것.

  python tools/gid_compare/capture.py --scenario scenario_01

출력: results/gid_compare/<scenario>/capture.pkl  (관측 + 카메라 메타)
"""
from __future__ import annotations

import argparse
import json
import pickle
import time
from pathlib import Path

import cv2
import numpy as np

import sys
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from system.contracts import FrameItem                      # noqa: E402
from system.ingest.frame_queue import FrameQueue            # noqa: E402
from system.tracking.analyzer import AnalyzerThread         # noqa: E402

REHEARSAL = ROOT / "media/vsource/cj/rehearsal/rehearsal.json"
FLOOR_JSON = ROOT / "data/sites/default/floor.json"          # floor10 (map_px 2000x1887)
MAP_PNG = ROOT / "data/sites/default/map_floor10.png"


def _project(H: np.ndarray, uv: tuple[float, float]) -> tuple[float, float]:
    """foot_uv(원본 프레임 px) → 플로어맵 px, 호모그래피 적용."""
    x, y = uv
    v = H @ np.array([x, y, 1.0])
    if abs(v[2]) < 1e-9:
        return (float("nan"), float("nan"))
    return (float(v[0] / v[2]), float(v[1] / v[2]))


def main() -> int:
    ap = argparse.ArgumentParser(description="글로벌ID 비교용 관측 캡처")
    ap.add_argument("--scenario", default="scenario_01")
    ap.add_argument("--profile", default="yolo26_clipreid")
    ap.add_argument("--fps", type=float, default=5.0, help="분석 fps (rehearsal analyze_fps)")
    ap.add_argument("--max-sec", type=float, default=None, help="캡처 상한 초(테스트)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    reh = json.loads(REHEARSAL.read_text(encoding="utf-8"))
    sc = next(s for s in reh["scenarios"] if s["id"] == args.scenario)
    cams_by_id = {c["cam"]: c for c in reh["cameras"]}

    # route 순서(핸드오버 순)로 카메라 정렬 — 같은 순간 내 처리 순서 고정
    route = sc.get("route") or [st["cam"] for st in sc["streams"]]
    streams = {st["cam"]: st for st in sc["streams"]}
    order = [c for c in route if c in streams] + [c for c in streams if c not in route]

    m_per_px = float(json.loads(FLOOR_JSON.read_text())["m_per_px"])

    cam_meta = {}
    caps = {}
    for cam in order:
        meta = cams_by_id[cam]
        vp = REHEARSAL.parent / streams[cam]["file"]
        cap = cv2.VideoCapture(str(vp))
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        caps[cam] = (cap, src_fps, n)
        cam_meta[cam] = {
            "video": str(vp), "src_fps": src_fps, "nframes": n, "w": W, "h": H,
            "H": np.array(meta["mapping"]["H"], np.float64).reshape(3, 3),
            "map_wh": meta.get("map_wh", [2000, 1887]),
            "analyze_fps": meta.get("analyze_fps", args.fps),
        }
        print(f"[capture] {cam}: {W}x{H}@{src_fps:.1f} n={n} -> {vp.name}")

    dur = min(streams[c]["duration_sec"] for c in order)
    if args.max_sec:
        dur = min(dur, args.max_sec)
    nsteps = int(dur * args.fps)
    print(f"[capture] {len(order)} cams, {dur:.1f}s @ {args.fps}fps -> {nsteps} steps, profile={args.profile}")

    # 우리 프로덕션 파이프라인 그대로 (검출+트래커+임베더). run() 스레드는 안 돌리고
    # _process 를 직접 호출해 프레임별 관측을 수집한다.
    bucket: dict[str, list] = {}

    def on_tracks(cam_id: str, ts: float, tracks) -> None:
        rows = []
        Hc = cam_meta[cam_id]["H"]
        for t in tracks:
            emb = None if t.emb is None else np.asarray(t.emb, np.float32).copy()  # 버퍼 재사용→복사 필수
            mx, my = _project(Hc, t.foot_uv)
            rows.append({
                "local_id": int(t.local_track_id),
                "bbox": tuple(float(v) for v in t.bbox_xyxy),
                "foot_uv": (float(t.foot_uv[0]), float(t.foot_uv[1])),
                "conf": float(t.conf),
                "map_xy": (mx, my),
                "emb": emb,
            })
        bucket[cam_id] = rows

    analyzer = AnalyzerThread(
        FrameQueue(maxsize=8), on_tracks,
        profile=args.profile,
        camera_fps={c: cam_meta[c]["analyze_fps"] for c in order},
        default_fps=args.fps,
    )

    frames_dir = None  # 렌더는 src 프레임 인덱스로 재디코드 (아래 frame_idx 저장)
    observations = []
    t0 = time.perf_counter()
    for step in range(nsteps):
        t_sec = step / args.fps
        snap = {"step": step, "t": t_sec, "cams": {}}
        for cam in order:
            cap, src_fps, n = caps[cam]
            fidx = min(int(round(t_sec * src_fps)), max(n - 1, 0))
            cap.set(cv2.CAP_PROP_POS_FRAMES, fidx)
            ok, frame = cap.read()
            if not ok or frame is None:
                snap["cams"][cam] = {"frame_idx": fidx, "tracks": []}
                continue
            bucket.clear()
            analyzer._process(FrameItem(cam_id=cam, ts=t_sec, frame=frame, seq=step))
            snap["cams"][cam] = {"frame_idx": fidx, "tracks": bucket.get(cam, [])}
        observations.append(snap)
        if step % 20 == 0:
            el = time.perf_counter() - t0
            print(f"  step {step}/{nsteps}  t={t_sec:.1f}s  ({el:.1f}s elapsed)")

    for cap, _, _ in caps.values():
        cap.release()

    out = Path(args.out) if args.out else ROOT / "results/gid_compare" / args.scenario / "capture.pkl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as f:
        pickle.dump({
            "scenario": args.scenario,
            "profile": args.profile,
            "fps": args.fps,
            "order": order,
            "route": route,
            "m_per_px": m_per_px,
            "map_png": str(MAP_PNG),
            "cam_meta": {c: {k: (v.tolist() if isinstance(v, np.ndarray) else v)
                             for k, v in m.items()} for c, m in cam_meta.items()},
            "observations": observations,
        }, f)
    n_tracks = sum(len(cd["tracks"]) for snap in observations for cd in snap["cams"].values())
    print(f"[capture] done: {nsteps} steps, {n_tracks} track-observations -> {out}")
    print(f"[capture] elapsed {time.perf_counter()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
