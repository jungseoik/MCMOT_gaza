#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""**동선 편집기** — 발표 영상의 점 움직임을 사람이 직접 그린다. :8930

왜 만들었나: 실측 트랙렛을 이어 붙여 각본대로 연출하는 자동 방식으로는 싱크가
끝내 안 맞았다. 이어 붙이고 자르고 미는 손질이 쌓일 때마다 어딘가 튀었다.

여기서는 **④ 리플레이와 같은 타임라인** 위에서 사람이 직접 찍는다. 리플레이는
녹화본을 그대로 흘려보내는 화면이라 우측 그리드 영상과 처음부터 싱크가 맞는다.
그 위에서 "이 순간 이 사람은 여기" 를 찍으면 싱크는 **저절로** 맞는다.

찍는 것은 **(시각, 위치)** 한 쌍이다 — 위치만 받으면 속도를 알 수 없고, 속도를
이쪽에서 지어내는 순간 다시 어긋난다. 재생하며 찍으면 둘 다 한 번에 들어온다.

    python tools/path_editor.py          # http://0.0.0.0:8930

화면
  · 좌 — 도면. 회색 점 = 녹화본 그대로(참고용), 색점 = 내가 그린 사람
  · 우 — 카메라 영상 한 대(고를 수 있음). 같은 시각으로 따라간다
  · 아래 — 타임라인. 재생/정지·배속·한 프레임씩

