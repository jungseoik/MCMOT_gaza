"""글로벌ID 비교 하네스 — 2단계: capture.pkl 을 우리 resolve() 와 zip assign()
양쪽에 동일하게 흘려 프레임별 글로벌ID를 산출하고, 카메라 그리드 + 2D 플로어맵으로
렌더한다. 검출·임베딩이 동일하므로 **글로벌ID 로직 차이만** 드러난다.

  python tools/gid_compare/render.py --scenario scenario_01

출력(results/gid_compare/<scenario>/):
  ours.mp4         5-cam 그리드 + 플로어맵, 우리 글로벌ID(gN)
  zip.mp4          동일 레이아웃, zip 글로벌ID(G-n)
  compare_map.mp4  두 방식 플로어맵 좌/우 나란히 (같은 사람=같은 색/ID 여부가 한눈에)
  summary.json     방식별 정체성 수·다중카메라 정체성 수
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import subprocess
import sys
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ZIP_SRC = ROOT / "multi_camera_tracking_zip"   # zip fusion 코드 위치(아래 --zip-src 로 지정 가능)

FONT = cv2.FONT_HERSHEY_SIMPLEX
GREY = (160, 160, 160)
_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)


def color_for(label: str | None) -> tuple[int, int, int]:
    """식별자 문자열 → 안정적 BGR. 미할당(None)=회색. zip 방식과 동일한 hue*37."""
    if not label:
        return GREY
    n = int("".join(ch for ch in label if ch.isdigit()) or 0)
    hue = (n * 37) % 180
    b, g, r = cv2.cvtColor(np.uint8([[[hue, 220, 240]]]), cv2.COLOR_HSV2BGR)[0, 0]
    return int(b), int(g), int(r)


# ───────────────────────── 우리 방식 (system/identity) ─────────────────────
def run_ours(cap: dict, min_conf: float) -> tuple[dict, dict]:
    from system.identity.global_id import GlobalIdService, DEFAULTS
    g = dict(DEFAULTS)
    try:
        g.update(json.loads((ROOT / "data/global_id.json").read_text()))
    except Exception:
        pass
    svc = GlobalIdService(ttl_sec=float(g["ttl_sec"]), cos_th=float(g["cos_th"]),
                          update_every=int(g["update_every"]),
                          min_new_obs=int(g["min_new_obs"]),
                          max_speed_mps=float(g["max_speed_mps"]))
    svc.reset()
    mpp = cap["m_per_px"]
    labels: dict = {}
    for snap in cap["observations"]:
        ts = snap["t"]
        for cam in cap["order"]:
            for t in snap["cams"].get(cam, {}).get("tracks", []):
                lid = t["local_id"]
                okey = f"{cam}:{lid}"
                if t["conf"] < min_conf:              # 엔진 min_conf 게이트(오탐 연명 차단)
                    labels[(snap["step"], cam, lid)] = None
                    continue
                mx, my = t["map_xy"]
                pos_m = (mx * mpp, my * mpp) if np.isfinite(mx) else None
                gid = svc.resolve(cam, lid, t["emb"], ts, pos_m)
                labels[(snap["step"], cam, lid)] = gid   # gN or None(보류/무효)
    # 요약
    ids = set(v for v in labels.values() if v)
    cams_of: dict[str, set] = defaultdict(set)
    for (_, cam, _), v in labels.items():
        if v:
            cams_of[v].add(cam)
    summ = {"method": "ours (system/identity)", "settings": g,
            "n_identities": len(ids),
            "n_multi_camera": sum(len(c) > 1 for c in cams_of.values()),
            "id_cameras": {k: sorted(v) for k, v in sorted(cams_of.items())}}
    return labels, summ


# ───────────────────────── zip 방식 (fusion, worker 재현) ───────────────────
def run_zip(cap: dict, zip_src: Path, checkpoint_frames: int,
            min_frames: int) -> tuple[dict, dict]:
    if str(zip_src) not in sys.path:
        sys.path.insert(0, str(zip_src))
    from pia_tracking.fusion.global_id import GlobalIDService       # type: ignore
    from pia_tracking.fusion.tracklet import Tracklet, TrackAccumulator  # type: ignore

    y = json.loads((zip_src.parent / "_zip_gid_cfg.json").read_text()) if \
        (zip_src.parent / "_zip_gid_cfg.json").exists() else {}
    svc = GlobalIDService(
        similarity_threshold=y.get("similarity_threshold", 0.45),
        reidentify_within_sec=y.get("reidentify_within_sec", 600.0),
        revise_at_loss=y.get("revise_at_loss", True),
        revise_margin=y.get("revise_margin", 0.05),
        percam_norm=y.get("percam_norm", True),
        percam_prior_weight=y.get("percam_prior_weight", 200.0),
    )
    # 카메라별 worker 상태 (worker.py 재현): accumulator + local→gid
    acc: dict[tuple[str, int], TrackAccumulator] = {}
    gids: dict[tuple[str, int], int] = {}
    active: dict[str, set] = defaultdict(set)

    def dt(ts: float) -> datetime:
        return _EPOCH + timedelta(seconds=float(ts))

    def checkpoint(cam: str, lid: int, ts: float):
        a = acc[(cam, lid)]
        if a.frame_count == 0 or a.frame_count % checkpoint_frames:
            return
        tl = a.finalize(cam)
        seg = a.split_segment()
        try:
            gid = svc.assign(tl)
            gids[(cam, lid)] = gid
        except Exception:
            a.merge_segment(*seg)

    def finalize(cam: str, lid: int):
        a = acc.pop((cam, lid), None)
        if a is None or a.frame_count == 0:
            return
        tl = a.finalize(cam)
        if (cam, lid) not in gids and tl.frame_count < min_frames:
            return
        try:
            gids[(cam, lid)] = svc.assign(tl)
        except Exception:
            pass

    labels: dict = {}
    for snap in cap["observations"]:
        ts = snap["t"]
        fidx = snap["step"]
        for cam in cap["order"]:
            tracks = snap["cams"].get(cam, {}).get("tracks", [])
            cur = {t["local_id"] for t in tracks if t["emb"] is not None}
            # open accumulators + 동일카메라 동시등장 기록(두 박스=두 사람)
            for lid in cur:
                acc.setdefault((cam, lid), TrackAccumulator(lid))
            if len(cur) > 1:
                for lid in cur:
                    acc[(cam, lid)].note_concurrent(set(cur))
            # accumulate
            for t in tracks:
                if t["emb"] is None:
                    continue
                acc[(cam, t["local_id"])].add(t["emb"], dt(ts), fidx)
            # checkpoint (미할당 트랙)
            for lid in cur:
                if (cam, lid) not in gids:
                    checkpoint(cam, lid, ts)
            # loss → finalize
            for lid in list(active[cam] - cur):
                finalize(cam, lid)
            active[cam] = cur
            # 이 스텝 라벨(현재 알려진 gid)
            for t in tracks:
                gid = gids.get((cam, t["local_id"]))
                labels[(fidx, cam, t["local_id"])] = (f"G-{gid}" if gid is not None else None)
    # 끝: 남은 트랙 flush (라벨엔 소급 안 함 — 라이브 뷰 유지)
    for cam in cap["order"]:
        for lid in list(active[cam]):
            finalize(cam, lid)

    ids = set(v for v in labels.values() if v)
    cams_of: dict[str, set] = defaultdict(set)
    for (_, cam, _), v in labels.items():
        if v:
            cams_of[v].add(cam)
    summ = {"method": "zip (fusion.global_id)",
            "settings": {"similarity_threshold": svc.similarity_threshold,
                         "percam_norm": svc.percam_norm is not None,
                         "checkpoint_frames": checkpoint_frames,
                         "min_frames_before_id_assign": min_frames},
            "n_identities": len(ids),
            "n_multi_camera": sum(len(c) > 1 for c in cams_of.values()),
            "id_cameras": {k: sorted(v) for k, v in sorted(cams_of.items())},
            **{k: getattr(svc.stats, k) for k in vars(svc.stats)}}
    return labels, summ


# ───────────────────────── 렌더 ─────────────────────────
def _even(n): n = int(round(n)); return n if n % 2 == 0 else n + 1


def _grid_shape(n, cols=None):
    if cols is None:
        cols = math.ceil(math.sqrt(n))
    return math.ceil(n / cols), cols


def draw_boxes(frame, tracks, labels, step, cam, cw, ch):
    out = cv2.resize(frame, (cw, ch))
    sx, sy = cw / frame.shape[1], ch / frame.shape[0]
    for t in tracks:
        lab = labels.get((step, cam, t["local_id"]))
        col = color_for(lab)
        x1, y1, x2, y2 = t["bbox"]
        p1 = (int(x1 * sx), int(y1 * sy)); p2 = (int(x2 * sx), int(y2 * sy))
        cv2.rectangle(out, p1, p2, col, 2)
        txt = lab if lab else f"·{t['local_id']}"
        (tw, th), _ = cv2.getTextSize(txt, FONT, 0.45, 1)
        ty = max(th + 2, p1[1])
        cv2.rectangle(out, (p1[0], ty - th - 3), (p1[0] + tw + 3, ty), col, -1)
        cv2.putText(out, txt, (p1[0] + 1, ty - 2), FONT, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.rectangle(out, (0, 0), (60, 18), (40, 40, 40), -1)
    cv2.putText(out, cam, (4, 13), FONT, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    return out


def make_grid(snap, labels, order, cw, ch, videos):
    rows, cols = _grid_shape(len(order))
    cells = []
    for cam in order:
        cd = snap["cams"].get(cam, {})
        fr = videos[cam](cd.get("frame_idx", 0))
        cells.append(draw_boxes(fr, cd.get("tracks", []), labels, snap["step"], cam, cw, ch))
    blank = np.zeros((ch, cw, 3), np.uint8)
    while len(cells) < rows * cols:
        cells.append(blank)
    return np.vstack([np.hstack(cells[r * cols:(r + 1) * cols]) for r in range(rows)])


def make_map(snap, labels, order, base_map, mw, mh, trails, title):
    out = cv2.resize(base_map, (mw, mh))
    sx, sy = mw / 2000.0, mh / 1887.0
    for cam in order:
        for t in snap["cams"].get(cam, {}).get("tracks", []):
            lab = labels.get((snap["step"], cam, t["local_id"]))
            mx, my = t["map_xy"]
            if not np.isfinite(mx):
                continue
            p = (int(mx * sx), int(my * sy))
            col = color_for(lab)
            if lab:
                trails[lab].append(p)
                pts = list(trails[lab])
                for i in range(1, len(pts)):
                    cv2.line(out, pts[i - 1], pts[i], col, 2, cv2.LINE_AA)
            cv2.circle(out, p, 6, col, -1)
            cv2.circle(out, p, 6, (30, 30, 30), 1)
            if lab:
                cv2.putText(out, lab, (p[0] + 7, p[1] - 6), FONT, 0.5, col, 2, cv2.LINE_AA)
    cv2.rectangle(out, (0, 0), (len(title) * 11 + 12, 24), (30, 30, 30), -1)
    cv2.putText(out, title, (6, 17), FONT, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def _video_getter(path):
    cap = cv2.VideoCapture(path)
    cache = {}

    def get(idx):
        if idx in cache:
            return cache[idx]
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, fr = cap.read()
        if not ok:
            fr = np.zeros((1080, 1920, 3), np.uint8)
        cache.clear()
        cache[idx] = fr
        return fr
    return get


def _h264(path):
    tmp = str(path) + ".t.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
                        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", "-an", tmp], capture_output=True)
    if r.returncode == 0:
        Path(tmp).replace(path)
    elif Path(tmp).exists():
        Path(tmp).unlink()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="scenario_01")
    ap.add_argument("--capture", default=None)
    ap.add_argument("--zip-src", default=str(ZIP_SRC))
    ap.add_argument("--min-conf", type=float, default=0.5)
    ap.add_argument("--checkpoint-frames", type=int, default=7,
                    help="zip 체크포인트 — 30fps 기준 40프레임(~1.3s)을 분석 5fps로 환산≈7")
    ap.add_argument("--min-frames", type=int, default=2,
                    help="zip 최소 프레임 — 30fps 10프레임(~0.33s)을 5fps로 환산≈2")
    ap.add_argument("--cell-w", type=int, default=384)
    ap.add_argument("--map-h", type=int, default=560)
    args = ap.parse_args()

    out_dir = ROOT / "results/gid_compare" / args.scenario
    cap = pickle.load(open(args.capture or out_dir / "capture.pkl", "rb"))
    order = cap["order"]
    print(f"[render] {args.scenario}: {len(cap['observations'])} steps, cams={order}")

    ours_labels, ours_sum = run_ours(cap, args.min_conf)
    zip_labels, zip_sum = run_zip(cap, Path(args.zip_src), args.checkpoint_frames, args.min_frames)
    print(f"[render] ours: {ours_sum['n_identities']} ids "
          f"({ours_sum['n_multi_camera']} multi-cam) | "
          f"zip: {zip_sum['n_identities']} ids ({zip_sum['n_multi_camera']} multi-cam)")

    cw = args.cell_w
    ch = _even(cw * 1080 / 1920)
    base_map = cv2.imread(cap["map_png"])
    mh = args.map_h
    mw = _even(mh * 2000 / 1887)
    videos = {cam: _video_getter(cap["cam_meta"][cam]["video"]) for cam in order}

    rows, cols = _grid_shape(len(order))
    grid_w, grid_h = cw * cols, ch * rows
    # 방식별 프레임 = [그리드 | 맵], 맵 높이를 그리드에 맞춤
    panel_mh = grid_h
    panel_mw = _even(panel_mh * 2000 / 1887)

    fps = float(cap["fps"])
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out_dir.mkdir(parents=True, exist_ok=True)

    def render_method(name, labels, title):
        path = out_dir / f"{name}.mp4"
        w = grid_w + panel_mw
        vw = cv2.VideoWriter(str(path), fourcc, fps, (w, grid_h))
        trails = defaultdict(lambda: deque(maxlen=25))
        for snap in cap["observations"]:
            grid = make_grid(snap, labels, order, cw, ch, videos)
            mp = make_map(snap, labels, order, base_map, panel_mw, panel_mh, trails, title)
            frame = np.hstack([grid, mp])
            cv2.line(frame, (grid_w, 0), (grid_w, grid_h), (80, 80, 80), 2)
            vw.write(frame)
        vw.release()
        _h264(path)
        print(f"[render] -> {path}")

    render_method("ours", ours_labels, "OURS (system/identity)")
    render_method("zip", zip_labels, "ZIP (fusion.global_id)")

    # compare_map — 두 방식 맵 좌/우
    path = out_dir / "compare_map.mp4"
    vw = cv2.VideoWriter(str(path), fourcc, fps, (mw * 2, mh))
    tr_o = defaultdict(lambda: deque(maxlen=25))
    tr_z = defaultdict(lambda: deque(maxlen=25))
    for snap in cap["observations"]:
        mo = make_map(snap, ours_labels, order, base_map, mw, mh, tr_o, "OURS")
        mz = make_map(snap, zip_labels, order, base_map, mw, mh, tr_z, "ZIP")
        vw.write(np.hstack([mo, mz]))
    vw.release()
    _h264(path)
    print(f"[render] -> {path}")

    (out_dir / "summary.json").write_text(
        json.dumps({"ours": ours_sum, "zip": zip_sum}, ensure_ascii=False, indent=2))
    print(f"[render] -> {out_dir/'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
