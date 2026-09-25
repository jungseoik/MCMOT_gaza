# -*- coding: utf-8 -*-
"""**붙박이 오검출**(사람이 아닌 것) 골라내기 — 발표 시각화 전용.

발표용 영상은 "지표가 무엇을 재는가"를 보여주는 자리다. 도면 위에 한 자리에
40초씩 박혀 있는 점이 있으면 보는 사람은 그게 지표 이야기인 줄 안다. 실제로는
의자·게시물 같은 걸 사람으로 잡은 것이다. 그런 점은 **빼고** 보여준다.

판정은 보수적으로 간다 — 진짜 사람을 지우면 그게 더 큰 거짓말이다:
  ① 앵커 — {MIN_SEC}초 이상 살아 있으면서 이동 반경이 {MAX_MOVE_M}m 미만
  ② 같은 자리 재검출 — 앵커 {NEAR_M}m 안에서만 놀다 간 트랙(트래커가 id 를
     새로 딴 같은 물체). 이건 {MIN_SEC_NEAR}초만 넘으면 같이 뺀다

훈련 중 **잠깐 멈춘 사람**은 걸리지 않는다(각본 03 의 "2초 정지"는 물론이고,
문 앞에서 20초를 기다려도 반경 1m 안에 40초를 박혀 있지는 않는다).

빼는 대상은 `results/demo/_annotate/exclude_<세션>.json` 에 근거(위치·지속·반경)
까지 남긴다. 손으로 고칠 수 있고, 나중에 "왜 뺐냐"에 답할 수 있어야 한다.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANN = ROOT / "results/demo/_annotate"

MIN_SEC = 25.0        # 앵커 판정 — 이만큼 붙박이면 사람이 아니다
MAX_MOVE_M = 1.0      # 앵커 이동 반경 상한
NEAR_M = 1.0          # 같은 자리 재검출로 볼 거리
MIN_SEC_NEAR = 6.0    # 재검출은 이 정도만 살아 있어도 같이 뺀다


def path(session_id: str) -> Path:
    return ANN / f"exclude_{session_id}.json"


def detect(db_path) -> list[dict]:
    """녹화본 → 붙박이 오검출 트랙 목록. 엔진과 같은 투영기를 쓴다."""
    import numpy as np

    from system.config.schema import CameraConfig, SiteConfig
    from system.metrics import recorder
    from system.spatial import CameraProjector

    meta = recorder.load_meta(db_path)
    site = SiteConfig.model_validate(meta["site_view"])
    m_per_px = site.map.resolve_m_per_px() if site.map else None
    if not m_per_px:
        return []
    cams = [CameraConfig.model_validate(c) for c in (meta.get("cameras") or [])]
    mw = site.map.w if site.map else None
    mh = site.map.h if site.map else None
    projs = {c.cam_id: CameraProjector(c, mw, mh) for c in cams if c.mapping is not None}

    seen: dict[str, list] = {}
    for cam_id, ts, tracks in recorder.iter_calls(db_path):
        pr = projs.get(cam_id)
        if pr is None:
            continue
        for tr in tracks:
            p = pr.project(tr.foot_uv)
            if p is not None and getattr(p, "in_bounds", True):
                seen.setdefault(f"{cam_id}:{tr.local_track_id}", []).append((ts, p.x, p.y))

    stat = {}
    for key, pts in seen.items():
        if len(pts) < 5:
            continue
        xs = np.array([q[1] for q in pts]); ys = np.array([q[2] for q in pts])
        rng = float(np.hypot(xs.max() - xs.min(), ys.max() - ys.min())) * m_per_px
        stat[key] = {"key": key, "dur": pts[-1][0] - pts[0][0], "range_m": rng,
                     "x": float(xs.mean()), "y": float(ys.mean()), "n": len(pts)}

    anchors = [s for s in stat.values()
               if s["dur"] >= MIN_SEC and s["range_m"] < MAX_MOVE_M]
    out = {a["key"]: {**a, "reason": f"{a['dur']:.0f}초간 반경 {a['range_m']:.2f}m — 붙박이"}
           for a in anchors}
    for s in stat.values():
        if s["key"] in out or s["dur"] < MIN_SEC_NEAR:
            continue
        for a in anchors:
            d = ((s["x"] - a["x"]) ** 2 + (s["y"] - a["y"]) ** 2) ** 0.5 * m_per_px
            if d < NEAR_M and s["range_m"] < MAX_MOVE_M:
                out[s["key"]] = {**s, "reason": f"{a['key']} 와 같은 자리({d:.2f}m) 재검출"}
                break
    return sorted(out.values(), key=lambda s: -s["dur"])


def save(session_id: str, ghosts: list[dict]) -> Path:
    p = path(session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"session_id": session_id, "exclude": ghosts},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load(session_id: str) -> list[dict]:
    p = path(session_id)
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("exclude") or []
    except (OSError, json.JSONDecodeError):
        return []


def contact_sheet(db_path, session_id, ghosts, out_png, label=None):
    """후보를 **원본 영상에서 잘라** 한 장에 붙인다 — 눈으로 보고 확정하라고.

    이 단계는 건너뛰면 안 된다. 실측으로 확인했듯 같은 규칙이 **진짜 사람**도
    문다(계단에서 26초 서 있던 사람, 소파에 39초 앉아 있던 사람). 사람을 지운
    영상은 오검출을 남긴 영상보다 나쁘다.
    """
    import cv2
    import numpy as np

    from system.metrics import recorder
    from tools.session_viz import drill_label, resolve_clips

    meta = recorder.load_meta(db_path)
    t_base = float(meta.get("source_t0") or meta["alarm_ts"])
    label = label or drill_label(session_id) or session_id
    want = [(g["key"].rsplit(":", 1)[0], int(g["key"].rsplit(":", 1)[1])) for g in ghosts]
    clips, _ = resolve_clips(sorted({c for c, _ in want}), label, None, None)

    crops = []
    for (cam, tid), g in zip(want, ghosts):
        fp = clips.get(cam)
        rows = [(ts, tr) for c, ts, trs in recorder.iter_calls(db_path) if c == cam
                for tr in trs if tr.local_track_id == tid]
        if not fp or not rows:
            continue
        ts, tr = rows[len(rows) // 2]
        cap = cv2.VideoCapture(str(fp))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int((ts - t_base) * fps)))
        ok, im = cap.read()
        cap.release()
        if not ok:
            continue
        x1, y1, x2, y2 = tr.bbox_xyxy
        cv2.rectangle(im, (int(x1), int(y1)), (int(x2), int(y2)), (0, 0, 255), 3)
        pad = int(max(x2 - x1, y2 - y1) * 1.7)
        cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)
        c = im[max(0, cy - pad):cy + pad, max(0, cx - pad):cx + pad]
        if c.size == 0:
            continue
        c = cv2.resize(c, (320, int(320 * c.shape[0] / max(1, c.shape[1]))))
        cv2.putText(c, f"{cam.split('_')[-1]}:{tid}", (6, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2)
        crops.append(c)
    if not crops:
        return None
    h = max(c.shape[0] for c in crops)
    sheet = np.full((h, 320 * len(crops), 3), 20, np.uint8)
    for i, c in enumerate(crops):
        sheet[:c.shape[0], i * 320:(i + 1) * 320] = c
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_png), sheet)
    return out_png


def _main() -> int:
    import argparse
    import sys as _sys
    _sys.path.insert(0, str(ROOT))
    from tools.session_viz import find_session

    ap = argparse.ArgumentParser(description="붙박이 오검출 후보 제안 — 확정은 사람이")
    ap.add_argument("--session", required=True)
    ap.add_argument("--floor")
    ap.add_argument("--sheet", help="후보 크롭 대조표 PNG 경로")
    ap.add_argument("--save", action="store_true", help="후보를 제외 목록으로 저장")
    a = ap.parse_args()

    db, floor = find_session(a.session, a.floor)
    g = detect(db)
    print(f"{a.session} ({floor}) → 후보 {len(g)}건")
    for s in g:
        print(f"  {s['key']:>24}  ({s['x']:5.0f},{s['y']:5.0f})  {s['dur']:5.1f}s  {s['reason']}")
    if a.sheet and g:
        print("대조표 →", contact_sheet(db, a.session, g, a.sheet))
    if a.save:
        print("저장 →", save(a.session, g), "— 사람이 섞였으면 이 파일에서 지울 것")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
