"""Shared clean drawing helpers (used by basic / speed / counter overlays).

Everything is anti-aliased (cv2.LINE_AA) and sized to the frame height, with
filled label tags + contrasting text — so labels stay crisp/legible at any
resolution. Track color comes from src.inference._get_color.
"""
import cv2

from src.inference import _get_color

FONT = cv2.FONT_HERSHEY_SIMPLEX


def box_thickness(h):
    return max(2, round(h / 360.0))


def font_scale(h):
    return max(0.5, h / 900.0)


def txt_thickness(fs):
    return max(1, round(fs * 1.6))


def _contrast(color):
    lum = 0.299 * color[2] + 0.587 * color[1] + 0.114 * color[0]
    return (0, 0, 0) if lum > 140 else (255, 255, 255)


def align_color(a):
    """Alignment cosine [-1..1] -> BGR. green=aligned, amber=cross, red=counter."""
    if a is None:
        return (160, 160, 160)
    if a > 0.4:
        return (0, 200, 0)
    if a < -0.2:
        return (0, 0, 230)
    return (0, 170, 255)


def draw_label(vis, x, y, text, color, fs, tt, below=False):
    """Filled tag + AA contrasting text. Tag sits above (default) or below y."""
    (tw, th), bl = cv2.getTextSize(text, FONT, fs, tt)
    if below:
        top = y
        cv2.rectangle(vis, (x, top), (x + tw + 8, top + th + bl + 4), color, -1, cv2.LINE_AA)
        org = (x + 4, top + th + 2)
    else:
        ty = max(y, th + bl + 2)
        cv2.rectangle(vis, (x, ty - th - bl - 4), (x + tw + 8, ty), color, -1, cv2.LINE_AA)
        org = (x + 4, ty - bl - 2)
    cv2.putText(vis, text, org, FONT, fs, _contrast(color), tt, cv2.LINE_AA)


def draw_id_box(vis, x1, y1, x2, y2, tid, label=None, fs=None, bt=None):
    """AA box + filled ID tag. Returns the track color."""
    h = vis.shape[0]
    fs = fs if fs is not None else font_scale(h)
    bt = bt if bt is not None else box_thickness(h)
    c = _get_color(tid)
    cv2.rectangle(vis, (x1, y1), (x2, y2), c, bt, cv2.LINE_AA)
    draw_label(vis, x1, y1, label if label is not None else f"ID {tid}",
               c, fs, txt_thickness(fs))
    return c
