"""BoostTrack++ video inference module."""

import argparse
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from tqdm import tqdm

# Ensure project root is on sys.path
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dataset import preproc
from default_settings import GeneralSettings
from external.adaptors.detector import Detector
from tracker.boost_track import BoostTrack, KalmanBoxTracker


# Deterministic color palette for track IDs
_COLORS = [
    (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255),
    (0, 255, 255), (128, 0, 0), (0, 128, 0), (0, 0, 128), (128, 128, 0),
    (128, 0, 128), (0, 128, 128), (255, 128, 0), (255, 0, 128), (128, 255, 0),
    (0, 255, 128), (128, 0, 255), (0, 128, 255), (255, 128, 128), (128, 255, 128),
]


def _get_color(track_id: int) -> tuple:
    return _COLORS[int(track_id) % len(_COLORS)]


class BoostTrackInference:
    """Video inference with BoostTrack++ multi-object tracker.

    Args:
        detector_weights: Path to YOLOX detector weights.
        reid_weights: Path to ReID model weights.
        input_size: Model input size (H, W).
        det_thresh: Detection confidence threshold.
        use_reid: Enable ReID appearance features.
        use_ecc: Enable camera motion compensation.
    """

    def __init__(
        self,
        detector_weights: str = "external/weights/bytetrack_x_mot20.tar",
        input_size: tuple = (896, 1600),
        det_thresh: float = 0.4,
        use_reid: bool = True,
        use_ecc: bool = True,
    ):
        self.detector_weights = detector_weights
        self.input_size = input_size
        self.det_thresh = det_thresh
        self.use_reid = use_reid
        self.use_ecc = use_ecc

        self._configure_settings()
        self._load_detector()

    def _configure_settings(self):
        GeneralSettings.values['dataset'] = 'mot20'
        GeneralSettings.values['test_dataset'] = True
        GeneralSettings.values['use_embedding'] = self.use_reid
        GeneralSettings.values['use_ecc'] = self.use_ecc
        GeneralSettings.values['det_thresh'] = self.det_thresh

    def _load_detector(self):
        self.detector = Detector(
            model_type="yolox",
            path=self.detector_weights,
            dataset="mot20",
        )
        self.detector.initialize_model()

    def _preprocess(self, frame: np.ndarray) -> tuple:
        """Preprocess a BGR frame for YOLOX.

        Returns:
            (tensor, original_frame, scale_ratio)
        """
        padded, r = preproc(frame, self.input_size, mean=None, std=None)
        tensor = torch.from_numpy(padded).unsqueeze(0).cuda()
        return tensor, frame, r

    def _draw_tracks(self, frame: np.ndarray, targets: np.ndarray) -> np.ndarray:
        """Draw bounding boxes and track IDs on frame."""
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
        """Run full tracking pipeline on a video.

        Args:
            input_video: Path to input video file.
            output_video: Path to save output visualization video.

        Returns:
            dict with total_frames, fps, total_time.
        """
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

        # Reset tracker state
        KalmanBoxTracker.count = 0
        tracker = BoostTrack()

        processed = 0
        t_start = time.time()

        pbar = tqdm(total=total_frames, desc="Tracking", unit="frame",
                    bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                tensor, np_img, _ = self._preprocess(frame)
                tag = f"inference:{processed + 1}"

                pred = self.detector.detect(tensor)
                targets = tracker.update(pred, tensor, np_img, tag)

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
            self.detector.cache.clear()

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
    parser = argparse.ArgumentParser(description="BoostTrack++ Video Inference")
    parser.add_argument("--input", "-i", required=True, help="Input video path")
    parser.add_argument("--output", "-o", required=True, help="Output video path")
    parser.add_argument("--det_thresh", type=float, default=0.4, help="Detection threshold (default: 0.4)")
    parser.add_argument("--no_reid", action="store_true", help="Disable ReID features")
    parser.add_argument("--no_ecc", action="store_true", help="Disable camera motion compensation")
    parser.add_argument("--detector_weights", default="external/weights/bytetrack_x_mot20.tar")
    parser.add_argument("--input_size", nargs=2, type=int, default=[896, 1600], help="Model input H W")
    args = parser.parse_args()

    tracker = BoostTrackInference(
        detector_weights=args.detector_weights,
        input_size=tuple(args.input_size),
        det_thresh=args.det_thresh,
        use_reid=not args.no_reid,
        use_ecc=not args.no_ecc,
    )
    tracker.run(args.input, args.output)


if __name__ == "__main__":
    main()
