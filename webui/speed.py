"""Per-object speed estimation + dashboard metrics for the live web UI.

Sliding-window per-object speed estimator.

Pixel→metric mapping is pluggable (single source of truth for "how fast"):
  - homography : image foot point → ground meters (perspective-correct)  → km/h
  - pixels_per_meter : single global scale (no perspective correction)    → km/h
  - neither    : raw pixel speed                                          → px/s

The object reference point is the **foot** (bbox bottom-center), which is what
touches the ground — required for any ground-plane (homography/depth) mapping.
"""
from collections import deque

import cv2
import numpy as np

from src.inference import _get_color
from webui import draw_utils as du


class SpeedEstimator:
    def __init__(self, fps, pixels_per_meter=None, homography=None, roi=None,
                 frame_size=None, world_area_m2=None, reference_vec=None,
                 units_per_px=1.0, reference_vec_is_world=False, map_size=None,
                 window_sec=1.0, min_move_px=2.0):
        self.fps = float(fps) if fps else 25.0
        self.ppm = pixels_per_meter            # px per meter (linear mode)
        self.H = (np.array(homography, dtype=np.float64).reshape(3, 3)
                  if homography is not None else None)
        self.roi = (np.array(roi, dtype=np.int32).reshape(-1, 2)
                    if roi else None)          # (4,2) polygon
        self.fw, self.fh = frame_size or (0, 0)
        self.world_area_m2 = world_area_m2     # ROI real area (homography mode)
        # map-registration mode: H outputs MAP pixels (not meters); multiply by
        # units_per_px (m/px) for real distance, and the map view = full map image.
        self.units_per_px = float(units_per_px)
        self.map_size = map_size               # (W,H) of map image, or None
        self.window_sec = window_sec           # sliding window length in SECONDS
        self.min_move_px = min_move_px
        self.metric = (self.H is not None) or (self.ppm is not None)
        # "moving" threshold in display unit (km/h or px/s)
        self.move_thresh = 0.5 if self.metric else 3.0
        # time is wall-clock seconds; callers pass frame_idx/fps (file) or a
        # monotonic timestamp (RTSP, where frames are skipped non-uniformly).
        self.history = {}      # id -> deque[(t, fx, fy)]  (t in seconds)
        self.speed = {}        # id -> current speed (km/h or px/s)
        self.first_seen = {}   # id -> t (first seen, for dwell)
        self.last_t = 0.0
        self.seen_ids = set()
        self.prev = {}         # id -> (t, speed) for acceleration
        self.accel = {}

        # Optional alignment reference: a directed vector (tail->head) the user
        # drew on the map, pointing the recommended evacuation direction. Only
        # active when provided -> alignment is opt-in (computed/shown on demand).
        self.reference_vec = reference_vec     # [[tx,ty],[hx,hy]] image px, or None
        self.ref_dir = None                    # unit dir in the SAME space as _obj_map
        if reference_vec is not None:
            (tx, ty), (hx, hy) = reference_vec
            if self.H is not None and not reference_vec_is_world:  # image -> output
                tx, ty = self._world(tx, ty)
                hx, hy = self._world(hx, hy)
            # reference_vec_is_world: arrow already drawn in output(map) space -> as-is
            dx, dy = hx - tx, hy - ty
            L = (dx * dx + dy * dy) ** 0.5
            if L > 1e-6:
                self.ref_dir = (dx / L, dy / L)
        self.has_align = self.ref_dir is not None

    @property
    def unit(self):
        return "km/h" if self.metric else "px/s"

    def _foot(self, x1, y1, x2, y2):
        return ((x1 + x2) / 2.0, float(y2))     # bottom-center touches ground

    def _in_roi(self, x, y):
        if self.roi is None:
            return True
        return cv2.pointPolygonTest(self.roi, (float(x), float(y)), False) >= 0

    def _world(self, x, y):
        """Image foot point -> ground meters via homography."""
        p = np.array([[[x, y]]], dtype=np.float64)
        w = cv2.perspectiveTransform(p, self.H)[0, 0]
        return float(w[0]), float(w[1])

    def _speed(self, dq):
        """Speed (km/h or px/s) from the sliding-window foot history.
        dt is real elapsed seconds (window endpoints), so it is correct whether
        frames are consecutive (file) or skipped non-uniformly (RTSP)."""
        if len(dq) < 2:
            return 0.0
        t0, x0, y0 = dq[0]
        t1, x1, y1 = dq[-1]
        dt = t1 - t0
        px_dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        if dt <= 0 or px_dist < self.min_move_px:
            return 0.0
        if self.H is not None:                  # output units -> meters (×units_per_px)
            wx0, wy0 = self._world(x0, y0)
            wx1, wy1 = self._world(x1, y1)
            m = (((wx1 - wx0) ** 2 + (wy1 - wy0) ** 2) ** 0.5) * self.units_per_px
            return (m / dt) * 3.6                # m/s -> km/h
        if self.ppm:                            # global scale
            return ((px_dist / self.ppm) / dt) * 3.6
        return px_dist / dt                     # px/s

    def update(self, t, targets):
        """Feed one frame's targets at wall-clock time `t` (seconds).
        Returns {id: speed} for in-ROI objects.

        File mode passes t = frame_idx / fps (video timeline); RTSP passes a
        monotonic clock. Either way dt and dwell are computed from real seconds.
        """
        t = float(t)
        self.last_t = t
        present = {}
        seen = set()
        if targets is not None and getattr(targets, "ndim", 0) == 2:
            for tg in targets:
                if tg.shape[0] < 5:
                    continue
                fx, fy = self._foot(tg[0], tg[1], tg[2], tg[3])
                tid = int(tg[4])
                if not self._in_roi(fx, fy):
                    self._forget(tid)
                    continue
                seen.add(tid)
                self.first_seen.setdefault(tid, t)
                dq = self.history.setdefault(tid, deque())
                dq.append((t, fx, fy))
                while len(dq) > 1 and (t - dq[0][0]) > self.window_sec:
                    dq.popleft()                 # keep only the last window_sec

                spd = self._speed(dq)
                self.speed[tid] = spd
                present[tid] = spd
                self.seen_ids.add(tid)

                pf = self.prev.get(tid)          # acceleration
                if pf is not None:
                    pt, ps = pf
                    dt2 = t - pt
                    if dt2 > 0:
                        self.accel[tid] = abs(spd - ps) / dt2
                self.prev[tid] = (t, spd)

        for tid in list(self.history.keys()):
            if tid not in seen:
                self._forget(tid)
        return present

    def _forget(self, tid):
        for d in (self.history, self.speed, self.first_seen, self.prev, self.accel):
            d.pop(tid, None)

    def _obj_map(self, tid):
        """Top-down map position + unit direction for one object.
        Returns (mx, my, dirx, diry). Metric ground meters when homography is
        set, else raw image-foot pixels (perspective-distorted fallback)."""
        dq = self.history.get(tid)
        if not dq:
            return 0.0, 0.0, 0.0, 0.0
        _, x0, y0 = dq[0]
        _, x1, y1 = dq[-1]
        if self.H is not None:
            mx, my = self._world(x1, y1)
            sx, sy = self._world(x0, y0)
        else:
            mx, my, sx, sy = x1, y1, x0, y0
        dx, dy = mx - sx, my - sy
        L = (dx * dx + dy * dy) ** 0.5
        if L > 1e-6:
            dx, dy = dx / L, dy / L
        else:
            dx, dy = 0.0, 0.0
        return mx, my, dx, dy

    def _map_bounds(self):
        """[x0,y0,x1,y1] for the map view. ROI(+H) -> stable rect; whole-frame
        depth -> None (client auto-fits); no calibration -> image pixel space."""
        if self.map_size is not None:           # map mode: full uploaded-map extent
            return [0.0, 0.0, float(self.map_size[0]), float(self.map_size[1])]
        if self.H is not None and self.roi is not None:
            pts = self.roi.astype(np.float32).reshape(-1, 1, 2)
            w = cv2.perspectiveTransform(pts, self.H).reshape(-1, 2)
            return [float(w[:, 0].min()), float(w[:, 1].min()),
                    float(w[:, 0].max()), float(w[:, 1].max())]
        if self.H is not None:
            return None
        return [0.0, 0.0, float(self.fw), float(self.fh)]

    def _density(self, n):
        """(value, unit). m² when metric scale known, else per-megapixel."""
        if self.H is not None and self.world_area_m2:
            return round(n / self.world_area_m2, 3), "명/m²"
        area_px = (abs(float(cv2.contourArea(self.roi))) if self.roi is not None
                   else float(self.fw * self.fh))
        if area_px > 0 and self.ppm:
            return round(n / (area_px / (self.ppm ** 2)), 3), "명/m²"
        if area_px > 0:
            return round(n / (area_px / 1e6), 2), "명/Mpx"
        return 0.0, "-"

    def metrics(self, present):
        speeds = [float(s) for s in present.values()]
        n = len(present)
        moving = int(sum(1 for s in speeds if s > self.move_thresh))
        density, density_unit = self._density(n)

        if density_unit == "명/m²":
            if density < 0.4:
                level, level_kr = "Low", "여유"
            elif density < 1.0:
                level, level_kr = "Normal", "보통"
            else:
                level, level_kr = "High", "혼잡"
        else:
            level, level_kr = "—", "—"

        dwells = {tid: (self.last_t - self.first_seen.get(tid, self.last_t))
                  for tid in present}      # already in seconds
        dvals = list(dwells.values())
        accels = [self.accel.get(tid, 0.0) for tid in present]

        objects = [self._obj_entry(tid, s, dwells[tid])
                   for tid, s in sorted(present.items(), key=lambda kv: -kv[1])]
        aligns = [o["align"] for o in objects
                  if o["align"] is not None and o["speed"] > self.move_thresh]
        avg_align = round(sum(aligns) / len(aligns), 3) if aligns else None

        return {
            "unit": self.unit,
            "count": int(n),
            "cumulative": int(len(self.seen_ids)),
            "avg": round(sum(speeds) / n, 1) if speeds else 0.0,
            "max": round(max(speeds), 1) if speeds else 0.0,
            "accel": round(sum(accels) / len(accels), 2) if accels else 0.0,
            "moving": moving,
            "stationary": n - moving,
            "moving_ratio": round(100.0 * moving / n, 0) if n else 0.0,
            "density": density,
            "density_unit": density_unit,
            "level": level,
            "level_kr": level_kr,
            "avg_dwell": round(sum(dvals) / len(dvals), 1) if dvals else 0.0,
            "max_dwell": round(max(dvals), 1) if dvals else 0.0,
            "map_metric": self.H is not None,     # true top-down vs image-space
            "map_image": self.map_size is not None,  # map view = uploaded image bg
            "map_bounds": self._map_bounds(),
            "has_align": self.has_align,          # alignment opt-in (arrow drawn)
            "avg_align": avg_align,               # zone mean cosine (moving only)
            "ref_dir": list(self.ref_dir) if self.ref_dir else None,
            "objects": objects,
        }

    def _obj_entry(self, tid, s, dwell):
        mx, my, dx, dy = self._obj_map(tid)
        align = None                            # cosine vs recommended dir
        if self.ref_dir is not None and (dx or dy):
            align = round(dx * self.ref_dir[0] + dy * self.ref_dir[1], 3)
        return {"id": int(tid), "speed": round(float(s), 1),
                "dwell": round(float(dwell), 1),
                "mx": round(mx, 2), "my": round(my, 2),
                "dirx": round(dx, 2), "diry": round(dy, 2), "align": align}


