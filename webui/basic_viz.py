"""Basic visualization for the download-only mode: ID + box, nothing else.

Uses the shared clean drawing helpers (AA, resolution-scaled, filled tags) so
the full-resolution download looks crisp. Color is per-track-ID.
"""
from webui.draw_utils import draw_id_box


def draw_basic(frame, targets):
    vis = frame.copy()
    if targets is not None and getattr(targets, "ndim", 0) == 2:
        for tg in targets:
            if tg.shape[0] < 5:
                continue
            x1, y1, x2, y2 = int(tg[0]), int(tg[1]), int(tg[2]), int(tg[3])
            draw_id_box(vis, x1, y1, x2, y2, int(tg[4]))
    return vis
