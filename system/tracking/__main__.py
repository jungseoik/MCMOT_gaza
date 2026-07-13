"""멀티카메라 동시 트래킹 스모크 CLI (M3 검증용).

예)  2ch 동시 트래킹 40초 + 카메라별 ID 공간 독립 확인:
  conda run -n boosttrack python -m system.tracking \
      --streams zara01,zara02 --secs 40 --fps 5
"""
from __future__ import annotations

import argparse
import logging
import threading
import time
from collections import defaultdict

from system.config.schema import CameraConfig
from system.contracts import TrackedObject
from system.ingest import FrameQueue, IngestManager
from system.tracking import AnalyzerThread


def main() -> None:
    ap = argparse.ArgumentParser(description="멀티카메라 동시 트래킹 실측")
    ap.add_argument("--streams", required=True,
                    help="mediamtx 경로 이름 콤마 구분 또는 rtsp:// 전체 URL")
    ap.add_argument("--base", default="rtsp://127.0.0.1:8554")
    ap.add_argument("--secs", type=float, default=40.0)
    ap.add_argument("--fps", type=float, default=5.0)
    ap.add_argument("--gpus", default="", help="디코드 hwaccel_device 분산 (예: 0,1)")
    ap.add_argument("--no-reid", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    names = [s.strip() for s in args.streams.split(",") if s.strip()]
    urls = [(n if n.startswith("rtsp://") else f"{args.base}/{n}") for n in names]
    gpus = [int(g) for g in args.gpus.split(",") if g.strip()] or None
    cams = [CameraConfig(cam_id=f"cam{i+1:02d}", name=u.rsplit('/', 1)[-1],
                         rtsp=u, analyze_fps=args.fps)
            for i, u in enumerate(urls)]

    q = FrameQueue(maxsize=64)
    mgr = IngestManager(q, gpu_devices=gpus)

    ids_by_cam: dict[str, set[int]] = defaultdict(set)
    tracks_by_cam: dict[str, int] = defaultdict(int)
    lock = threading.Lock()

    def on_tracks(cam_id: str, ts: float, tracks: list[TrackedObject]) -> None:
        with lock:
            for tr in tracks:
                ids_by_cam[cam_id].add(tr.local_track_id)
                tracks_by_cam[cam_id] += 1

    analyzer = AnalyzerThread(
        q, on_tracks,
        camera_fps={c.cam_id: c.analyze_fps for c in cams},
        default_fps=args.fps,
        use_reid=not args.no_reid,
    )
    analyzer.start()
    mgr.start(cams)

    t0 = time.time()
    try:
        while time.time() - t0 < args.secs:
            time.sleep(5.0)
            s = analyzer.stats()
            print(f"[{time.time()-t0:5.1f}s] frames={s['frames']} "
                  f"avg={s['avg_infer_ms']:.1f}ms lag={s['avg_queue_lag_s']:.2f}s "
                  f"qsize={s['queue_size']} drops={s['queue_dropped']}")
    except KeyboardInterrupt:
        pass
    finally:
        elapsed = time.time() - t0
        mgr.stop()
        analyzer.stop()
        analyzer.join(timeout=5.0)

    s = analyzer.stats()
    print(f"\n== 결과 ({elapsed:.1f}s, {len(cams)}ch, analyze_fps={args.fps:g}) ==")
    print(f"분석 {s['frames']}프레임 ({s['frames']/elapsed:.1f}fps 합계), "
          f"평균 추론 {s['avg_infer_ms']:.1f}ms/frame, "
          f"평균 큐 지연 {s['avg_queue_lag_s']:.2f}s, 큐 드랍 {s['queue_dropped']}")
    for cfg in cams:
        ids = sorted(ids_by_cam.get(cfg.cam_id, set()))
        n = s["frames_by_cam"].get(cfg.cam_id, 0)
        rng = f"{ids[0]}..{ids[-1]}" if ids else "-"
        print(f"{cfg.cam_id} ({cfg.name}): 분석 {n}프레임, 트랙관측 "
              f"{tracks_by_cam.get(cfg.cam_id, 0)}건, 고유 ID {len(ids)}개 (범위 {rng})")
    # ID 공간 독립 확인: 카메라마다 ID가 1부터 시작해야 함
    starts = {c.cam_id: (min(ids_by_cam[c.cam_id]) if ids_by_cam.get(c.cam_id) else None)
              for c in cams}
    print(f"ID 시작값(카메라별, 독립이면 모두 1): {starts}")


if __name__ == "__main__":
    main()
