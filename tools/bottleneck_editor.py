# -*- coding: utf-8 -*-
"""**병목 위치 편집기** (발표 시각화 전용) — :8940

운영 웹 UI(:8900)·CAD 편집기(:8910)와 **완전히 분리된 도구다.** 저 둘의 설정을
전혀 건드리지 않는다. 여기서 옮긴 값은 `results/demo/_annotate/bn_edit_<층>.json`
한 파일에만 저장되고, 그 파일을 읽는 곳은 발표용 시각화(`tools/demo_bottleneck`)
뿐이다 — demo 영상과 v2 지표가 같은 자리를 본다.

왜 만들었나: 병목이 출구선 바로 위에 얹혀 있어 문과 겹쳐 보였는데, "얼마나
위로" 를 말로 주고받으며 맞추는 게 더 느렸다. 도면 위에서 끌어다 놓는 게 빠르다.

    python tools/bottleneck_editor.py          # http://0.0.0.0:8940
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from system.config.schema import SiteConfig          # noqa: E402
from system.metrics import recorder                  # noqa: E402
from tools import demo_bottleneck as dbn             # noqa: E402
from tools.session_viz import find_session           # noqa: E402

SITE = ROOT / "data/sites/default"
ANN = ROOT / "results/demo/_annotate"
HERE = Path(__file__).resolve().parent

# 층마다 대표 세션 하나 — 병목·출구는 세션 스냅샷에서 읽는다(훈련 당시 기준).
FLOORS = [
    {"id": "floor4", "name": "AI hub 3층", "session": "sess-1789693478593",
     "map": "map_floor4.png"},
    {"id": "floor5", "name": "AI hub 1층", "session": "sess-1789693709646",
     "map": "map_floor5.png"},
]

app = FastAPI(title="병목 위치 편집기")


def _edit_path(floor: str) -> Path:
    return ANN / f"bn_edit_{floor}.json"


def _load_edit(floor: str) -> dict:
    p = _edit_path(floor)
    if not p.is_file():
        return {"move": {}, "scale": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"move": {}, "scale": {}}
    return {"move": d.get("move") or {}, "scale": d.get("scale") or {}}


@app.get("/", response_class=HTMLResponse)
def index():
    return (HERE / "bottleneck_editor.html").read_text(encoding="utf-8")


@app.get("/map/{floor}")
def map_png(floor: str):
    fl = next((f for f in FLOORS if f["id"] == floor), None)
    if fl is None:
        raise HTTPException(404, f"모르는 층: {floor}")
    p = SITE / fl["map"]
    if not p.is_file():
        raise HTTPException(404, p.name)
    return FileResponse(p, media_type="image/png")


@app.get("/api/meta")
def meta(floor: str = "floor4"):
    """도면 크기 + 병목(원래 모양) + 출구선 + 저장된 편집본."""
    fl = next((f for f in FLOORS if f["id"] == floor), None)
    if fl is None:
        raise HTTPException(404, f"모르는 층: {floor}")
    db, _f = find_session(fl["session"], floor)
    site = SiteConfig.model_validate(recorder.load_meta(db)["site_view"])
    bns = []
    for b in (site.bottlenecks or []):
        sh = b.shape.model_dump() if b.shape else None
        bns.append({"id": b.id, "name": b.name or b.id,
                    "polygon": [list(p) for p in b.polygon],
                    "rho_crit": b.rho_crit,
                    "center": (list(sh["center"]) if sh and sh.get("center") else None),
                    "radius": (sh.get("radius") if sh else None),
                    "sector": bool(sh and sh.get("kind") == "sector")})
    exits = [{"id": e.id, "line": [list(e.line[0]), list(e.line[1])]}
             for e in (site.exits or []) if e.line]
    return {"floors": FLOORS, "floor": floor,
            "map": {"w": site.map.w, "h": site.map.h,
                    "m_per_px": site.map.resolve_m_per_px()},
            "bottlenecks": bns, "exits": exits,
            "edit": _load_edit(floor),
            "default_scale": dbn.SCALE, "per_id": dbn.PER_ID}


@app.put("/api/edit")
def save(payload: dict):
    """{floor, move:{id:[dx,dy]}, scale:{id:배수}} 저장."""
    floor = str(payload.get("floor") or "")
    if not any(f["id"] == floor for f in FLOORS):
        raise HTTPException(422, f"모르는 층: {floor!r}")
    move = {str(k): [float(v[0]), float(v[1])]
            for k, v in (payload.get("move") or {}).items()}
    scale = {str(k): float(v) for k, v in (payload.get("scale") or {}).items()}
    ANN.mkdir(parents=True, exist_ok=True)
    p = _edit_path(floor)
    p.write_text(json.dumps({"floor": floor, "move": move, "scale": scale},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    dbn._LOADED["floor"] = None          # 다음 렌더가 새로 읽게
    return {"saved": str(p.relative_to(ROOT)), "move": move, "scale": scale}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8940, log_level="warning")
