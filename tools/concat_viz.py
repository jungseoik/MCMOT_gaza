"""Side-by-side visualization: LEFT = ID-overlay tracking video,
RIGHT = 2D top-down map with per-object motion-direction vectors.

For each input video it runs BoostTrack++ (TRT) inference, draws the two panels
(reusing webui.speed.annotate + webui.map_render.render_map), concatenates them
horizontally, writes an mp4, then re-encodes to H.264 so it plays everywhere.

Usage:
    python tools/concat_viz.py                      # default 2 smoke-test samples
    python tools/concat_viz.py a.mp4 b.mp4           # explicit files
    python tools/concat_viz.py /path/to/folder       # all *.mp4 in a folder
    python tools/concat_viz.py vids/ --out results/myrun --max-width 1280

Output: <out>/<name>_concat.mp4   (default out = results/clab_concat)
"""
import os
import sys
import argparse
import subprocess
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.inference_gpu import BoostTrackGPUInference   # noqa: E402
from webui.speed import SpeedEstimator, annotate        # noqa: E402
from webui.map_render import render_map                 # noqa: E402

OUT_DIR = ROOT / "results" / "clab_concat"
MAX_PANEL_W = 960          # downscale wide/4K sources per panel

# 기본 샘플 경로(이 서버 전용) — 다른 서버는 CLI 인자로 입력 지정하거나
# SAMPLE_DIR 환경변수로 오버라이드. (track-viz 헬퍼 — 실행 필수 경로 아님)
SAMPLE_DIR = Path(os.environ.get(
    "SAMPLE_DIR",
    "/home/pia/data/nas_200tb/ai-public/tracking_dataset/"
    "samsung_clab_sample/sample_example"))
DEFAULT_INPUTS = [SAMPLE_DIR / "sample.mp4", SAMPLE_DIR / "in_out_counting.mp4"]

FONT = cv2.FONT_HERSHEY_SIMPLEX


def _even(n):
    n = int(round(n))
    return n if n % 2 == 0 else n + 1


def _label(img, text):
    fs, tt = 0.5, 1
    (tw, th), bl = cv2.getTextSize(text, FONT, fs, tt)
    cv2.rectangle(img, (0, 0), (tw + 12, th + bl + 8), (40, 40, 40), -1)
    cv2.putText(img, text, (6, th + 4), FONT, fs, (255, 255, 255), tt, cv2.LINE_AA)


def _transcode_h264(path):
    src = str(path)
    tmp = src + ".tmp.mp4"
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", "-an", tmp],
            capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        print("  [warn] ffmpeg not found; leaving mp4v output")
        return
    if r.returncode == 0 and os.path.exists(tmp):
        os.replace(tmp, src)
    elif os.path.exists(tmp):
        os.remove(tmp)


def process(model, src_path, out_path, max_panel_w=MAX_PANEL_W, reference_vec=None,
            left_only=False):
    """left_only=True 면 원본 해상도로 좌측 ID-추적 오버레이만 저장(2D맵·다운스케일 없음)."""
    est = writer = None
    pw = ph = 0
    fps = 25.0
    for item in model.stream(str(src_path), draw=False):
        if est is None:
            fps = item["fps"] or 25.0
            W, H = item["width"], item["height"]
            est = SpeedEstimator(fps, frame_size=(W, H), reference_vec=reference_vec)
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            if left_only:
                pw, ph = _even(W), _even(H)          # 원본 해상도 그대로
                writer = cv2.VideoWriter(str(out_path), fourcc, fps, (pw, ph))
                print(f"  {W}x{H}@{fps:.1f} -> overlay-only {pw}x{ph}, total {item['total']} frames")
            else:
                scale = min(1.0, max_panel_w / W)
                pw, ph = _even(W * scale), _even(H * scale)
                writer = cv2.VideoWriter(str(out_path), fourcc, fps, (2 * pw, ph))
                print(f"  {W}x{H}@{fps:.1f} -> panel {pw}x{ph}, total {item['total']} frames")

        t = item["index"] / fps
        present = est.update(t, item["targets"])

        left = annotate(item["frame"], item["targets"], present, est)
        if left_only:
            if (left.shape[1], left.shape[0]) != (pw, ph):
                left = cv2.resize(left, (pw, ph), interpolation=cv2.INTER_AREA)
            _label(left, "ID TRACKING")
            writer.write(left)
        else:
            m = est.metrics(present)
            left = cv2.resize(left, (pw, ph), interpolation=cv2.INTER_AREA)
            _label(left, "ID TRACKING")
            right = render_map(m, pw, ph)
            combo = np.hstack([left, right])
            cv2.line(combo, (pw, 0), (pw, ph), (90, 90, 90), 2, cv2.LINE_AA)
            writer.write(combo)

        if item["index"] % 100 == 0:
            print(f"    frame {item['index']}/{item['total']}")

    if writer is not None:
        writer.release()
    print(f"  encoding H.264 -> {out_path}")
    _transcode_h264(out_path)


def _expand(paths):
    """Expand each path: a directory -> its *.mp4 (sorted), a file -> itself."""
    out = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out.extend(sorted(p.glob("*.mp4")))
        elif p.exists():
            out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser(description="ID-tracking + 2D-map concat visualizer")
    ap.add_argument("inputs", nargs="*", help="video files or folders (default: 2 samples)")
    ap.add_argument("--out", default=str(OUT_DIR), help="output dir")
    ap.add_argument("--max-width", type=int, default=MAX_PANEL_W, help="per-panel max width px")
    ap.add_argument("--align", default=None,
                    help="alignment ref vector 'tx,ty,hx,hy' in ORIGINAL px (tail->head)")
    ap.add_argument("--left-only", action="store_true",
                    help="원본 해상도로 좌측 ID-추적 오버레이만 저장(2D맵 패널·다운스케일 없음)")
    args = ap.parse_args()

    ref_vec = None
    if args.align:
        v = [float(x) for x in args.align.split(",")]
        ref_vec = [[v[0], v[1]], [v[2], v[3]]]

    inputs = _expand(args.inputs) if args.inputs else [p for p in DEFAULT_INPUTS if p.exists()]
    if not inputs:
        print("no valid input videos")
        return
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[concat_viz] {len(inputs)} video(s) -> {out_dir}  (panel<= {args.max_width}px)")
    print("[concat_viz] loading TRT model ...")
    model = BoostTrackGPUInference()
    for src_path in inputs:
        suffix = "_track" if args.left_only else "_concat"
        out_path = out_dir / f"{src_path.stem}{suffix}.mp4"
        print(f"[concat_viz] {src_path.name}")
        process(model, src_path, out_path, max_panel_w=args.max_width,
                reference_vec=ref_vec, left_only=args.left_only)
    print(f"[concat_viz] done -> {out_dir}")


if __name__ == "__main__":
    main()