저장 위치 `results/demo/_annotate/paths_<세션>.json`
"""
from __future__ import annotations

import json
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from system.config.schema import SiteConfig          # noqa: E402
from system.metrics import recorder                  # noqa: E402
from system.metrics.replay import run_replay         # noqa: E402
from tools import demo_ghosts as dghost              # noqa: E402
from tools import demo_obstacles as dobs             # noqa: E402
from tools.session_viz import (                      # noqa: E402
    drill_label, find_session, resolve_clips,
)

SITE = ROOT / "data/sites/default"
ANN = ROOT / "results/demo/_annotate"
ANN.mkdir(parents=True, exist_ok=True)

# 발표 영상 4건. 각본 인원은 10명으로 고정한다(모든 각본이 10명이다).
SESSIONS = [
    {"id": "sess-1789693149252", "floor": "floor4", "tag": "01_IDR",
     "name": "01 IDR · 권장경로 + 우측 우회 5명"},
    {"id": "sess-1789693292022", "floor": "floor4", "tag": "02_EPFI",
     "name": "02 EPFI · 우측 빙 돌아 줄서서 4명"},
    {"id": "sess-1789693478593", "floor": "floor4", "tag": "03_CBS",
     "name": "03 CBS · 한 무리로 뭉쳐서"},
    {"id": "sess-1789693805049", "floor": "floor5", "tag": "04_SEI",
     "name": "04 SEI · 1층 반반 나눠 나가기"},
]
N_PEOPLE = 10

app = FastAPI(title="동선 편집기")
_cache: dict = {}


def _session(sid: str) -> dict:
    for s in SESSIONS:
        if s["id"] == sid:
            return s
    raise HTTPException(404, f"모르는 세션: {sid}")


def _load(sid: str) -> dict:
    """녹화본 → 참고용 점·출구·병목·카메라. 한 번 읽고 캐시한다(재생 중 재계산 금지)."""
    if sid in _cache:
        return _cache[sid]
    s = _session(sid)
    db, floor = find_session(sid, s["floor"])
    meta = recorder.load_meta(db)
    site = SiteConfig.model_validate(meta["site_view"])
    t0 = float(meta["alarm_ts"])
    gh = [g["key"] for g in dghost.load(sid)]
    res, tl, frames, _ = run_replay(db, {"exclude_tracks": gh} if gh else {}, fps=5.0)

    # 참고 점 — ④ 리플레이가 그리는 것과 같은 내용(원 관측)
    ref = [{"t": round(f["ts"] - t0, 2),
            "o": [[round(o["x"], 1), round(o["y"], 1), o["gid"]] for o in f["objects"]]}
           for f in frames]
    # 출구 통과 실제 시각 — 찍을 때 "이 사람은 여기서 나가야 한다"의 기준
    passes, prev = {}, {}
    for p in tl:
        d = p.model_dump() if hasattr(p, "model_dump") else p
        for k, v in (d.get("exit_counts") or {}).items():
            n = v - prev.get(k, 0)
            if n > 0:
                passes.setdefault(k, []).extend([round(d["ts"] - t0, 1)] * n)
                prev[k] = v
    for k in passes:
        passes[k].sort()

    cams = sorted({c["cam_id"] for c in (meta.get("cameras") or [])})
    clips, _desc = resolve_clips(cams, drill_label(sid) or sid, None, None)
    mp = site.map
    data = {
        "id": sid, "floor": floor, "name": s["name"], "tag": s["tag"],
        "map_px": [mp.w, mp.h], "m_per_px": mp.resolve_m_per_px(),
        "t_end": round(max(r["t"] for r in ref), 1) if ref else 0.0,
        "ref": ref,
        "exits": [{"id": e.id, "line": e.line} for e in site.exits if e.line],
        "bottlenecks": [{"id": b.id, "polygon": b.polygon} for b in site.bottlenecks],
        "obstacles": dobs.load(floor),
        "cams": [c for c in cams if c in clips],
        "passes": passes,
    }
    _cache[sid] = data
    _cache.setdefault("_clips", {})[sid] = {k: str(v) for k, v in clips.items()}
    return data


@app.get("/api/sessions")
def sessions():
    return {"sessions": SESSIONS, "n_people": N_PEOPLE}


@app.get("/api/session/{sid}")
def session(sid: str):
    return _load(sid)


def paths_file(sid: str) -> Path:
    return ANN / f"paths_{sid}.json"


@app.get("/api/paths/{sid}")
def get_paths(sid: str):
    _session(sid)
    p = paths_file(sid)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"session_id": sid, "people": {}}


@app.put("/api/paths/{sid}")
def put_paths(sid: str, body: dict):
    _session(sid)
    out = {}
    for pid, keys in (body.get("people") or {}).items():
        ks = []
        for k in keys:
            ks.append({"t": round(float(k["t"]), 2),
                       "x": round(float(k["x"]), 1),
                       "y": round(float(k["y"]), 1)})
        ks.sort(key=lambda k: k["t"])
        if ks:
            out[pid] = ks
    data = {"session_id": sid, "people": out,
            "exit_at": body.get("exit_at") or {}}
    paths_file(sid).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    return {"saved": len(out), "keys": sum(len(v) for v in out.values()),
            "path": str(paths_file(sid).relative_to(ROOT)),
            "warn": _wall_check(sid, out)}


def _wall_check(sid: str, people: dict) -> list[str]:
    """그린 선이 **벽·장애물을 뚫는지** 저장할 때 알려 준다.

    브라우저에서 벽을 판정하려면 격자를 통째로 내려보내야 하고, 그린 뒤에 알게
    되면 다시 그려야 한다. 저장 순간에 서버가 한 번 봐 주는 게 싸다."""
    try:
        from system.evac.route_solver import _los
        from tools.demo_paths import _catmull
        from tools.demo_tracks_build import distfield
        s = _session(sid)
        db, floor = find_session(sid, s["floor"])
        site = SiteConfig.model_validate(recorder.load_meta(db)["site_view"])
        df, _ = distfield(site, floor, site.map.resolve_m_per_px())
    except Exception:
        return []
    import numpy as np
    bad = []
    for pid, ks in people.items():
        if len(ks) < 2:
            continue
        grid = np.arange(ks[0]["t"], ks[-1]["t"] + 0.05, 0.1)
        xy = _catmull(ks, grid)
        hit = sum(1 for a, b in zip(xy, xy[1:])
                  if not _los(df.wall, df.to_cell(*a), df.to_cell(*b)))
        if hit:
            bad.append(f"{pid}({hit})")
    return bad


@app.get("/img/map/{floor}")
def img_map(floor: str):
    name = "map.png" if floor == "default" else f"map_{floor}.png"
    return FileResponse(SITE / name)


_caps: dict = {}


@app.get("/frame/{sid}/{cam}")
def frame(sid: str, cam: str, t: float = 0.0, w: int = 420):
    """그 시각의 **프레임 한 장**(JPEG).

    <video> 7개를 한꺼번에 띄우면 브라우저가 622MB 를 물고 멈춘다(실측: 03 CBS).
    스크럽하며 보는 작업이라 프레임 한 장이면 충분하고, 그게 훨씬 가볍다.
    """
    import cv2
    from fastapi.responses import Response

    _load(sid)
    fp = _cache.get("_clips", {}).get(sid, {}).get(cam)
    if not fp:
        raise HTTPException(404, f"영상 없음: {cam}")
    key = (sid, cam)
    cap = _caps.get(key)
    if cap is None:
        cap = cv2.VideoCapture(str(fp))
        if not cap.isOpened():
            raise HTTPException(404, f"영상을 못 엽니다: {cam}")
        _caps[key] = cap
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(t * fps)))
    ok, im = cap.read()
    if not ok:
        raise HTTPException(404, "프레임 없음")
    h = max(1, int(im.shape[0] * w / im.shape[1]))
    im = cv2.resize(im, (w, h))
    ok, buf = cv2.imencode(".jpg", im, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
    return Response(buf.tobytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/video/{sid}/{cam}")
def video(sid: str, cam: str):
    _load(sid)
    fp = _cache.get("_clips", {}).get(sid, {}).get(cam)
    if not fp:
        raise HTTPException(404, f"영상 없음: {cam}")
    return FileResponse(fp, media_type="video/mp4")      # Range 지원


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "path_editor.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8930)
