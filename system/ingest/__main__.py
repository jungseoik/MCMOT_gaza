"""ingest 스모크/부하 실측 CLI (M2 검증용 — 추론 없음).

예)  16ch 60초 수신 실측:
  conda run -n boosttrack python -m system.ingest \
      --streams sample1,zara01,... --secs 60 --fps 5 --gpus 0,1

재접속 검증: 실행 중 `pm2 stop <스트림>` → 백오프 로그 → `pm2 start <스트림>`.
"""
from __future__ import annotations

import argparse
import logging
import threading
import time
from collections import defaultdict

from system.config.schema import CameraConfig
from system.ingest import FrameQueue, IngestManager


def main() -> None:
    ap = argparse.ArgumentParser(description="멀티카메라 ingest 실측 (추론 없음)")
    ap.add_argument("--streams", required=True,
                    help="mediamtx 경로 이름 콤마 구분 (예: sample1,zara01) 또는 rtsp:// 전체 URL")
    ap.add_argument("--base", default="rtsp://127.0.0.1:8554")
    ap.add_argument("--secs", type=float, default=60.0)
    ap.add_argument("--fps", type=float, default=5.0, help="analyze_fps")
    ap.add_argument("--gpus", default="", help="hwaccel_device 분산 (예: 0,1)")
    ap.add_argument("--repeat", type=int, default=1,
                    help="스트림 목록 반복 배수 (14개→16ch 등 채널 수 맞추기)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    names = [s.strip() for s in args.streams.split(",") if s.strip()]
    urls = [(n if n.startswith("rtsp://") else f"{args.base}/{n}") for n in names]
    urls = (urls * args.repeat)
    gpus = [int(g) for g in args.gpus.split(",") if g.strip()] or None

    cams = [CameraConfig(cam_id=f"cam{i+1:02d}", name=u.rsplit('/', 1)[-1],
                         rtsp=u, analyze_fps=args.fps)
            for i, u in enumerate(urls)]

    q = FrameQueue(maxsize=64)
    mgr = IngestManager(q, gpu_devices=gpus)

    # 소비 스레드 — 큐를 비우며 카메라별 수신 수를 센다 (분석 스레드 대역)
    consumed: dict[str, int] = defaultdict(int)
    first_ts: dict[str, float] = {}
    last_ts: dict[str, float] = {}
    stop = threading.Event()

    def drain() -> None:
        while not stop.is_set():
            item = q.get(timeout=0.5)
            if item is None:
                continue
            consumed[item.cam_id] += 1
            first_ts.setdefault(item.cam_id, item.ts)
            last_ts[item.cam_id] = item.ts

    th = threading.Thread(target=drain, daemon=True)
    th.start()
    mgr.start(cams)

    t0 = time.time()
    try:
        while time.time() - t0 < args.secs:
            time.sleep(5.0)
            running = sum(1 for s in mgr.states() if s.status == "running")
            print(f"[{time.time()-t0:5.1f}s] running={running}/{len(cams)} "
                  f"qsize={q.qsize()} drops={q.dropped}")
    except KeyboardInterrupt:
        pass
    finally:
        elapsed = time.time() - t0
        final_states = {s.cam_id: s for s in mgr.states()}
        mgr.stop()
        stop.set()
        th.join(timeout=2.0)

    print(f"\n== 결과 ({elapsed:.1f}s, {len(cams)}ch, analyze_fps={args.fps:g}) ==")
    print(f"{'cam':7s} {'stream':18s} {'status':13s} {'recv':>6s} {'fps':>6s} {'drops':>6s}")
    for cfg in cams:
        st = final_states[cfg.cam_id]
        n = consumed.get(cfg.cam_id, 0)
        span = (last_ts.get(cfg.cam_id, 0) - first_ts.get(cfg.cam_id, 0))
        fps = (n - 1) / span if n >= 2 and span > 0 else 0.0
        print(f"{cfg.cam_id:7s} {cfg.name:18s} {st.status:13s} {n:6d} {fps:6.2f} {st.drops:6d}")
    total = sum(consumed.values())
    print(f"총 수신 {total}프레임 ({total/elapsed:.1f}fps 합계), 큐 드랍 {q.dropped}")


if __name__ == "__main__":
    main()
