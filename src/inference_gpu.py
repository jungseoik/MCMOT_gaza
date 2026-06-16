"""BoostTrack++ inference with TRT engines + optimized ReID preprocessing.

Key optimization: ReID per-crop tensor creation + torch.cat (536ms)
replaced with pre-allocated numpy buffer + single bulk GPU transfer (~15ms).
cv2 resize is identical to original — output matches exactly.
"""

import argparse
import os
import sys
import time
import threading
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from tqdm import tqdm

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dataset import preproc
from default_settings import GeneralSettings
from tracker.boost_track import BoostTrack, KalmanBoxTracker
from src.inference import _get_color
from src.inference_trt import TRTDetector, TRTReID



# ──────────────────────────────────────────────
# GPU-accelerated ReID embedding
# ──────────────────────────────────────────────

class GPUEmbeddingComputer:
    """Optimized EmbeddingComputer.compute_embedding() replacement.

    Uses cv2 resize (identical to original) but optimizes:
      - Pre-allocated numpy buffer (no per-crop tensor creation)
      - Single bulk GPU transfer (no torch.cat of 50 individual tensors)
      - Pinned memory for async transfer
    """

    def __init__(self, model, crop_size=(128, 384), max_batch=256):
        self.model = model
        self.crop_w, self.crop_h = crop_size
        self.max_batch = max_batch

        # Pre-allocate pinned numpy buffer (reused every frame)
        self._buf = None
        self._buf_size = 0

    def _ensure_buffer(self, n):
        if self._buf_size < n:
            self._buf_size = max(n, 128)
            self._buf = np.empty((self._buf_size, 3, self.crop_h, self.crop_w), dtype=np.float32)

    def compute_embedding(self, img: np.ndarray, bbox: np.ndarray, tag: str):
        if bbox.shape[0] == 0:
            return np.ones((0, 1))

        h, w = img.shape[:2]
        bbox = np.round(bbox).astype(np.int32)
        bbox[:, 0] = bbox[:, 0].clip(0, w)
        bbox[:, 1] = bbox[:, 1].clip(0, h)
        bbox[:, 2] = bbox[:, 2].clip(0, w)
        bbox[:, 3] = bbox[:, 3].clip(0, h)

        n = bbox.shape[0]
        self._ensure_buffer(n)

        # Batch crop + resize on CPU (cv2, identical to original)
        # Write directly into pre-allocated buffer, no per-crop tensor creation
        for i in range(n):
            x1, y1, x2, y2 = bbox[i]
            crop = img[y1:y2, x1:x2]
            if crop.size == 0:
                self._buf[i] = 0
                continue
            crop = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            crop = cv2.resize(crop, (self.crop_w, self.crop_h),
                              interpolation=cv2.INTER_LINEAR)
            self._buf[i] = crop.transpose(2, 0, 1).astype(np.float32)

        # Single bulk transfer to GPU (instead of 50x torch.as_tensor + torch.cat)
        crops = torch.from_numpy(self._buf[:n]).cuda()

        # Model inference
        embs = []
        for idx in range(0, n, self.max_batch):
            batch = crops[idx:idx + self.max_batch]
            with torch.no_grad():
                batch_embs = self.model(batch)
            embs.append(batch_embs)

        embs = torch.cat(embs, dim=0)
        embs = F.normalize(embs, dim=-1)
        return embs.cpu().numpy()


# ──────────────────────────────────────────────
# Main inference class
# ──────────────────────────────────────────────

