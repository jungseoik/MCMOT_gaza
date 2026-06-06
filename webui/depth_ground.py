"""Turn a metric depth map into an image->ground homography (meters).

Pipeline (runs in the boosttrack env; consumes the depth .npy produced by
da3_depth.py and person boxes from the detector):

  1. focal length  f   ← from people: f = h_px * Z / human_height  (median)
  2. ground plane (n,d) ← RANSAC over back-projected lower-image floor pixels
  3. homography H      ← image pixel --ray→ plane → 2D ground meters (4 pts)

The result H feeds SpeedEstimator(homography=H) exactly like the manual 4-point
mode, so the whole speed/metric pipeline is reused. Scale is anchored by the
~1.7 m human-height prior, so it is an *estimate* (label as such in the UI).
"""
import numpy as np
import cv2

HUMAN_H = 1.70  # meters, standing-adult prior


def estimate_focal(depth, boxes, human_height=HUMAN_H):
    """f = h_px * Z / H, median over people. Z sampled at the foot region."""
    H, W = depth.shape
    fs = []
    for (x1, y1, x2, y2) in boxes:
        h_px = float(y2 - y1)
        if h_px < 15:
            continue
        cx = int((x1 + x2) / 2)
        fa, fb = max(0, cx - 6), min(W, cx + 6)
        ya, yb = max(0, int(y2 - 0.12 * h_px)), min(H, int(y2) + 1)
        patch = depth[ya:yb, fa:fb]
        if patch.size == 0:
            continue
        Z = float(np.median(patch))
        if Z > 0:
            fs.append(h_px * Z / human_height)
    if not fs:
        return None
    return float(np.median(fs))


def fit_plane(depth, focal, person_boxes=None, sample=5000, thr=0.10, iters=300):
    """RANSAC plane n·P=d over floor candidate pixels (lower image, no people)."""
    H, W = depth.shape
    cx, cy = W / 2.0, H / 2.0
    mask = np.zeros((H, W), bool)
    mask[int(H * 0.45):, :] = True             # floor likely in lower image
    if person_boxes:
        for (x1, y1, x2, y2) in person_boxes:
            mask[max(0, int(y1)):int(y2), max(0, int(x1)):int(x2)] = False
    ys, xs = np.where(mask & (depth > 0))
    if len(xs) < 50:
        return None
    if len(xs) > sample:
        idx = np.random.default_rng(0).choice(len(xs), sample, replace=False)
        xs, ys = xs[idx], ys[idx]
    Z = depth[ys, xs].astype(np.float64)
    P = np.stack([(xs - cx) * Z / focal, (ys - cy) * Z / focal, Z], 1)

    rng = np.random.default_rng(0)
    best_n, best_d, best_cnt = None, None, -1
    for _ in range(iters):
        s = P[rng.choice(len(P), 3, replace=False)]
        n = np.cross(s[1] - s[0], s[2] - s[0])
        nn = np.linalg.norm(n)
        if nn < 1e-9:
            continue
        n = n / nn
        d = n @ s[0]
        cnt = int((np.abs(P @ n - d) < thr).sum())
        if cnt > best_cnt:
            best_cnt, best_n, best_d = cnt, n, d
    if best_n is None:
        return None
    inl = np.abs(P @ best_n - best_d) < thr
    Pi = P[inl]
    c = Pi.mean(0)
    _, _, vt = np.linalg.svd(Pi - c)
    n = vt[-1]
    d = n @ c
    return n.astype(float), float(d), (cx, cy), float(inl.mean())


def build_homography(plane, focal, image_size):
    """image pixel -> 2D ground meters (perspective). image_size=(W,H)."""
    n, d, (cx, cy), _inl = plane
    W, H = image_size
    a = np.array([1.0, 0, 0])
    if abs(float(n @ a)) > 0.9:
        a = np.array([0, 1.0, 0])
    uax = np.cross(n, a); uax /= np.linalg.norm(uax)
    vax = np.cross(n, uax)
    p0 = n * d

    def to_ground(u, v):
        ray = np.array([(u - cx) / focal, (v - cy) / focal, 1.0])
        denom = n @ ray
        if abs(denom) < 1e-9:
            return None
        P = (d / denom) * ray
        return [float((P - p0) @ uax), float((P - p0) @ vax)]

    img_pts = np.float32([[W * 0.2, H * 0.55], [W * 0.8, H * 0.55],
                          [W * 0.8, H * 0.95], [W * 0.2, H * 0.95]])
    gnd = []
    for p in img_pts:
        g = to_ground(p[0], p[1])
        if g is None:
            return None
        gnd.append(g)
    Hmat = cv2.getPerspectiveTransform(img_pts, np.float32(gnd))
    return Hmat, to_ground


def polygon_area_m2(homography, roi):
    """ROI image polygon -> ground area in m² (for density)."""
    pts = np.array(roi, dtype=np.float64).reshape(-1, 1, 2)
    g = cv2.perspectiveTransform(pts.astype(np.float32), homography).reshape(-1, 2)
    x, y = g[:, 0], g[:, 1]
    return float(abs(0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)))


def estimate(depth, boxes, image_size):
    """Full pipeline. Returns dict or None on failure."""
    f = estimate_focal(depth, boxes)
    if not f or f <= 0:
        return None
    plane = fit_plane(depth, f, person_boxes=boxes)
    if plane is None:
        return None
    res = build_homography(plane, f, image_size)
    if res is None:
        return None
    Hmat, _ = res
    return {"focal": f, "plane_inlier": plane[3],
            "homography": Hmat.tolist()}
