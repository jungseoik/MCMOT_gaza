"""Benchmark: compare Original PyTorch vs TensorRT (FP32/FP16) inference.

Compares:
  1. Detection output accuracy (per-frame)
  2. Tracking output accuracy (per-frame)
  3. Inference speed (FPS)

Usage:
  python -m src.benchmark --input assets/sample1.mp4 --num_frames 50
"""

import argparse
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dataset import preproc
from default_settings import GeneralSettings
from tracker.boost_track import BoostTrack, KalmanBoxTracker


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def configure_settings(det_thresh=0.4):
    GeneralSettings.values['dataset'] = 'mot20'
    GeneralSettings.values['test_dataset'] = True
    GeneralSettings.values['use_embedding'] = True
    GeneralSettings.values['use_ecc'] = True
    GeneralSettings.values['det_thresh'] = det_thresh


def preprocess_frame(frame, input_size=(896, 1600)):
    padded, r = preproc(frame, input_size, mean=None, std=None)
    tensor = torch.from_numpy(padded).unsqueeze(0).cuda()
    return tensor


def read_frames(video_path, num_frames):
    cap = cv2.VideoCapture(video_path)
    frames = []
    for _ in range(num_frames):
        ret, frame = cap.read()
        if not ret:
            break
        frames.append(frame)
    cap.release()
    return frames


def det_to_numpy(det):
    if det is None:
        return np.empty((0, 5))
    if isinstance(det, torch.Tensor):
        return det.cpu().numpy()
    return np.array(det)


# ──────────────────────────────────────────────
# Run pipeline for a given detector + reid model
# ──────────────────────────────────────────────

def run_pipeline(frames, detector_fn, tracker, input_size=(896, 1600)):
    """Run detection + tracking on frames.
    Returns list of (detections_np, targets_np) per frame and total time.
    """
    results = []
    KalmanBoxTracker.count = 0
    tracker.frame_count = 0
    tracker.trackers.clear()

    # Warm-up
    tensor = preprocess_frame(frames[0], input_size)
    for _ in range(3):
        _ = detector_fn(tensor)
    torch.cuda.synchronize()

    t_start = time.time()
    for i, frame in enumerate(frames):
        tensor = preprocess_frame(frame, input_size)
        det = detector_fn(tensor)
        det_np = det_to_numpy(det)
        targets = tracker.update(det, tensor, frame, f"bench:{i+1}")
        results.append((det_np.copy(), targets.copy()))
    torch.cuda.synchronize()
    total_time = time.time() - t_start

    return results, total_time


# ──────────────────────────────────────────────
# Comparison metrics
# ──────────────────────────────────────────────

def _iou_matrix(a, b):
    """Compute IoU between two sets of boxes (N,4) and (M,4)."""
    x1 = np.maximum(a[:, 0:1], b[:, 0:1].T)
    y1 = np.maximum(a[:, 1:2], b[:, 1:2].T)
    x2 = np.minimum(a[:, 2:3], b[:, 2:3].T)
    y2 = np.minimum(a[:, 3:4], b[:, 3:4].T)
    inter = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    return inter / (union + 1e-6)


def compare_detections(ref_results, tgt_results, label):
    """Compare detection outputs frame-by-frame using IoU matching."""
    n = len(ref_results)
    count_diffs = []
    coord_diffs = []
    conf_diffs = []
    matched_ratios = []

    for i in range(n):
        ref_det, _ = ref_results[i]
        tgt_det, _ = tgt_results[i]
        count_diffs.append(abs(len(ref_det) - len(tgt_det)))

        if len(ref_det) > 0 and len(tgt_det) > 0:
            iou = _iou_matrix(ref_det[:, :4], tgt_det[:, :4])
            matched = 0
            used = set()
            for ri in range(len(ref_det)):
                best_j = -1
                best_iou = 0.5  # threshold
                for tj in range(len(tgt_det)):
                    if tj not in used and iou[ri, tj] > best_iou:
                        best_iou = iou[ri, tj]
                        best_j = tj
                if best_j >= 0:
                    used.add(best_j)
                    matched += 1
                    coord_diffs.append(np.abs(ref_det[ri, :4] - tgt_det[best_j, :4]).max())
                    conf_diffs.append(abs(ref_det[ri, 4] - tgt_det[best_j, 4]))
            total = max(len(ref_det), len(tgt_det))
            matched_ratios.append(matched / total if total > 0 else 1.0)
        elif len(ref_det) == 0 and len(tgt_det) == 0:
            matched_ratios.append(1.0)

    return {
        "label": label,
        "det_count_diff_mean": np.mean(count_diffs) if count_diffs else 0,
        "det_count_diff_max": np.max(count_diffs) if count_diffs else 0,
        "det_match_rate": np.mean(matched_ratios) if matched_ratios else 1.0,
        "det_coord_max_diff": np.max(coord_diffs) if coord_diffs else 0,
        "det_coord_mean_diff": np.mean(coord_diffs) if coord_diffs else 0,
        "det_conf_mean_diff": np.mean(conf_diffs) if conf_diffs else 0,
    }