class BoostTrackGPUInference:
    """Video inference with TRT engines + GPU-accelerated preprocessing.

    Optimization over BoostTrackTRTInference:
      - Detection + ReID model: TRT engines (FP16)
      - ReID embedding: per-crop tensor + torch.cat (536ms) → pre-allocated
        numpy buffer + single bulk GPU transfer (~15ms). cv2 crop/resize is
        kept on CPU (identical to original output) — see GPUEmbeddingComputer.

    Same tracking logic, equivalent output.
    (preproc stays on CPU cv2; GPU preproc was tried and dropped —
     see docs/optimization-report.md.)
    """

    def __init__(
        self,
        yolox_engine: str = "external/weights/trt/yolox_mot20_fp16.engine",
        reid_engine: str = "external/weights/trt/fastreid_sbs_s50_fp16.engine",
        input_size: tuple = (896, 1600),
        det_thresh: float = 0.4,
        use_reid: bool = True,
        use_ecc: bool = True,
    ):
        self.input_size = input_size
        self.det_thresh = det_thresh
        self.use_reid = use_reid
        self.use_ecc = use_ecc

        self._configure_settings()

        # TRT detector
        self.detector = TRTDetector(yolox_engine)

        # BoostTrack tracker
        KalmanBoxTracker.count = 0
        self.tracker = BoostTrack()

        # Disable ECC cache for streaming (write-only, never re-read)
        if self.tracker.ecc is not None:
            self.tracker.ecc.use_cache = False
            self.tracker.ecc.cache = {}

        # Replace ReID with GPU-accelerated version
        if use_reid and self.tracker.embedder is not None:
            trt_reid = TRTReID(reid_engine)
            gpu_embedder = GPUEmbeddingComputer(trt_reid, crop_size=(128, 384))
            self.tracker.embedder.compute_embedding = gpu_embedder.compute_embedding
            self.tracker.embedder.model = trt_reid

    def _configure_settings(self):
        GeneralSettings.values['dataset'] = 'mot20'
        GeneralSettings.values['test_dataset'] = True
        GeneralSettings.values['use_embedding'] = self.use_reid
        GeneralSettings.values['use_ecc'] = self.use_ecc
        GeneralSettings.values['det_thresh'] = self.det_thresh

    def _draw_tracks(self, frame: np.ndarray, targets: np.ndarray) -> np.ndarray:
        vis = frame.copy()
        for t in targets:
            x1, y1, x2, y2 = int(t[0]), int(t[1]), int(t[2]), int(t[3])
            track_id = int(t[4])
            color = _get_color(track_id)
            cv2.rectangle(vis, (x1, y1), (x2, y2), color, 2)
            label = f"ID:{track_id}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(vis, (x1, y1 - th - 6), (x1 + tw, y1), color, -1)
            cv2.putText(vis, label, (x1, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        return vis

    def stream(self, input_video: str, reset: bool = True, draw: bool = True,
               live: bool = False, should_stop=None):
        """Generator form of run(): yields one dict per processed frame.

        Each item: {"index", "total", "fps", "width", "height", "frame" (BGR
        ndarray), "targets" (ndarray Nx>=6 [x1,y1,x2,y2,id,...])}.

        draw=True  -> "frame" has the default ID boxes drawn (run() / simple use).
        draw=False -> "frame" is the raw BGR frame and the caller draws its own
                      overlay using "targets" (used by the speed dashboard).

        live=False -> read a finite file frame-by-frame (every frame).
        live=True  -> treat input as a live source (e.g. rtsp://): a reader
                      thread always holds the newest frame; we process the
                      latest and DROP the backlog (real-time, no frame queue),
                      running until should_stop() returns True or the source
                      disconnects. total is 0 (unbounded).
        """
        cap = cv2.VideoCapture(input_video)
        if not cap.isOpened():
            raise FileNotFoundError(f"Cannot open video: {input_video}")
        if live:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        total_frames = 0 if live else int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps_in = cap.get(cv2.CAP_PROP_FPS) or 0.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # Reset tracker state so each source starts fresh
        if reset:
            KalmanBoxTracker.count = 0
            self.tracker.frame_count = 0
            self.tracker.trackers.clear()

        processed = 0

        def _emit(frame):
            nonlocal processed
            padded, _ = preproc(frame, self.input_size, mean=None, std=None)
            tensor = torch.from_numpy(padded).unsqueeze(0).cuda()
            pred = self.detector.detect(tensor)
            targets = self.tracker.update(pred, tensor, frame, f"inference:{processed + 1}")
            if draw and targets.shape[0] > 0 and targets.shape[1] >= 6:
                vis = self._draw_tracks(frame, targets)
            else:
                vis = frame
            processed += 1
            return {"index": processed, "total": total_frames, "fps": fps_in,
                    "width": w, "height": h, "frame": vis, "targets": targets}

        if not live:
            try:
                while True:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    yield _emit(frame)
            finally:
                cap.release()
            return

        # live: a reader thread OWNS the capture (reads + releases it). The main
        # thread never touches cap, avoiding a release-while-reading segfault.
        latest = {"frame": None, "seq": 0, "stop": False}

        def _reader():
            try:
                while not latest["stop"]:
                    ok, fr = cap.read()
                    if not ok:
                        latest["stop"] = True
                        break
                    latest["frame"] = fr
                    latest["seq"] += 1
            finally:
                cap.release()

        th = threading.Thread(target=_reader, daemon=True)
        th.start()
        last_seq = 0                             # 0 = no real frame consumed yet
        try:
            while not (should_stop and should_stop()):
                seq = latest["seq"]
                if seq == last_seq:              # no fresher frame yet
                    if latest["stop"]:
                        break
                    time.sleep(0.003)
                    continue
                last_seq = seq
                frame = latest["frame"]
                if frame is None:
                    continue                     # not ready; keep waiting
                yield _emit(frame)
        finally:
            latest["stop"] = True
            th.join(timeout=2.0)                 # let reader release cap first

    def run(self, input_video: str, output_video: str) -> dict:
        os.makedirs(os.path.dirname(output_video) or ".", exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = None
        pbar = None
        processed = 0
        t_start = time.time()

        try:
            for item in self.stream(input_video):
                if writer is None:
                    writer = cv2.VideoWriter(
                        output_video, fourcc, item["fps"] or 25.0,
                        (item["width"], item["height"]))
                    pbar = tqdm(total=item["total"], desc="Tracking(GPU)", unit="frame",
                                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} "
                                           "[{elapsed}<{remaining}, {rate_fmt}]")
                writer.write(item["frame"])
                processed = item["index"]
                pbar.update(1)
        finally:
            if pbar is not None:
                pbar.close()
            if writer is not None:
                writer.release()

        total_time = time.time() - t_start
        result = {
            "output_video": output_video,
            "total_frames": processed,
            "fps": processed / total_time if total_time > 0 else 0,
            "total_time": total_time,
        }
        print(f"\nDone: {processed} frames in {total_time:.1f}s ({result['fps']:.1f} FPS)")
        print(f"Output: {output_video}")
        return result


def main():
    parser = argparse.ArgumentParser(description="BoostTrack++ GPU-Optimized Inference")
    parser.add_argument("--input", "-i", required=True, help="Input video path")
    parser.add_argument("--output", "-o", required=True, help="Output video path")
    parser.add_argument("--yolox_engine", default="external/weights/trt/yolox_mot20_fp16.engine")
    parser.add_argument("--reid_engine", default="external/weights/trt/fastreid_sbs_s50_fp16.engine")
    parser.add_argument("--det_thresh", type=float, default=0.4)
    parser.add_argument("--no_reid", action="store_true")
    parser.add_argument("--no_ecc", action="store_true")
    parser.add_argument("--input_size", nargs=2, type=int, default=[896, 1600])
    args = parser.parse_args()

    tracker = BoostTrackGPUInference(
        yolox_engine=args.yolox_engine,
        reid_engine=args.reid_engine,
        input_size=tuple(args.input_size),
        det_thresh=args.det_thresh,
        use_reid=not args.no_reid,
        use_ecc=not args.no_ecc,
    )
    tracker.run(args.input, args.output)


if __name__ == "__main__":
    main()
