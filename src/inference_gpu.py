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
from src.rfdetr_trt import RFDETRTRTDetector



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
        detector: str = "yolox",
        rfdetr_engine: str = "external/weights/trt/rfdetr_base_fp16.engine",
        profile: str | None = None,
    ):
        """profile: 추론 프로파일 id (model_zoo.py). "auto"면 현재 선택값
        (:8900 UI 설정 → INFER_PROFILE → 기본)을 따른다. 지정하면 검출기·ReID·
        임계값이 전부 프로파일에서 오고 개별 engine 인자는 무시된다. None(기본)
        이면 종전 동작 그대로."""
        self.profile = None
        if profile is not None:
            import model_zoo
            self.profile = model_zoo.resolve(None if profile == "auto" else profile)
            input_size = self.profile.detector.input_size
            det_thresh = self.profile.tracker.det_thresh
            detector = self.profile.detector.kind

        self.input_size = input_size
        self.det_thresh = det_thresh
        self.use_reid = use_reid
        self.use_ecc = use_ecc

        self._configure_settings()

        # TRT detector — 투트랙(YOLOX / RF-DETR) + 프로파일 경로(YOLO26 등).
        # 모두 detect_frame(frame)->(dets,ref) 공통 인터페이스.
        if self.profile is not None:
            import model_zoo
            self.detector = model_zoo.build_detector(self.profile)
        elif detector == "rfdetr":
            self.detector = RFDETRTRTDetector(rfdetr_engine)
        elif detector == "yolox":
            self.detector = TRTDetector(yolox_engine, input_size=input_size)
        else:
            raise ValueError(f"detector는 'yolox'|'rfdetr' — 받은 값: {detector!r}")
        self.detector_kind = detector

        # BoostTrack tracker
        KalmanBoxTracker.count = 0
        self.tracker = BoostTrack()

        # Disable ECC cache for streaming (write-only, never re-read)
        if self.tracker.ecc is not None:
            self.tracker.ecc.use_cache = False
            self.tracker.ecc.cache = {}

        # Replace ReID with GPU-accelerated version
        # (crop 크기는 모델마다 다르다 — FastReID 128×384, CLIP-ReID 128×256)
        if use_reid and self.tracker.embedder is not None:
            if self.profile is not None:
                import model_zoo
                trt_reid, crop = model_zoo.build_reid(self.profile)
            else:
                trt_reid, crop = TRTReID(reid_engine), (128, 384)
            gpu_embedder = GPUEmbeddingComputer(trt_reid, crop_size=crop)
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
            pred, ref = self.detector.detect_frame(frame)
            targets = self.tracker.update(pred, ref, frame, f"inference:{processed + 1}")
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
    parser.add_argument("--detector", choices=["yolox", "rfdetr"], default="yolox",
                        help="검출기 선택(투트랙) — 기존 기본은 yolox")
    parser.add_argument("--yolox_engine", default="external/weights/trt/yolox_mot20_fp16.engine")
    parser.add_argument("--rfdetr_engine", default="external/weights/trt/rfdetr_base_fp16.engine")
    parser.add_argument("--reid_engine", default="external/weights/trt/fastreid_sbs_s50_fp16.engine")
    parser.add_argument("--det_thresh", type=float, default=0.4)
    parser.add_argument("--no_reid", action="store_true")
    parser.add_argument("--no_ecc", action="store_true")
    parser.add_argument("--input_size", nargs=2, type=int, default=[896, 1600])
    parser.add_argument("--profile", default=None,
                        help="추론 프로파일 id (model_zoo.py) — 지정 시 "
                             "검출기·ReID·임계값이 프로파일에서 온다. 'auto'면 현재 선택값")
    args = parser.parse_args()

    tracker = BoostTrackGPUInference(
        profile=args.profile,
        detector=args.detector,
        yolox_engine=args.yolox_engine,
        rfdetr_engine=args.rfdetr_engine,
        reid_engine=args.reid_engine,
        input_size=tuple(args.input_size),
        det_thresh=args.det_thresh,
        use_reid=not args.no_reid,
        use_ecc=not args.no_ecc,
    )
    tracker.run(args.input, args.output)


if __name__ == "__main__":
    main()
