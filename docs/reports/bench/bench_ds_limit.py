"""DeepStream 경로 한계 처리량 스윕 — 채널 수를 늘리며 실효 fps 저하 곡선 실측.

목적: DS 워커(system/ingest_ds) 경로에서 채널당 목표 5fps가 깨지는 지점(=현
서버 한계)과 그 이후 저하 곡선(4→3→2→1fps)을 실측한다. 완전 실시간이 아니어도
됨 — 지연이 커져도 어디까지 버티는지 기록한다. ffmpeg 베이스라인
(results_e2e_limit_ffmpeg_gpu1.json, bench_e2e_limit.py)과 방법론 동일:
mediamtx 스트림 12개를 채널이 중복 구독(urls[i % len(urls)])해 부하를 만든다.

실행 (GPU1 단독 — GPU0은 타 프로젝트 사용 중):
  conda run -n boosttrack python docs/reports/bench/bench_ds_limit.py \
      --tag gpu1 --gpu 1 --channels 12,16,24,32,48,64 --secs 60 --warmup 30

워커 분할 스윕 (같은 GPU에 워커 프로세스 N개 — GIL 분리, launcher P8):
  conda run -n boosttrack python docs/reports/bench/bench_ds_limit.py \
      --tag gpu1_w2 --gpu 1 --workers-per-gpu 2 \
      --channels 16,24,32,40,48 --secs 60 --warmup 30

측정 지표: 채널당 실효 fps(min/med/mean) · 총 처리량 · 지연(수신 ts→트랙 출력,
호스트 수신 시점 기준) · GPU util/mem/NVDEC util(nvidia-smi 폴링) ·
워커 STATS 로그의 batch 평균/infer ms/큐 드랍/zmq 드랍.

주의:
- vLLM이 GPU1 메모리를 대부분 점유 중 — OOM으로 워커가 죽으면 그 채널 수를
  메모리 한계로 기록하고 다음 스텝은 건너뛴다.
- NVDEC 세션 한도에 걸리면 워커 로그의 디코더 에러로 드러난다 → errors에 기록.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from system.ingest_ds.bridge import TrackBridge            # noqa: E402
from system.ingest_ds.launcher import (                    # noqa: E402
    WorkerContainer,
    partition_cams,
)

DEFAULT_STREAMS = ("sample1,zara01,zara02,eth,hotel,students03,arxiepiskopi,"
                   "in_out_counting,inout_sample2,1_v1,2_v1,3_v1")
OUT = Path(__file__).parent / "results_ds_limit_{tag}.json"

STATS_RE = re.compile(
    r"STATS fps=.*batch_avg=([\d.]+) infer_avg=([\d.]+)ms \| q=(\d+) qdrop=(\d+) "
    r"zmqdrop=(\d+) gpumap=(\S+)")


class GpuSampler(threading.Thread):
    """nvidia-smi 폴링 — 대상 GPU의 SM util/NVDEC util/mem."""

    def __init__(self, gpu: int, interval: float = 2.0) -> None:
        super().__init__(daemon=True)
        self.gpu = gpu
        self.interval = interval
        self.samples: list[tuple[float, float, float]] = []  # (sm%, dec%, mem MiB)
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                out = subprocess.run(
                    ["nvidia-smi", "-i", str(self.gpu),
                     "--query-gpu=utilization.gpu,utilization.decoder,memory.used",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5).stdout
                r = out.strip().split(",")
                self.samples.append((float(r[0]), float(r[1]), float(r[2])))
            except Exception:
                pass
            self._stop.wait(self.interval)

    def stop(self) -> dict:
        self._stop.set()
        if not self.samples:
            return {"gpu_util_avg": 0.0, "nvdec_util_avg": 0.0,
                    "gpu_util_max": 0.0, "nvdec_util_max": 0.0,
                    "gpu_mem_max_mib": 0.0}
        sm = [s[0] for s in self.samples]
        dec = [s[1] for s in self.samples]
        mem = [s[2] for s in self.samples]
        return {"gpu_util_avg": sum(sm) / len(sm), "gpu_util_max": max(sm),
                "nvdec_util_avg": sum(dec) / len(dec), "nvdec_util_max": max(dec),
                "gpu_mem_max_mib": max(mem)}


def parse_worker_stats(log: str, n_lines: int) -> dict:
    """워커 STATS 로그 마지막 n_lines개(≈측정 구간)의 batch/infer/드랍 요약."""
    rows = STATS_RE.findall(log)
    if not rows:
        return {"batch_avg": 0.0, "infer_ms_avg": 0.0, "queue_dropped": 0,
                "zmq_drops": 0, "gpumap": "?"}
    window = rows[-n_lines:] if n_lines > 0 else rows
    return {
        "batch_avg": sum(float(r[0]) for r in window) / len(window),
        "infer_ms_avg": sum(float(r[1]) for r in window) / len(window),
        # qdrop/zmqdrop은 누계 — 구간 증분으로 환산
        "queue_dropped": int(window[-1][3]) - int(window[0][3]),
        "zmq_drops": int(window[-1][4]) - int(window[0][4]),
        "gpumap": window[-1][5],
    }


def scan_errors(log: str) -> dict:
    """워커 로그에서 병목 원인 판별용 에러 시그널 수집."""
    return {
        "gst_errors": len(re.findall(r"GST ERROR", log)),
        "cuda_oom": len(re.findall(r"(?i)out of memory|cuda.*alloc.*fail", log)),
        "decode_errors": len(re.findall(r"(?i)nvdec|decoder.*(error|fail)", log)),
        "infer_fail": len(re.findall(r"배치 추론 실패", log)),
    }


def run_step(urls: list[str], n_ch: int, gpu: int, secs: float, fps: float,
             warmup: float, connect_timeout: float,
             workers_per_gpu: int = 1) -> dict:
    cams = [{"cam_id": f"cam{i + 1:02d}", "rtsp": urls[i % len(urls)],
             "analyze_fps": fps} for i in range(n_ch)]

    # 수신 계측 (measure_from 이후만 집계)
    lock = threading.Lock()
    t_first: dict[str, float] = {}
    t_last: dict[str, float] = {}
    counts: dict[str, int] = defaultdict(int)
    tracks_sum: dict[str, int] = defaultdict(int)   # 유사도 스팟체크용 트랙 수
    seen: set[str] = set()
    lags: list[float] = []
    measure_from = [float("inf")]

    def on_tracks(cam_id: str, ts: float, tracks) -> None:
        now = time.monotonic()
        wall = time.time()
        with lock:
            seen.add(cam_id)
            if now < measure_from[0]:
                return
            t_first.setdefault(cam_id, now)
            counts[cam_id] += 1
            tracks_sum[cam_id] += len(tracks)
            t_last[cam_id] = now
            lags.append(wall - ts)      # appsink 수신 ts → 호스트 트랙 수신

    # (GPU, 워커) 슬롯 분할 — launcher와 동일한 greedy 부하 균등
    slots = [(gpu, j) for j in range(workers_per_gpu)]
    assign = partition_cams(cams, slots)
    workers: list[WorkerContainer] = []
    slot_cams: dict[str, list[str]] = {}
    for (g, j) in slots:
        wc = WorkerContainer(g, worker=j, n_workers=workers_per_gpu,
                             prefix="macs-ds-bench")
        wc_cams = assign[(g, j)]
        workers.append(wc)
        slot_cams[wc.name] = [c["cam_id"] for c in wc_cams]
    bridge = TrackBridge(on_tracks, connect=[w.endpoint for w in workers])
    batch_sizes = {w.name: min(16, max(1, len(assign[(gpu, w.worker)])))
                   for w in workers}
    err: str | None = None
    try:
        for w in workers:
            w.start(assign[(gpu, w.worker)],
                    batch_size=batch_sizes[w.name] or None)
        bridge.start()

        # 워밍업 1: 엔진 로드+RTSP 연결 — 전 채널 첫 수신까지 대기
        t0 = time.monotonic()
        while time.monotonic() - t0 < connect_timeout:
            with lock:
                n_seen = len(seen)
            if n_seen >= n_ch:
                break
            dead = [w.name for w in workers if w.cams and not w.running()]
            if dead:
                err = f"워커 조기 종료 {dead} (OOM/에러 — 로그 참조)"
                break
            time.sleep(1.0)
        with lock:
            n_seen = len(seen)
        connect_sec = time.monotonic() - t0
        if err is None and n_seen < n_ch:
            err = f"연결 타임아웃 — {n_seen}/{n_ch} 채널만 수신"

        result: dict = {"channels": n_ch, "target_fps": fps,
                        "workers_per_gpu": workers_per_gpu,
                        "batch_sizes": batch_sizes, "measure_secs": secs,
                        "cams_connected": n_seen,
                        "connect_sec": round(connect_sec, 1)}

        if err is None or n_seen > 0:      # 부분 연결이어도 저하 곡선은 측정
            # 워밍업 2: 정착 대기 후 측정 구간 개시
            time.sleep(warmup)
            sampler = GpuSampler(gpu)
            sampler.start()
            with lock:
                measure_from[0] = time.monotonic()
            time.sleep(secs)
            gpu_stats = sampler.stop()

            with lock:
                per_cam_fps = {}
                per_cam_tracks = {}
                for cid in counts:
                    span = t_last[cid] - t_first[cid]
                    per_cam_fps[cid] = ((counts[cid] - 1) / span
                                        if span > 0 and counts[cid] > 1 else 0.0)
                    per_cam_tracks[cid] = tracks_sum[cid] / max(1, counts[cid])
                total = sum(counts.values())
                lag_sorted = sorted(lags)
            fps_vals = sorted(per_cam_fps.values())

            # 워커별 STATS·생존·담당 채널 fps 분포
            per_worker: dict[str, dict] = {}
            agg = {"batch_avg": 0.0, "infer_ms_avg": 0.0,
                   "queue_dropped": 0, "zmq_drops": 0, "gpumap": "?"}
            n_active = 0
            errors = {k: 0 for k in ("gst_errors", "cuda_oom",
                                     "decode_errors", "infer_fail")}
            for w in workers:
                if not w.cams:
                    continue
                log = w.logs(tail=400)
                st = parse_worker_stats(log, int(secs // 5))
                w_fps = [per_cam_fps.get(c, 0.0) for c in slot_cams[w.name]]
                per_worker[w.name] = {
                    "cams": len(slot_cams[w.name]),
                    "alive": w.running(),
                    "fps_mean": (sum(w_fps) / len(w_fps)) if w_fps else 0.0,
                    **st,
                }
                for k in ("batch_avg", "infer_ms_avg"):
                    agg[k] += st[k]
                for k in ("queue_dropped", "zmq_drops"):
                    agg[k] += st[k]
                agg["gpumap"] = st["gpumap"]
                for k, v in scan_errors(log).items():
                    errors[k] += v
                n_active += 1
            if n_active:
                agg["batch_avg"] /= n_active
                agg["infer_ms_avg"] /= n_active

            result.update({
                "per_cam_fps": per_cam_fps,
                "per_cam_tracks_mean": per_cam_tracks,
                "fps_min": fps_vals[0] if fps_vals else 0.0,
                "fps_median": fps_vals[len(fps_vals) // 2] if fps_vals else 0.0,
                "fps_mean": (sum(fps_vals) / len(fps_vals)) if fps_vals else 0.0,
                "total_throughput_fps": total / secs,
                "analyzed_frames": total,
                "lag_p50_s": lag_sorted[len(lag_sorted) // 2] if lag_sorted else 0.0,
                "lag_p95_s": (lag_sorted[int(len(lag_sorted) * 0.95)]
                              if lag_sorted else 0.0),
                "lag_max_s": lag_sorted[-1] if lag_sorted else 0.0,
                "worker_alive_after": all(w.running() for w in workers if w.cams),
                "per_worker": per_worker,
                **gpu_stats,
                **agg,
                "errors": errors,
            })
        if err:
            result["error"] = err
        return result
    finally:
        bridge.stop()
        for w in workers:
            w.stop()
        bridge.join(timeout=2.0)


def main() -> None:
    ap = argparse.ArgumentParser(description="DS 경로 한계 처리량 스윕")
    ap.add_argument("--tag", required=True, help="결과 파일 태그 (예: gpu1)")
    ap.add_argument("--gpu", type=int, default=1)
    ap.add_argument("--workers-per-gpu", type=int, default=1,
                    help="같은 GPU의 워커 프로세스 수 (GIL 분리 — 기본 1)")
    ap.add_argument("--channels", default="12,16,24,32,48,64")
    ap.add_argument("--secs", type=float, default=60.0)
    ap.add_argument("--warmup", type=float, default=30.0)
    ap.add_argument("--connect-timeout", type=float, default=180.0,
                    help="엔진 로드+전 채널 첫 수신 대기 상한")
    ap.add_argument("--fps", type=float, default=5.0)
    ap.add_argument("--streams", default=DEFAULT_STREAMS)
    ap.add_argument("--base", default="rtsp://127.0.0.1:8554")
    args = ap.parse_args()

    names = [s.strip() for s in args.streams.split(",") if s.strip()]
    urls = [(n if n.startswith("rtsp://") else f"{args.base}/{n}") for n in names]
    steps = [int(c) for c in args.channels.split(",")]

    results = []
    for n in steps:
        print(f"\n===== {n}채널 (GPU{args.gpu} ×{args.workers_per_gpu}워커, "
              f"target {args.fps:g}fps, {args.secs:g}s 측정) =====", flush=True)
        r = run_step(urls, n, args.gpu, args.secs, args.fps,
                     args.warmup, args.connect_timeout,
                     workers_per_gpu=args.workers_per_gpu)
        results.append(r)
        if "fps_mean" in r:
            print(f"  → min/med/mean fps = {r['fps_min']:.2f}/{r['fps_median']:.2f}/"
                  f"{r['fps_mean']:.2f} | 합계 {r['total_throughput_fps']:.1f}fps | "
                  f"batch {r['batch_avg']:.1f} | infer {r['infer_ms_avg']:.1f}ms | "
                  f"lag p50/p95 {r['lag_p50_s']:.2f}/{r['lag_p95_s']:.2f}s | "
                  f"SM {r['gpu_util_avg']:.0f}% NVDEC {r['nvdec_util_avg']:.0f}% | "
                  f"mem {r['gpu_mem_max_mib']:.0f}MiB", flush=True)
            for wname, pw in r.get("per_worker", {}).items():
                print(f"     {wname}: {pw['cams']}ch fps {pw['fps_mean']:.2f} | "
                      f"batch {pw['batch_avg']:.1f} | infer {pw['infer_ms_avg']:.1f}ms"
                      f" | qdrop {pw['queue_dropped']} | alive={pw['alive']}",
                      flush=True)
        if r.get("error"):
            print(f"  !! {r['error']}", flush=True)
            if not r.get("worker_alive_after", True) and r.get("errors", {}).get("cuda_oom"):
                print("  → OOM 한계 도달 — 이후 스텝 중단", flush=True)
                break
        time.sleep(5.0)   # 스텝 간 쿨다운

    out = OUT.with_name(OUT.name.format(tag=args.tag))
    out.write_text(json.dumps({
        "tag": args.tag, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "gpu": args.gpu, "target_fps": args.fps, "results": results,
    }, indent=2, ensure_ascii=False))
    print(f"\n저장: {out}")

    print(f"\n{'ch':>4s} {'min':>6s} {'med':>6s} {'mean':>6s} {'total':>7s} "
          f"{'batch':>6s} {'infer':>7s} {'lagp95':>7s} {'SM%':>4s} {'DEC%':>5s} "
          f"{'mem':>7s}")
    for r in results:
        if "fps_mean" not in r:
            print(f"{r['channels']:4d}  (실패: {r.get('error', '?')})")
            continue
        print(f"{r['channels']:4d} {r['fps_min']:6.2f} {r['fps_median']:6.2f} "
              f"{r['fps_mean']:6.2f} {r['total_throughput_fps']:7.1f} "
              f"{r['batch_avg']:6.1f} {r['infer_ms_avg']:6.1f}m "
              f"{r['lag_p95_s']:6.2f}s {r['gpu_util_avg']:4.0f} "
              f"{r['nvdec_util_avg']:5.0f} {r['gpu_mem_max_mib']:6.0f}M")


if __name__ == "__main__":
    main()