def compare_tracks(ref_results, tgt_results, label):
    """Compare tracking outputs frame-by-frame."""
    n = len(ref_results)
    count_diffs = []
    id_match_rates = []
    coord_diffs = []

    for i in range(n):
        _, ref_trk = ref_results[i]
        _, tgt_trk = tgt_results[i]

        ref_n = ref_trk.shape[0] if ref_trk.ndim == 2 else 0
        tgt_n = tgt_trk.shape[0] if tgt_trk.ndim == 2 else 0
        count_diffs.append(abs(ref_n - tgt_n))

        if ref_n > 0 and tgt_n > 0:
            ref_ids = set(ref_trk[:, 4].astype(int))
            tgt_ids = set(tgt_trk[:, 4].astype(int))
            overlap = len(ref_ids & tgt_ids)
            union = len(ref_ids | tgt_ids)
            id_match_rates.append(overlap / union if union > 0 else 1.0)

            # Coordinate diff for matching IDs
            common_ids = ref_ids & tgt_ids
            if common_ids:
                diffs = []
                for tid in common_ids:
                    rb = ref_trk[ref_trk[:, 4].astype(int) == tid][0, :4]
                    tb = tgt_trk[tgt_trk[:, 4].astype(int) == tid][0, :4]
                    diffs.append(np.abs(rb - tb).max())
                coord_diffs.append(np.max(diffs))
        elif ref_n == 0 and tgt_n == 0:
            id_match_rates.append(1.0)

    return {
        "label": label,
        "trk_count_diff_mean": np.mean(count_diffs) if count_diffs else 0,
        "trk_count_diff_max": np.max(count_diffs) if count_diffs else 0,
        "trk_id_match_rate": np.mean(id_match_rates) if id_match_rates else 1.0,
        "trk_coord_max_diff": np.max(coord_diffs) if coord_diffs else 0,
        "trk_coord_mean_diff": np.mean(coord_diffs) if coord_diffs else 0,
    }


# ──────────────────────────────────────────────
# Pretty printer
# ──────────────────────────────────────────────

def print_header(title):
    w = 70
    print(f"\n{'=' * w}")
    print(f"  {title}")
    print(f"{'=' * w}")


def print_speed_table(results):
    print_header("Speed Comparison")
    print(f"  {'Mode':<25} {'Frames':>7} {'Time(s)':>9} {'FPS':>8} {'Speedup':>9}")
    print(f"  {'-'*25} {'-'*7} {'-'*9} {'-'*8} {'-'*9}")
    base_fps = results[0]["fps"]
    for r in results:
        speedup = r["fps"] / base_fps if base_fps > 0 else 0
        marker = "" if r is results[0] else f"x{speedup:.2f}"
        print(f"  {r['label']:<25} {r['frames']:>7} {r['time']:>9.1f} {r['fps']:>8.2f} {marker:>9}")


def print_accuracy_table(det_stats, trk_stats):
    print_header("Detection Accuracy (vs PyTorch Original)")
    print(f"  {'Comparison':<25} {'CountDiff':>10} {'Match%':>8} {'CoordMax':>10} {'CoordMean':>10} {'ConfDiff':>10}")
    print(f"  {'-'*25} {'-'*10} {'-'*8} {'-'*10} {'-'*10} {'-'*10}")
    for d in det_stats:
        print(f"  {d['label']:<25} {d['det_count_diff_mean']:>10.2f} "
              f"{d['det_match_rate']*100:>7.1f}% "
              f"{d['det_coord_max_diff']:>10.2f} {d['det_coord_mean_diff']:>10.2f} "
              f"{d['det_conf_mean_diff']:>10.4f}")

    print_header("Tracking Accuracy (vs PyTorch Original)")
    print(f"  {'Comparison':<25} {'Count Diff':>11} {'ID Match%':>11} {'Coord Max':>11} {'Coord Mean':>11}")
    print(f"  {'-'*25} {'-'*11} {'-'*11} {'-'*11} {'-'*11}")
    for t in trk_stats:
        print(f"  {t['label']:<25} {t['trk_count_diff_mean']:>11.2f} "
              f"{t['trk_id_match_rate']*100:>10.1f}% "
              f"{t['trk_coord_max_diff']:>11.2f} {t['trk_coord_mean_diff']:>11.2f}")


