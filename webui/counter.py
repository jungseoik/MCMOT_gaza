"""In/Out line-crossing counter for occupancy estimation.

Draw a line (2 points) at a doorway; the line splits the plane into two sides.
Each tracked person's FOOT point is on one side; when their stable side flips,
that's a crossing. The side the user marked as "inside" decides in vs out.

occupancy = start + in - out  (negative => miscount / unobserved entry).

Counting is done purely in image pixel space — no calibration needed.
"""
import numpy as np
import cv2

from src.inference import _get_color
from webui import draw_utils as du


class LineCounter:
    def __init__(self, line, inside_point, segment_only=True,
                 margin_px=6.0, seg_pad=0.06, start_occupancy=0):
        self.A = np.array(line[0], dtype=np.float64)
        self.B = np.array(line[1], dtype=np.float64)
        self.AB = self.B - self.A
        self.L = float(np.hypot(self.AB[0], self.AB[1])) or 1.0
        self.segment_only = bool(segment_only)
        self.margin = float(margin_px)        # deadband near line (anti-jitter)
        self.seg_pad = float(seg_pad)
        self.start = int(start_occupancy)
        self.inside_sign = self._side(np.array(inside_point, dtype=np.float64))
        self.in_count = 0
        self.out_count = 0
        self.side = {}                         # id -> last stable side (+1/-1)
        self._present = set()

    def _signed_dist(self, P):
        cross = self.AB[0] * (P[1] - self.A[1]) - self.AB[1] * (P[0] - self.A[0])
        return cross / self.L                  # +/- distance (px) from the line

    def _side(self, P):
        return 1 if self._signed_dist(P) >= 0 else -1

    def _near_segment(self, P):
        t = float(np.dot(P - self.A, self.AB) / (self.L ** 2))
        return -self.seg_pad <= t <= 1 + self.seg_pad

    @staticmethod
    def _foot(t):
        return np.array([(t[0] + t[2]) / 2.0, float(t[3])])

    def update(self, targets):
        seen = set()
        if targets is not None and getattr(targets, "ndim", 0) == 2:
            for tg in targets:
                if tg.shape[0] < 5:
                    continue
                tid = int(tg[4])
                P = self._foot(tg)
                seen.add(tid)
                d = self._signed_dist(P)
                if abs(d) < self.margin:        # too close to line -> ignore (jitter)
                    continue
                cur = 1 if d > 0 else -1
                prev = self.side.get(tid)
                if prev is None:
                    self.side[tid] = cur        # first stable side, no count yet
                    continue
                if cur != prev:                 # crossed the line
                    if (not self.segment_only) or self._near_segment(P):
                        if cur == self.inside_sign:
                            self.in_count += 1
                        else:
                            self.out_count += 1
                    self.side[tid] = cur
        for tid in list(self.side):             # drop gone ids
            if tid not in seen:
                self.side.pop(tid, None)
        self._present = seen

    def metrics(self):
        occ = self.start + self.in_count - self.out_count
        return {
            "kind": "count",
            "in": int(self.in_count),
            "out": int(self.out_count),
            "occupancy": int(occ),
            "alert": bool(occ < 0),             # negative => something went wrong
            "present": int(len(self._present)),
        }

    # ---- drawing ----
    def _frame_line(self, w, h):
        """Endpoints of the (infinite) line clipped to the frame border."""
        # param: A + s*AB; find s where it hits each border, keep inside ones.
        pts = []
        dx, dy = self.AB
        for (val, isx) in [(0, True), (w, True), (0, False), (h, False)]:
            if isx and abs(dx) > 1e-9:
                s = (val - self.A[0]) / dx
                y = self.A[1] + s * dy
                if -1 <= y <= h + 1:
                    pts.append((val, y))
            if (not isx) and abs(dy) > 1e-9:
                s = (val - self.A[1]) / dy
                x = self.A[0] + s * dx
                if -1 <= x <= w + 1:
                    pts.append((x, val))
        return pts[:2] if len(pts) >= 2 else [tuple(self.A), tuple(self.B)]

    def draw(self, frame, targets):
        vis = frame.copy()
        h, w = vis.shape[:2]
        # line (extended if infinite mode, else just the segment)
        if self.segment_only:
            p1, p2 = tuple(self.A.astype(int)), tuple(self.B.astype(int))
        else:
            fl = self._frame_line(w, h)
            p1 = tuple(map(int, fl[0])); p2 = tuple(map(int, fl[1]))
        fs = du.font_scale(h)
        tt = du.txt_thickness(fs)
        lt = du.box_thickness(h)
        cv2.line(vis, p1, p2, (255, 200, 0), lt, cv2.LINE_AA)   # line
        cv2.circle(vis, tuple(self.A.astype(int)), lt + 2, (255, 200, 0), -1, cv2.LINE_AA)
        cv2.circle(vis, tuple(self.B.astype(int)), lt + 2, (255, 200, 0), -1, cv2.LINE_AA)
        # "inside" arrow from midpoint toward the inside half
        M = (self.A + self.B) / 2.0
        n = np.array([-self.AB[1], self.AB[0]]) / self.L
        ins = M + self.inside_sign * n * 46
        cv2.arrowedLine(vis, tuple(M.astype(int)), tuple(ins.astype(int)),
                        (60, 220, 60), lt, tipLength=0.3, line_type=cv2.LINE_AA)
        cv2.putText(vis, "IN", tuple((ins + self.inside_sign * n * 14 - [10, 0]).astype(int)),
                    du.FONT, fs, (60, 220, 60), tt, cv2.LINE_AA)
        # tracked people: clean ID box + foot dot
        if targets is not None and getattr(targets, "ndim", 0) == 2:
            for tg in targets:
                if tg.shape[0] < 5:
                    continue
                tid = int(tg[4]); P = self._foot(tg).astype(int)
                c = du.draw_id_box(vis, int(tg[0]), int(tg[1]), int(tg[2]), int(tg[3]),
                                   tid, fs=fs)
                cv2.circle(vis, tuple(P), lt + 1, c, -1, cv2.LINE_AA)
        # counters (bottom-left readout)
        occ = self.start + self.in_count - self.out_count
        bar = f"IN {self.in_count}   OUT {self.out_count}   in space {occ}"
        (bw, bh), bl = cv2.getTextSize(bar, du.FONT, fs, tt)
        cv2.rectangle(vis, (8, h - bh - bl - 16), (16 + bw, h - 8), (0, 0, 0), -1, cv2.LINE_AA)
        col = (0, 0, 255) if occ < 0 else (0, 255, 255)
        cv2.putText(vis, bar, (12, h - bl - 12), du.FONT, fs, col, tt, cv2.LINE_AA)
        return vis

    def draw_line(self, vis):
        """Overlay ONLY the line + inside arrow + counter bar onto an already-drawn
        frame (used as an add-on in speed/map mode; per-object boxes are drawn by
        the speed annotate, so we don't redraw them here). Mutates `vis` in place."""
        h, w = vis.shape[:2]
        if self.segment_only:
            p1, p2 = tuple(self.A.astype(int)), tuple(self.B.astype(int))
        else:
            fl = self._frame_line(w, h)
            p1 = tuple(map(int, fl[0])); p2 = tuple(map(int, fl[1]))
        fs = du.font_scale(h); tt = du.txt_thickness(fs); lt = du.box_thickness(h)
        cv2.line(vis, p1, p2, (255, 200, 0), lt, cv2.LINE_AA)
        cv2.circle(vis, tuple(self.A.astype(int)), lt + 2, (255, 200, 0), -1, cv2.LINE_AA)
        cv2.circle(vis, tuple(self.B.astype(int)), lt + 2, (255, 200, 0), -1, cv2.LINE_AA)
        M = (self.A + self.B) / 2.0
        n = np.array([-self.AB[1], self.AB[0]]) / self.L
        ins = M + self.inside_sign * n * 46
        cv2.arrowedLine(vis, tuple(M.astype(int)), tuple(ins.astype(int)),
                        (60, 220, 60), lt, tipLength=0.3, line_type=cv2.LINE_AA)
        cv2.putText(vis, "IN", tuple((ins + self.inside_sign * n * 14 - [10, 0]).astype(int)),
                    du.FONT, fs, (60, 220, 60), tt, cv2.LINE_AA)
        occ = self.start + self.in_count - self.out_count
        bar = f"IN {self.in_count}  OUT {self.out_count}  in {occ}"
        (bw, bh), bl = cv2.getTextSize(bar, du.FONT, fs, tt)
        cv2.rectangle(vis, (8, h - bh - bl - 16), (16 + bw, h - 8), (0, 0, 0), -1, cv2.LINE_AA)
        col = (0, 0, 255) if occ < 0 else (0, 255, 255)
        cv2.putText(vis, bar, (12, h - bl - 12), du.FONT, fs, col, tt, cv2.LINE_AA)
        return vis
