"""BoostTrack++ video inference with TensorRT-accelerated models."""

import argparse
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
import tensorrt as trt
from tqdm import tqdm

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dataset import preproc
from default_settings import GeneralSettings
from tracker.boost_track import BoostTrack, KalmanBoxTracker
from yolox.utils import postprocess
from src.inference import _get_color


# ──────────────────────────────────────────────
# TensorRT engine wrapper
# ──────────────────────────────────────────────

class TRTEngine:
    """Minimal TensorRT engine wrapper."""

    def __init__(self, engine_path: str):
        self.logger = trt.Logger(trt.Logger.WARNING)
        with open(engine_path, "rb") as f:
            runtime = trt.Runtime(self.logger)
            self.engine = runtime.deserialize_cuda_engine(f.read())
        self.context = self.engine.create_execution_context()

        # Inspect bindings
        self.input_names = []
        self.output_names = []
        self.output_shapes = {}
        self.dynamic_batch = False

        for i in range(self.engine.num_io_tensors):
            name = self.engine.get_tensor_name(i)
            mode = self.engine.get_tensor_mode(name)
            shape = self.engine.get_tensor_shape(name)
            if mode == trt.TensorIOMode.INPUT:
                self.input_names.append(name)
                if shape[0] == -1:
                    self.dynamic_batch = True
            else:
                self.output_names.append(name)
                self.output_shapes[name] = tuple(shape)

        self.stream = torch.cuda.Stream()

    def __call__(self, input_tensor: torch.Tensor) -> list:
        batch_size = input_tensor.shape[0]

        # Set input shape for dynamic batch
        if self.dynamic_batch:
            actual_shape = tuple(input_tensor.shape)
            self.context.set_input_shape(self.input_names[0], actual_shape)

        self.context.set_tensor_address(self.input_names[0], input_tensor.data_ptr())

        # Allocate outputs
        outputs = []
        for name in self.output_names:
            shape = self.context.get_tensor_shape(name)
            out = torch.empty(tuple(shape), dtype=torch.float32, device="cuda")
            self.context.set_tensor_address(name, out.data_ptr())
            outputs.append(out)

        self.context.execute_async_v3(self.stream.cuda_stream)
        self.stream.synchronize()
        return outputs


# ──────────────────────────────────────────────
# TRT-accelerated detector
# ──────────────────────────────────────────────

class TRTDetector:
    """YOLOX detector using TensorRT engine."""

    def __init__(self, engine_path: str, num_classes=1, conf_thresh=0.1, nms_thresh=0.7):
        self.engine = TRTEngine(engine_path)
        self.num_classes = num_classes
        self.conf_thresh = conf_thresh
        self.nms_thresh = nms_thresh

    def detect(self, img_tensor: torch.Tensor):
        """Run detection. Input: (1, 3, H, W) tensor on CUDA.
        Returns: (N, 5) tensor [x1, y1, x2, y2, conf] or None.
        """
        raw = self.engine(img_tensor.float())[0]
        pred = postprocess(raw, self.num_classes, self.conf_thresh, self.nms_thresh)[0]
        if pred is not None:
            return torch.cat((pred[:, :4], (pred[:, 4] * pred[:, 5])[:, None]), dim=1)
        return None


# ──────────────────────────────────────────────
# TRT-accelerated ReID
# ──────────────────────────────────────────────

class TRTReID(torch.nn.Module):
    """FastReID model replaced with TensorRT engine.
    Drop-in replacement for FastReID class in fastreid_adaptor.py.
    """

    def __init__(self, engine_path: str):
        super().__init__()
        self.engine = TRTEngine(engine_path)
        self.pH, self.pW = 384, 128

    def forward(self, batch: torch.Tensor):
        outputs = self.engine(batch.float())
        return outputs[0]


# ──────────────────────────────────────────────
# Main inference class
# ──────────────────────────────────────────────

class BoostTrackTRTInference:
    """Video inference with TensorRT-accelerated YOLOX + FastReID.

    Args:
        yolox_engine: Path to YOLOX TRT engine.
        reid_engine: Path to FastReID TRT engine.
        input_size: Model input size (H, W).
        det_thresh: Detection confidence threshold.
        use_reid: Enable ReID appearance features.
        use_ecc: Enable camera motion compensation.
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

        # BoostTrack tracker (creates EmbeddingComputer internally)
        KalmanBoxTracker.count = 0
        self.tracker = BoostTrack()

        # Replace PyTorch ReID model with TRT engine
        if use_reid and self.tracker.embedder is not None:
            self.tracker.embedder.model = TRTReID(reid_engine)

    def _configure_settings(self):
        GeneralSettings.values['dataset'] = 'mot20'
        GeneralSettings.values['test_dataset'] = True
        GeneralSettings.values['use_embedding'] = self.use_reid
        GeneralSettings.values['use_ecc'] = self.use_ecc
        GeneralSettings.values['det_thresh'] = self.det_thresh

    def _preprocess(self, frame: np.ndarray):
        padded, r = preproc(frame, self.input_size, mean=None, std=None)
        tensor = torch.from_numpy(padded).unsqueeze(0).cuda()
        return tensor, frame, r

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

    def run(self, input_video: str, output_video: str) -> dict:
        """Run full tracking pipeline on a video."""
        cap = cv2.VideoCapture(input_video)
        if not cap.isOpened():
            raise FileNotFoundError(f"Cannot open video: {input_video}")

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps_in = cap.get(cv2.CAP_PROP_FPS)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        os.makedirs(os.path.dirname(output_video) or ".", exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(output_video, fourcc, fps_in, (w, h))

        # Reset tracker
        KalmanBoxTracker.count = 0
        self.tracker.frame_count = 0
        self.tracker.trackers.clear()

        processed = 0
        t_start = time.time()

        pbar = tqdm(total=total_frames, desc="Tracking(TRT)", unit="frame",
                    bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                tensor, np_img, _ = self._preprocess(frame)
                tag = f"inference:{processed + 1}"

                pred = self.detector.detect(tensor)
                targets = self.tracker.update(pred, tensor, np_img, tag)

                if targets.shape[0] > 0 and targets.shape[1] >= 6:
                    vis = self._draw_tracks(frame, targets)
                else:
                    vis = frame

                writer.write(vis)
                processed += 1
                pbar.update(1)
        finally:
            pbar.close()
            cap.release()
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
    parser = argparse.ArgumentParser(description="BoostTrack++ TensorRT Inference")
    parser.add_argument("--input", "-i", required=True, help="Input video path")
    parser.add_argument("--output", "-o", required=True, help="Output video path")
    parser.add_argument("--yolox_engine", default="external/weights/trt/yolox_mot20_fp16.engine")
    parser.add_argument("--reid_engine", default="external/weights/trt/fastreid_sbs_s50_fp16.engine")
    parser.add_argument("--det_thresh", type=float, default=0.4)
    parser.add_argument("--no_reid", action="store_true")
    parser.add_argument("--no_ecc", action="store_true")
    parser.add_argument("--input_size", nargs=2, type=int, default=[896, 1600])
    args = parser.parse_args()

    tracker = BoostTrackTRTInference(
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
