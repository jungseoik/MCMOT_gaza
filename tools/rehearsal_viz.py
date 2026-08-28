#!/usr/bin/env python3
"""리허설 시나리오 시각화 — 라이브와 같은 조건(5fps 분석)으로 카메라 그리드 + 공유 2D 맵.

    python tools/rehearsal_viz.py --package cj-rehearsal --scenario scenario_02
    python tools/rehearsal_viz.py --package cj-rehearsal --scenario scenario_02 --fps 5 --out results/rehearsal_viz

무엇을 보여주나 (라이브에서 "끊긴다"가 추론 문제인지 매핑 문제인지 가르기 위해):
  좌  카메라 그리드 — ID 박스(ID별 색) · 발끝점 · **대응점 헐/유효 ROI**(청록) ·
      발끝이 헐 안이면 ● 녹색 / 밖이면 ✕ 빨강(= 라이브에서 버려지는 관측) · 화면 통과선(주황)
  우  공유 2D 맵 — 사이트 층 도면 + 구역·병목·출구 + 전 카메라 투영점(카메라별 색, 2초 궤적)
      헐 밖 관측은 회색 ✕ 로 "있었다면 여기" 위치에 표시
  좌하 정보 셀 — t · 카메라별 관측 수 · 헐 안/밖 누계 · 살아있는 ID 수

패키지 매니페스트(rehearsal.json)의 매핑(H·cctv_pts·valid_roi)과 사이트 층(data/sites/default)
의 도면·공간요소를 그대로 쓴다 — 라이브와 같은 좌표계. 분석은 원본 30fps 를 `--fps` 로
서브샘플(기본 5 = analyze_fps) 하고 카메라마다 독립 BoostTrack 을 둔다(라이브와 동형).
검출기·ReID 는 tools/concat_viz.py 와 같은 BoostTrackGPUInference(TRT).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from system.vsource import package as vpkg          # noqa: E402

FONT = cv2.FONT_HERSHEY_SIMPLEX
CAM_COLORS = [(80, 200, 255), (255, 120, 80), (120, 255, 120), (255, 80, 220),
              (80, 255, 255), (255, 200, 80), (200, 120, 255), (120, 200, 200)]


def _color_id(i: int) -> tuple:
    rng = np.random.default_rng(int(i) * 7919 + 17)
    c = rng.integers(70, 255, size=3)
    return int(c[0]), int(c[1]), int(c[2])


def _hull(pts: list) -> np.ndarray:
    return cv2.convexHull(np.array(pts, np.float32)).reshape(-1, 2)


def _inside(poly: np.ndarray, u: float, v: float) -> bool:
    return cv2.pointPolygonTest(poly.astype(np.float32), (float(u), float(v)), False) >= 0


def _load_site_floor(site_dir: Path, floor_id: str):
    site = json.loads((site_dir / "site.json").read_text(encoding="utf-8"))
    fl = next((f for f in site.get("floors", []) if f.get("id") == floor_id), None)
    if fl is None:
        raise SystemExit(f"사이트에 층 없음: {floor_id}")
    img = cv2.imread(str(site_dir / fl["map"]["image"])) if fl.get("map") else None
    if img is None:
        raise SystemExit(f"층 도면 없음: {floor_id}")
    return fl, img


def _draw_floor_elements(canvas: np.ndarray, fl: dict, s: float) -> None:
    def P(p):
        return int(round(p[0] * s)), int(round(p[1] * s))
    for z in fl.get("zones", []):
        pts = np.array([P(p) for p in z["polygon"]], np.int32)
        cv2.polylines(canvas, [pts], True, (60, 200, 60), 2, cv2.LINE_AA)
        cv2.putText(canvas, z["id"], P(z["polygon"][0]), FONT, 0.5, (60, 200, 60), 1, cv2.LINE_AA)
    for b in fl.get("bottlenecks", []):
        pts = np.array([P(p) for p in b["polygon"]], np.int32)
        cv2.polylines(canvas, [pts], True, (60, 140, 255), 2, cv2.LINE_AA)
        cv2.putText(canvas, b["id"], P(b["polygon"][0]), FONT, 0.5, (60, 140, 255), 1, cv2.LINE_AA)
    for e in fl.get("exits", []):
        a, b = e["line"]
        cv2.line(canvas, P(a), P(b), (255, 80, 80), 3, cv2.LINE_AA)
        cv2.putText(canvas, e["id"], P(a), FONT, 0.5, (255, 80, 80), 1, cv2.LINE_AA)
    for r in fl.get("routes", []):
        pts = np.array([P(p) for p in r["points"]], np.int32)
        cv2.polylines(canvas, [pts], False, (200, 200, 90), 1, cv2.LINE_AA)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--package", required=True)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--fps", type=float, default=5.0, help="분석 fps (라이브 analyze_fps 와 동일하게)")
    ap.add_argument("--out", default=str(ROOT / "results" / "rehearsal_viz"))
    ap.add_argument("--site-dir", default=str(ROOT / "data" / "sites" / "default"))
    ap.add_argument("--cell", type=int, default=480, help="카메라 셀 폭 px")
    ap.add_argument("--profile", default="auto",
                    help="추론 프로파일(model_zoo.py). 기본 auto = :8900 ① 설정에서 고른 것 그대로 "
                         "(웹UI 와 모델을 맞춘다). 예: yolox_fastreid, yolo26_clipreid")
    ap.add_argument("--max-sec", type=float, default=0, help="앞부분만(초). 0=전체")
    a = ap.parse_args()

    pkg = vpkg.get(a.package)
    if not pkg:
        raise SystemExit(f"패키지 없음: {a.package}")
    root = Path(pkg["_root"])
    scen = next((s for s in pkg.get("scenarios", []) if s["id"] == a.scenario), None)
    if not scen:
        raise SystemExit(f"시나리오 없음: {a.scenario}")
    cam_cfg = {c["cam"]: c for c in pkg.get("cameras", [])}
    streams = [st for st in scen["streams"] if cam_cfg.get(st["cam"], {}).get("mapping")]
    skipped = [st["cam"] for st in scen["streams"] if st not in streams]
    if not streams:
        raise SystemExit("매핑된 카메라가 없다 — 먼저 ② 에서 매핑")
    floor_id = cam_cfg[streams[0]["cam"]].get("floor")
    fl, map_img = _load_site_floor(Path(a.site_dir), floor_id)
    site_floor = json.loads((Path(a.site_dir) / "site.json").read_text(encoding="utf-8"))
    exits = [e for f in site_floor["floors"] if f["id"] == floor_id for e in f.get("exits", [])]

    # ---- 카메라별 상태 ----
    from src.inference_gpu import BoostTrackGPUInference
    from tracker.boost_track import BoostTrack
    print(f"[viz] {a.package}/{a.scenario} · 카메라 {len(streams)}대 · 층 {floor_id} · 분석 {a.fps:.0f}fps"
          + (f" · 매핑 없어 제외: {skipped}" if skipped else ""))
    print("[viz] TRT 모델 로드…")
    model = BoostTrackGPUInference(profile=a.profile)
    print(f"[viz] 프로파일: {a.profile}"
          + (f" → {getattr(model, 'profile_id', getattr(model, 'profile', ''))}" if a.profile == "auto" else ""))
    cams = []
    for i, st in enumerate(streams):
        cap = cv2.VideoCapture(str(root / st["file"]))
        if not cap.isOpened():
            raise SystemExit(f"영상 열기 실패: {st['file']}")
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        c = cam_cfg[st["cam"]]
        H = np.array(c["mapping"]["H"], np.float64).reshape(3, 3)
        roi = np.array(c["valid_roi"], np.float32) if c.get("valid_roi") else _hull(c["mapping"]["cctv_pts"])
        cam_lines = [(e["id"], e["cam_line"], e.get("cam_zone")) for e in exits
                     if e.get("count_cam") == vpkg.cam_id_of(st["cam"]) and (e.get("cam_line") or e.get("cam_zone"))]
        cams.append({"cam": st["cam"], "cap": cap, "stride": max(1, int(round(src_fps / a.fps))),
                     "src_fps": src_fps, "H": H, "roi": roi, "has_roi": bool(c.get("valid_roi")),
                     "tracker": BoostTrack(), "color": CAM_COLORS[i % len(CAM_COLORS)],
                     "n_in": 0, "n_out": 0, "cam_lines": cam_lines,
                     "w": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "h": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))})

    # ---- 레이아웃 ----
    cw = a.cell
    ch = int(round(cw * 9 / 16))
    ncell = len(cams) + 1                         # + 정보 셀
    cols = 2
    rows = (ncell + cols - 1) // cols
    left_w, left_h = cw * cols, ch * rows
    ms = left_h / map_img.shape[0]
    map_w = int(round(map_img.shape[1] * ms))
    map_w += map_w % 2
    W, Hh = left_w + map_w, left_h
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{a.package}_{a.scenario}_grid.mp4"
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), a.fps, (W, Hh))
    map_base = cv2.resize(map_img, (map_w, left_h), interpolation=cv2.INTER_AREA)
    _draw_floor_elements(map_base, fl, ms)
    trails: dict[str, deque] = defaultdict(lambda: deque(maxlen=int(2 * a.fps)))
    last_seen: dict[str, int] = {}
    print(f"[viz] 출력 {W}x{Hh} @ {a.fps:.0f}fps → {out_path}")

    k = 0
    while True:
        if a.max_sec and k / a.fps > a.max_sec:
            break
        panels = []
        map_c = map_base.copy()
        alive = 0
        got_any = False
        for c in cams:
            frame = None
            for _ in range(c["stride"]):           # 서브샘플 — 라이브 analyze_fps 와 같은 밀도
                ok, fr = c["cap"].read()
                if not ok:
                    frame = None
                    break
                frame = fr
            if frame is None:
                panels.append(None)
                continue
            got_any = True
            pred, ref = model.detector.detect_frame(frame)
            targets = c["tracker"].update(pred, ref, frame, f"{c['cam']}:{k}")
            vis = frame.copy()
            # 유효영역(헐/ROI)
            cv2.polylines(vis, [c["roi"].astype(np.int32)], True, (255, 220, 0), 2, cv2.LINE_AA)
            for eid, line, zone in c["cam_lines"]:  # 화면 통과선
                if line:
                    cv2.line(vis, tuple(map(int, line[0])), tuple(map(int, line[1])), (0, 140, 255), 3, cv2.LINE_AA)
                    cv2.putText(vis, f"exit {eid}", tuple(map(int, line[0])), FONT, 0.8, (0, 140, 255), 2, cv2.LINE_AA)
                if zone:
                    cv2.polylines(vis, [np.array(zone, np.int32)], True, (0, 140, 255), 2, cv2.LINE_AA)
            for t in targets:
                x1, y1, x2, y2, tid = int(t[0]), int(t[1]), int(t[2]), int(t[3]), int(t[4])
                col = _color_id(tid)
                cv2.rectangle(vis, (x1, y1), (x2, y2), col, 2)
                cv2.putText(vis, str(tid), (x1, max(0, y1 - 6)), FONT, 0.8, col, 2, cv2.LINE_AA)
                fu, fv = (x1 + x2) / 2.0, float(y2)
                inside = _inside(c["roi"], fu, fv)
                p = cv2.perspectiveTransform(np.array([[[fu, fv]]], np.float64), c["H"])[0, 0]
                mx, my = int(round(p[0] * ms)), int(round(p[1] * ms))
                gid = f"{c['cam']}:{tid}"
                if inside:
                    c["n_in"] += 1
                    cv2.circle(vis, (int(fu), int(fv)), 7, (60, 220, 60), -1, cv2.LINE_AA)
                    trails[gid].append((mx, my))
                    last_seen[gid] = k
                    pts = list(trails[gid])
                    for j in range(1, len(pts)):
                        cv2.line(map_c, pts[j - 1], pts[j], c["color"], 2, cv2.LINE_AA)
                    cv2.circle(map_c, (mx, my), 7, c["color"], -1, cv2.LINE_AA)
                    cv2.circle(map_c, (mx, my), 7, (30, 30, 30), 1, cv2.LINE_AA)
                    cv2.putText(map_c, str(tid), (mx + 8, my - 6), FONT, 0.45, c["color"], 1, cv2.LINE_AA)
                else:
                    c["n_out"] += 1
                    cv2.drawMarker(vis, (int(fu), int(fv)), (60, 60, 255), cv2.MARKER_TILTED_CROSS, 22, 3)
                    if 0 <= mx < map_w and 0 <= my < left_h:
                        cv2.drawMarker(map_c, (mx, my), (150, 150, 150), cv2.MARKER_TILTED_CROSS, 10, 1)
            n_in_now = sum(1 for t in targets if _inside(c["roi"], (t[0] + t[2]) / 2.0, t[3]))
            small = cv2.resize(vis, (cw, ch), interpolation=cv2.INTER_AREA)
            cv2.rectangle(small, (0, 0), (cw, 26), (30, 30, 30), -1)
            cv2.rectangle(small, (0, 0), (cw, ch), c["color"], 2)
            cv2.putText(small, f"{c['cam']}  obj {len(targets)}  in-hull {n_in_now}"
                        + ("" if c["has_roi"] else "  (hull=4pts, no ROI)"),
                        (8, 18), FONT, 0.55, c["color"], 1, cv2.LINE_AA)
            panels.append(small)
        if not got_any:
            break
        alive = sum(1 for g, kk in last_seen.items() if k - kk <= 1)
        # 정보 셀
        info = np.full((ch, cw, 3), 28, np.uint8)
        lines = [f"t = {k / a.fps:5.1f}s   analyze {a.fps:.0f}fps   floor {floor_id}",
                 f"alive IDs {alive}   trail 2s", ""]
        for c in cams:
            tot = c["n_in"] + c["n_out"]
            pct = (100.0 * c["n_out"] / tot) if tot else 0.0
            lines.append(f"{c['cam']:6s} in {c['n_in']:4d}  out {c['n_out']:4d}  dropped {pct:4.0f}%")
        lines += ["", "green dot = projected (kept)", "red X = outside hull -> DROPPED live"]
        for i, s in enumerate(lines):
            cv2.putText(info, s, (10, 24 + i * 22), FONT, 0.5, (220, 220, 220), 1, cv2.LINE_AA)
        panels.append(info)
        while len(panels) < ncell:
            panels.append(np.zeros((ch, cw, 3), np.uint8))
        grid_rows = []
        for r in range(rows):
            row = [p if p is not None else np.zeros((ch, cw, 3), np.uint8) for p in panels[r * cols:(r + 1) * cols]]
            while len(row) < cols:
                row.append(np.zeros((ch, cw, 3), np.uint8))
            grid_rows.append(np.hstack(row))
        left = np.vstack(grid_rows)
        cv2.rectangle(map_c, (0, 0), (map_w, 26), (30, 30, 30), -1)
        cv2.putText(map_c, f"2D MAP {floor_id}  t={k / a.fps:.1f}s  alive {alive}", (8, 18), FONT, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        writer.write(np.hstack([left, map_c]))
        k += 1
        if k % 25 == 0:
            print(f"    t={k / a.fps:.0f}s")
    writer.release()
    for c in cams:
        c["cap"].release()
    tmp = str(out_path) + ".tmp.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out_path), "-c:v", "libx264",
                        "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", tmp])
    if r.returncode == 0:
        Path(tmp).replace(out_path)
    print(f"[viz] 완료 {k}프레임({k / a.fps:.0f}s) → {out_path}")
    for c in cams:
        tot = c["n_in"] + c["n_out"]
        print(f"   {c['cam']}: 관측 {tot} · 헐 안 {c['n_in']} · 헐 밖(라이브 폐기) {c['n_out']} ({(100 * c['n_out'] / tot) if tot else 0:.0f}%)"
              + ("" if c["has_roi"] else " · ROI 없음→대응점 헐 사용"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
