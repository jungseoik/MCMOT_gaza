"""Depth extraction CLI — runs in the separate `da3` conda env (Depth-Anything-3).

The web server (boosttrack env) shells out to this once, at the start of a
depth-mode job, to get a metric depth map for the first frame. Kept as a thin
standalone script because Depth-Anything-3 (torch cu128, numpy<2) cannot share
the boosttrack env (torch cu130, numpy 2.x).

Usage (invoked by the server):
  <da3-python> webui/da3_depth.py --image IN.png --out-depth D.npy [--out-vis VIS.png]

Outputs:
  - D.npy   : float32 metric depth (meters), resized to the input resolution
  - VIS.png : colorized depth preview (optional, for the UI confirm step)
"""
import argparse
import sys

import numpy as np
import cv2
import torch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out-depth", required=True)
    ap.add_argument("--out-vis", default=None)
    ap.add_argument("--model", default="depth-anything/DA3METRIC-LARGE")
    args = ap.parse_args()

    from depth_anything_3.api import DepthAnything3  # heavy; import after argparse

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = DepthAnything3.from_pretrained(args.model).to(dev)
    model.eval()
    with torch.no_grad():
        pred = model.inference([args.image])

    depth = np.asarray(pred.depth)[0].astype(np.float32)   # (h,w) meters @ model res
    img = cv2.imread(args.image)
    if img is None:
        print("ERROR cannot read image", file=sys.stderr); sys.exit(2)
    H, W = img.shape[:2]
    depth = cv2.resize(depth, (W, H), interpolation=cv2.INTER_LINEAR)
    np.save(args.out_depth, depth)

    if args.out_vis:
        dn = (depth - depth.min()) / (depth.max() - depth.min() + 1e-6)
        vis = cv2.applyColorMap((dn * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
        cv2.imwrite(args.out_vis, vis)

    print("OK depth shape=%s min=%.3f max=%.3f median=%.3f"
          % (depth.shape, float(depth.min()), float(depth.max()),
             float(np.median(depth))))


if __name__ == "__main__":
    main()