def annotate(frame, targets, present, estimator):
    """Draw ROI + per-object box, ID, speed label (foot-based) — clean style."""
    vis = frame.copy()
    h = vis.shape[0]
    fs = du.font_scale(h)
    tt = du.txt_thickness(fs)
    unit = estimator.unit
    if estimator.roi is not None:
        cv2.polylines(vis, [estimator.roi], isClosed=True, color=(255, 0, 0),
                      thickness=du.box_thickness(h), lineType=cv2.LINE_AA)
    # recommended evacuation direction (only when the user drew one)
    if estimator.has_align and estimator.reference_vec is not None:
        (tx, ty), (hx, hy) = estimator.reference_vec
        cv2.arrowedLine(vis, (int(tx), int(ty)), (int(hx), int(hy)),
                        (255, 80, 0), max(3, du.box_thickness(h)),
                        cv2.LINE_AA, 0, 0.25)
        cv2.putText(vis, "EVAC DIR", (int(hx) + 6, int(hy)), du.FONT,
                    fs * 0.85, (255, 80, 0), tt, cv2.LINE_AA)
    if targets is not None and getattr(targets, "ndim", 0) == 2:
        for t in targets:
            if t.shape[0] < 5:
                continue
            tid = int(t[4])
            if tid not in present:
                continue
            x1, y1, x2, y2 = int(t[0]), int(t[1]), int(t[2]), int(t[3])
            du.draw_id_box(vis, x1, y1, x2, y2, tid, fs=fs)
            cv2.putText(vis, f"{present[tid]:.1f} {unit}", (x1 + 2, y2 + int(fs * 26)),
                        du.FONT, fs * 0.85, (0, 255, 255), tt, cv2.LINE_AA)
            if estimator.has_align:            # alignment marker (green/amber/red)
                _, _, dx, dy = estimator._obj_map(tid)
                a = (dx * estimator.ref_dir[0] + dy * estimator.ref_dir[1]
                     if (dx or dy) else None)
                cv2.circle(vis, (x2 - 6, y1 + 6), 5, du.align_color(a), -1, cv2.LINE_AA)
    return vis
