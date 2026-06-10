"""End-to-end MULTI-CHANNEL latency benchmark of the CURRENT pipeline.

Answers: "현재 기능(검출+추적+ReID)을 1→15채널 배치로 돌리면 한 라운드(채널마다 1프레임)
처리 지연이 채널 수에 따라 얼마나 늘고, 그 결과 채널당 fps는 얼마인가?"

Per round (N channels):
  1. preproc N frames (CPU)
  2. ONE batched detection GPU call on (N,3,896,1600)  <- 진짜 배치
  3. N x tracker.update()  (per-channel: ECC + ReID + association, stateful)
Measures steady-state wall time per round T(N) → per-channel fps = 1/T(N),
total throughput = N/T(N).

Run: CUDA_VISIBLE_DEVICES=1 python docs/reports/bench/bench_pipeline_channels.py
"""
import os
import sys
import json
import time
from pathlib import Path

import cv2
import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dataset import preproc                                   # noqa: E402
from default_settings import GeneralSettings                  # noqa: E402
from tracker.boost_track import BoostTrack, KalmanBoxTracker  # noqa: E402
from src.inference_trt import TRTDetector, TRTReID            # noqa: E402
from src.inference_gpu import GPUEmbeddingComputer            # noqa: E402
from yolox.utils import postprocess                           # noqa: E402

TRT = ROOT / "external/weights/trt"
YOLOX_DYN = TRT / "yolox_mot20_fp16_dynamic.engine"
REID = TRT / "fastreid_sbs_s50_fp16.engine"
SAMPLE = ROOT / "assets/sample1.mp4"
INPUT_HW = (896, 1600)

CHANNELS = [1, 2, 4, 6, 8, 10, 12, 15]
POOL = 60            # frames preloaded from sample1
WARMUP = 5
ROUNDS = 20


def configure():
    GeneralSettings.values['dataset'] = 'mot20'
    GeneralSettings.values['test_dataset'] = True
    GeneralSettings.values['use_embedding'] = True
    GeneralSettings.values['use_ecc'] = True
    GeneralSettings.values['det_thresh'] = 0.4


def make_tracker(reid_model):
    """One BoostTrack instance wired with GPU ReID (same as BoostTrackGPUInference)."""
    t = BoostTrack()
    if t.ecc is not None:
        t.ecc.use_cache = False
        t.ecc.cache = {}
    if t.embedder is not None:
        emb = GPUEmbeddingComputer(reid_model, crop_size=(128, 384))
        t.embedder.compute_embedding = emb.compute_embedding
        t.embedder.model = reid_model
    return t


def load_frames():
    cap = cv2.VideoCapture(str(SAMPLE))
    frames, tensors = [], []
    while len(frames) < POOL:
        ok, fr = cap.read()
        if not ok:
            break
        frames.append(fr)
        padded, _ = preproc(fr, INPUT_HW, mean=None, std=None)
        tensors.append(torch.from_numpy(padded).unsqueeze(0).cuda())
    cap.release()
    return frames, tensors


def batched_detect(detector, batch_tensor):
    raw = detector.engine(batch_tensor.float())[0]          # (N, anchors, 6)
    preds = postprocess(raw, detector.num_classes,
                        detector.conf_thresh, detector.nms_thresh)
    out = []
    for p in preds:
        if p is not None:
            out.append(torch.cat((p[:, :4], (p[:, 4] * p[:, 5])[:, None]), dim=1))
        else:
            out.append(None)
    return out


def main():
    configure()
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    frames, tensors = load_frames()
    print(f"Loaded {len(frames)} frames ({frames[0].shape[1]}x{frames[0].shape[0]})")

    detector = TRTDetector(str(YOLOX_DYN))
    reid_model = TRTReID(str(REID))

    nmax = max(CHANNELS)
    KalmanBoxTracker.count = 0
    trackers = [make_tracker(reid_model) for _ in range(nmax)]

    results = []
    for N in CHANNELS:
        # fresh tracker state for this N
        for c in range(N):
            trackers[c].frame_count = 0
            trackers[c].trackers.clear()
        KalmanBoxTracker.count = 0

        det_ms = trk_ms = pre_ms = 0.0
        tot = 0.0
        for r in range(WARMUP + ROUNDS):
            measure = r >= WARMUP
            # pick a moving frame per channel
            idx = [(c * 7 + r) % len(frames) for c in range(N)]

            t0 = time.perf_counter()
            # (1) tensors are pre-padded (preproc EXCLUDED here; measured
            #     separately ~29ms/frame in bench_batch, CPU, parallelizable).
            #     This round = detection(batched) + per-channel tracking only.
            batch = torch.cat([tensors[idx[c]] for c in range(N)], dim=0)
            torch.cuda.synchronize()
            t1 = time.perf_counter()

            # (2) batched detection
            preds = batched_detect(detector, batch)
            torch.cuda.synchronize()
            t2 = time.perf_counter()

            # (3) per-channel tracking
            for c in range(N):
                trackers[c].update(preds[c], tensors[idx[c]],
                                   frames[idx[c]], f"ch{c}:{r}")
            torch.cuda.synchronize()
            t3 = time.perf_counter()

            if measure:
                pre_ms += (t1 - t0) * 1000
                det_ms += (t2 - t1) * 1000
                trk_ms += (t3 - t2) * 1000
                tot += (t3 - t0) * 1000

        tot /= ROUNDS
        det_ms /= ROUNDS
        trk_ms /= ROUNDS
        pre_ms /= ROUNDS
        per_ch_fps = 1000.0 / tot
        total_fps = N * per_ch_fps
        results.append({
            "channels": N, "round_ms": round(tot, 1),
            "detect_ms": round(det_ms, 1), "track_ms": round(trk_ms, 1),
            "stack_ms": round(pre_ms, 1),
            "per_channel_fps": round(per_ch_fps, 2),
            "total_fps": round(total_fps, 1),
        })
        print(f"  N={N:2d}ch | round {tot:7.1f} ms "
              f"(det {det_ms:5.1f} + track {trk_ms:6.1f}) | "
              f"per-ch {per_ch_fps:5.2f} fps | total {total_fps:6.1f} fps")

    out = Path(__file__).resolve().parent / "results_pipeline.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
