"""E2E 한계 처리량 스윕 — 라이브 RTSP → ingest → 분석(검출+ReID+추적) 실효 fps.

목적: 채널 수를 늘리며 "채널당 실효 분석 fps"가 목표(5fps)를 못 지키는 지점,
그리고 그 이후 얼마나 저하되는지(4→3→2→1fps) 곡선을 실측한다.
현행 ffmpeg 인제스트(베이스라인)와 이후 DeepStream 경로 비교의 기준 데이터.

실행 (GPU1 격리 — GPU0은 타 프로젝트 사용 중):
  CUDA_VISIBLE_DEVICES=1 conda run -n boosttrack python \
      docs/reports/bench/bench_e2e_limit.py --tag ffmpeg_gpu1 \
      --channels 2,4,6,8,12,16 --secs 45

주의: CUDA_VISIBLE_DEVICES가 ffmpeg 서브프로세스에도 상속되므로
--decode-gpus는 "보이는 디바이스 기준" 인덱스(기본 0)로 준다.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
import sys

sys.path.insert(0, str(ROOT))

from system.config.schema import CameraConfig            # noqa: E402
from system.ingest import FrameQueue, IngestManager      # noqa: E402
from system.tracking.analyzer import AnalyzerThread      # noqa: E402

DEFAULT_STREAMS = ("sample1,zara01,zara02,eth,hotel,students03,arxiepiskopi,"
                   "in_out_counting,inout_sample2,1_v1,2_v1,3_v1")
OUT = Path(__file__).parent / "results_e2e_limit_{tag}.json"


class GpuSampler(threading.Thread):
    """nvidia-smi 폴링 — 측정 구간의 GPU util/mem 평균."""

    def __init__(self, interval: float = 2.0) -> None:
        super().__init__(daemon=True)
        self.interval = interval
        self.samples: list[tuple[float, float]] = []   # (util%, mem MiB)
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5).stdout
                rows = [r.split(",") for r in out.strip().splitlines()]
                # CUDA_VISIBLE_DEVICES와 무관하게 전체 평균이 아닌 최대 util GPU 기록
                utils = [float(r[0]) for r in rows]
                mems = [float(r[1]) for r in rows]
                k = utils.index(max(utils))
                self.samples.append((utils[k], mems[k]))
            except Exception:
                pass
            self._stop.wait(self.interval)

    def stop(self) -> dict:
        self._stop.set()
        if not self.samples:
            return {"gpu_util_avg": 0.0, "gpu_mem_max_mib": 0.0}
        return {
            "gpu_util_avg": sum(s[0] for s in self.samples) / len(self.samples),
            "gpu_mem_max_mib": max(s[1] for s in self.samples),
        }


def run_step(urls: list[str], n_ch: int, secs: float, fps: float,
             decode_gpus: list[int], warmup: float) -> dict:
    """N채널 1스텝: ingest+분석 기동 → 측정 → 결과 dict."""
    cams = [CameraConfig(cam_id=f"cam{i+1:02d}", name=f"ch{i+1}",
                         rtsp=urls[i % len(urls)], analyze_fps=fps)
            for i in range(n_ch)]

    q = FrameQueue(maxsize=64)
    mgr = IngestManager(q, gpu_devices=decode_gpus or None)

    # on_tracks 수신 계측 (warmup 이후만 집계)
    lock = threading.Lock()
    t_open: dict[str, float] = {}
    counts: dict[str, int] = defaultdict(int)
    last_ts: dict[str, float] = {}
    measure_from = [float("inf")]

    def on_tracks(cam_id: str, ts: float, tracks) -> None:
        now = time.monotonic()
        if now < measure_from[0]:
            return
        with lock:
            t_open.setdefault(cam_id, now)
            counts[cam_id] += 1
            last_ts[cam_id] = now

    analyzer = AnalyzerThread(q, on_tracks, camera_fps={c.cam_id: fps for c in cams},
                              default_fps=fps)
    sampler = GpuSampler()

    mgr.start(cams)
    analyzer.start()
    sampler.start()

    # 워밍업: 전 채널 연결 + 엔진 워밍업 대기
    time.sleep(warmup)
    measure_from[0] = time.monotonic()
    stats0 = analyzer.stats()
    time.sleep(secs)

    stats1 = analyzer.stats()
    gpu = sampler.stop()
    mgr.stop()
    analyzer.stop()
    analyzer.join(timeout=5.0)

    per_cam_fps = {}
    with lock:
        for cid in counts:
            span = last_ts[cid] - t_open[cid]
            per_cam_fps[cid] = (counts[cid] - 1) / span if span > 0 and counts[cid] > 1 else 0.0
    fps_vals = sorted(per_cam_fps.values())
    analyzed = stats1["frames"] - stats0["frames"]
    dropped = stats1["queue_dropped"] - stats0["queue_dropped"]

    return {
        "channels": n_ch,
        "target_fps": fps,
        "measure_secs": secs,
        "per_cam_fps": per_cam_fps,
        "fps_min": fps_vals[0] if fps_vals else 0.0,
        "fps_median": fps_vals[len(fps_vals) // 2] if fps_vals else 0.0,
        "fps_mean": sum(fps_vals) / len(fps_vals) if fps_vals else 0.0,
        "total_throughput_fps": analyzed / secs,
        "analyzed_frames": analyzed,
        "queue_dropped": dropped,
        "avg_infer_ms": stats1["avg_infer_ms"],
        "avg_queue_lag_s": stats1["avg_queue_lag_s"],
        **gpu,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="E2E 한계 처리량 스윕")
    ap.add_argument("--tag", required=True, help="결과 파일 태그 (예: ffmpeg_gpu1)")
    ap.add_argument("--channels", default="2,4,6,8,12,16")
    ap.add_argument("--secs", type=float, default=45.0)
    ap.add_argument("--warmup", type=float, default=25.0)
    ap.add_argument("--fps", type=float, default=5.0)
    ap.add_argument("--streams", default=DEFAULT_STREAMS)
    ap.add_argument("--base", default="rtsp://127.0.0.1:8554")
    ap.add_argument("--decode-gpus", default="0",
                    help="ffmpeg hwaccel_device (CUDA_VISIBLE_DEVICES 기준 인덱스)")
    args = ap.parse_args()

    names = [s.strip() for s in args.streams.split(",") if s.strip()]
    urls = [(n if n.startswith("rtsp://") else f"{args.base}/{n}") for n in names]
    decode_gpus = [int(g) for g in args.decode_gpus.split(",") if g.strip()]
    steps = [int(c) for c in args.channels.split(",")]

    results = []
    for n in steps:
        print(f"\n===== {n}채널 (target {args.fps:g}fps, {args.secs:g}s 측정) =====",
              flush=True)
        r = run_step(urls, n, args.secs, args.fps, decode_gpus, args.warmup)
        results.append(r)
        print(f"  → min/med/mean fps = {r['fps_min']:.2f}/{r['fps_median']:.2f}/"
              f"{r['fps_mean']:.2f} | 합계 {r['total_throughput_fps']:.1f}fps | "
              f"infer {r['avg_infer_ms']:.1f}ms | lag {r['avg_queue_lag_s']:.2f}s | "
              f"drop {r['queue_dropped']} | GPU {r['gpu_util_avg']:.0f}%", flush=True)
        time.sleep(5.0)   # 스텝 간 쿨다운 (ffmpeg 정리)

    out = OUT.with_name(OUT.name.format(tag=args.tag))
    out.write_text(json.dumps({
        "tag": args.tag, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "target_fps": args.fps, "results": results,
    }, indent=2, ensure_ascii=False))
    print(f"\n저장: {out}")

    print(f"\n{'ch':>4s} {'min':>6s} {'med':>6s} {'mean':>6s} {'total':>7s} "
          f"{'infer':>7s} {'lag':>6s} {'drop':>6s}")
    for r in results:
        print(f"{r['channels']:4d} {r['fps_min']:6.2f} {r['fps_median']:6.2f} "
              f"{r['fps_mean']:6.2f} {r['total_throughput_fps']:7.1f} "
              f"{r['avg_infer_ms']:6.1f}m {r['avg_queue_lag_s']:5.2f}s "
              f"{r['queue_dropped']:6d}")


if __name__ == "__main__":
    main()