def print_verdict(det_stats, trk_stats):
    print_header("Verdict")
    for t in trk_stats:
        match = t["trk_id_match_rate"]
        coord = t["trk_coord_max_diff"]
        if match >= 0.99 and coord < 2.0:
            status = "PASS (identical)"
        elif match >= 0.95 and coord < 5.0:
            status = "PASS (negligible diff)"
        elif match >= 0.90:
            status = "WARN (minor diff)"
        else:
            status = "FAIL (significant diff)"
        print(f"  {t['label']:<25} -> {status}")
    print()


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Benchmark PyTorch vs TensorRT")
    parser.add_argument("--input", "-i", required=True, help="Input video path")
    parser.add_argument("--num_frames", "-n", type=int, default=50, help="Number of frames to benchmark")
    parser.add_argument("--det_thresh", type=float, default=0.4)
    parser.add_argument("--trt_dir", default="external/weights/trt")
    args = parser.parse_args()

    configure_settings(args.det_thresh)
    frames = read_frames(args.input, args.num_frames)
    print(f"Loaded {len(frames)} frames from {args.input}")

    speed_results = []
    all_results = {}

    n_variants = 4
    fp16_yolox = os.path.join(args.trt_dir, "yolox_mot20_fp16.engine")
    fp16_reid = os.path.join(args.trt_dir, "fastreid_sbs_s50_fp16.engine")
    fp32_yolox = os.path.join(args.trt_dir, "yolox_mot20_fp32.engine")
    fp32_reid = os.path.join(args.trt_dir, "fastreid_sbs_s50_fp32.engine")

    # ── 1. PyTorch Original ──
    print(f"\n[1/{n_variants}] Running PyTorch Original...")
    from external.adaptors.detector import Detector
    det_pt = Detector("yolox", "external/weights/bytetrack_x_mot20.tar", "mot20")
    det_pt.initialize_model()
    tracker_pt = BoostTrack()

    res_pt, time_pt = run_pipeline(frames, det_pt.detect, tracker_pt)
    all_results["pytorch"] = res_pt
    speed_results.append({"label": "PyTorch (Original)", "frames": len(frames),
                          "time": time_pt, "fps": len(frames) / time_pt})

    # ── 2. TRT FP32 ──
    if os.path.exists(fp32_yolox) and os.path.exists(fp32_reid):
        print(f"[2/{n_variants}] Running TensorRT FP32...")
        from src.inference_trt import TRTDetector, TRTReID
        det_fp32 = TRTDetector(fp32_yolox)
        KalmanBoxTracker.count = 0
        tracker_fp32 = BoostTrack()
        tracker_fp32.embedder.model = TRTReID(fp32_reid)

        res_fp32, time_fp32 = run_pipeline(frames, det_fp32.detect, tracker_fp32)
        all_results["trt_fp32"] = res_fp32
        speed_results.append({"label": "TRT FP32", "frames": len(frames),
                              "time": time_fp32, "fps": len(frames) / time_fp32})
    else:
        print(f"[2/{n_variants}] TRT FP32 engines not found, skipping.")

    # ── 3. TRT FP16 ──
    if os.path.exists(fp16_yolox) and os.path.exists(fp16_reid):
        print(f"[3/{n_variants}] Running TensorRT FP16...")
        from src.inference_trt import TRTDetector, TRTReID
        det_fp16 = TRTDetector(fp16_yolox)
        KalmanBoxTracker.count = 0
        tracker_fp16 = BoostTrack()
        tracker_fp16.embedder.model = TRTReID(fp16_reid)

        res_fp16, time_fp16 = run_pipeline(frames, det_fp16.detect, tracker_fp16)
        all_results["trt_fp16"] = res_fp16
        speed_results.append({"label": "TRT FP16", "frames": len(frames),
                              "time": time_fp16, "fps": len(frames) / time_fp16})
    else:
        print(f"[3/{n_variants}] TRT FP16 engines not found, skipping.")

    # ── 4. TRT FP16 + GPU Preproc ──
    if os.path.exists(fp16_yolox) and os.path.exists(fp16_reid):
        print(f"[4/{n_variants}] Running TRT FP16 + GPU Preproc...")
        from src.inference_gpu import GPUEmbeddingComputer
        from src.inference_trt import TRTDetector as TRTDet2, TRTReID as TRTReID2
        det_gpu = TRTDet2(fp16_yolox)
        KalmanBoxTracker.count = 0
        tracker_gpu = BoostTrack()
        trt_reid_gpu = TRTReID2(fp16_reid)
        gpu_emb = GPUEmbeddingComputer(trt_reid_gpu, crop_size=(128, 384))
        tracker_gpu.embedder.compute_embedding = gpu_emb.compute_embedding
        tracker_gpu.embedder.model = trt_reid_gpu

        res_gpu, time_gpu = run_pipeline(frames, det_gpu.detect, tracker_gpu)
        all_results["trt_fp16_gpu"] = res_gpu
        speed_results.append({"label": "TRT FP16 + GPU Preproc", "frames": len(frames),
                              "time": time_gpu, "fps": len(frames) / time_gpu})
    else:
        print(f"[4/{n_variants}] TRT FP16 engines not found, skipping.")

    # ── Results ──
    print_speed_table(speed_results)

    det_stats = []
    trk_stats = []
    ref = all_results["pytorch"]
    for key, label in [("trt_fp32", "TRT FP32 vs PyTorch"),
                        ("trt_fp16", "TRT FP16 vs PyTorch"),
                        ("trt_fp16_gpu", "TRT FP16+GPU vs PyTorch")]:
        if key in all_results:
            det_stats.append(compare_detections(ref, all_results[key], label))
            trk_stats.append(compare_tracks(ref, all_results[key], label))

    if det_stats:
        print_accuracy_table(det_stats, trk_stats)
        print_verdict(det_stats, trk_stats)


if __name__ == "__main__":
    main()
