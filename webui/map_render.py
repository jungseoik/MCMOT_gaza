"""Server-side 2D top-down map renderer (Python/OpenCV port of index.html drawMap).

Takes a SpeedEstimator.metrics() dict and paints a white top-down map with one
dot + motion-direction arrow per tracked object. Colors use the SAME
src.inference._get_color(id) as the left ID-overlay panel, so the same track ID
is the same color on both sides (left video <-> right map).

Without calibration (no ROI/homography) the positions are image-plane foot
pixels (perspective, not true metric top-down); the motion arrows are still
valid. With homography set, map_bounds/positions are true ground meters.

Note: OpenCV's Hershey fonts are ASCII-only, so all labels are English.
"""
import cv2
import numpy as np

from src.inference import _get_color
from webui import draw_utils as du

FONT = cv2.FONT_HERSHEY_SIMPLEX


def _panel_label(img, text):
    fs, tt = 0.5, 1
    (tw, th), bl = cv2.getTextSize(text, FONT, fs, tt)
    cv2.rectangle(img, (0, 0), (tw + 12, th + bl + 8), (40, 40, 40), -1)
    cv2.putText(img, text, (6, th + 4), FONT, fs, (255, 255, 255), tt, cv2.LINE_AA)


def render_map(m, width, height, label="2D MAP - motion vectors"):
    """Render one map frame (BGR ndarray HxWx3) from a metrics dict."""
    W, H = int(width), int(height)
    img = np.full((H, W, 3), 255, np.uint8)
    objs = m.get("objects") or []
    b = m.get("map_bounds")
    if not b:                                  # auto-fit to current points (+15% pad)
        if not objs:
            cv2.putText(img, "no objects", (16, 28), FONT, 0.6,
                        (158, 148, 139), 1, cv2.LINE_AA)
            _panel_label(img, label)
            return img
        xs = [o["mx"] for o in objs]
        ys = [o["my"] for o in objs]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        px = (x1 - x0) * 0.15 + 1
        py = (y1 - y0) * 0.15 + 1
        b = [x0 - px, y0 - py, x1 + px, y1 + py]

    bx0, by0, bx1, by1 = b
    bw = max(1e-3, bx1 - bx0)
    bh = max(1e-3, by1 - by0)
    pad = 24
    s = min((W - 2 * pad) / bw, (H - 2 * pad) / bh)
    ox = (W - bw * s) / 2 - bx0 * s
    oy = (H - bh * s) / 2 - by0 * s
    TX = lambda x: int(round(ox + x * s))      # noqa: E731
    TY = lambda y: int(round(oy + y * s))      # noqa: E731

    cv2.rectangle(img, (TX(bx0), TY(by0)), (TX(bx1), TY(by1)),
                  (208, 215, 222), 1, cv2.LINE_AA)
    tag = "top-down (m)" if m.get("map_metric") else "image plane (no calib)"
    cv2.putText(img, tag, (TX(bx0) + 4, TY(by0) + 16), FONT, 0.45,
                (158, 148, 139), 1, cv2.LINE_AA)

    has_align = bool(m.get("has_align"))
    for o in objs:
        X, Y = TX(o["mx"]), TY(o["my"])
        c = _get_color(int(o["id"]))           # dot keeps track-ID color
        dx, dy = o.get("dirx", 0.0), o.get("diry", 0.0)
        if dx or dy:
            L = 18
            ex, ey = int(round(X + dx * L)), int(round(Y + dy * L))
            # arrow colored by alignment when active, else track color
            ac = du.align_color(o.get("align")) if has_align else c
            cv2.arrowedLine(img, (X, Y), (ex, ey), ac, 2, cv2.LINE_AA, 0, 0.45)
        cv2.circle(img, (X, Y), 4, c, -1, cv2.LINE_AA)

    if has_align and m.get("ref_dir"):         # long reference vector (no text readout)
        rd = m["ref_dir"]
        if objs:
            cxw = sum(o["mx"] for o in objs) / len(objs)
            cyw = sum(o["my"] for o in objs) / len(objs)
        else:
            cxw, cyw = (bx0 + bx1) / 2, (by0 + by1) / 2
        Xc, Yc, L = TX(cxw), TY(cyw), 46
        ex, ey = int(round(Xc + rd[0] * L)), int(round(Yc + rd[1] * L))
        cv2.arrowedLine(img, (Xc, Yc), (ex, ey), (250, 120, 0), 4, cv2.LINE_AA, 0, 0.3)
        cv2.circle(img, (Xc, Yc), 3, (250, 120, 0), -1, cv2.LINE_AA)
        cv2.putText(img, "EVAC", (ex + 5, ey + 4), FONT, 0.45, (250, 120, 0), 1, cv2.LINE_AA)

    _panel_label(img, label)
    return img
