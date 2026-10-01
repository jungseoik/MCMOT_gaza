#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""장애물 표시 편집기 (발표 시각화 전용) — :8920

**운영 웹 UI(:8900)·CAD 편집기(:8910)와 완전히 분리된 도구다.** 저 둘의 설정·도면을
전혀 건드리지 않는다. 여기서 찍은 장애물은 `results/demo/_annotate/obstacles_<층>.json`
한 파일에만 저장되고, 그 파일을 읽는 곳은 발표용 시각화(tools/demo_viz.py)뿐이다.

무엇에 영향을 주나:
  · 영상 좌측 도면에 **그려진다** (표출)
  · 재실자별 **최적 피난경로 산출을 막는다** (의자를 통과하는 경로가 나오면 안 되니까)
  · 4대 지표(IDR·EPFI·CBS·SEI) 값에는 **영향이 없다** — 지표는 사람이 그린 공통
    경로 기준으로 이미 산출돼 있고, 이 파일은 그 계산에 들어가지 않는다

배경으로 **실제 동선**(그 시나리오에서 사람이 지나간 자리)을 겹쳐 볼 수 있다 —
"여기는 실제로 사람이 다녔으니 막으면 안 된다"를 보면서 찍으라는 뜻이다.

    python tools/obstacle_editor.py          # http://0.0.0.0:8920
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

import sys as _sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in _sys.path:          # 레포 루트를 넣어야 system.* 를 찾는다
    _sys.path.insert(0, str(ROOT))
SITE = ROOT / "data/sites/default"
ANN = ROOT / "results/demo/_annotate"
ANN.mkdir(parents=True, exist_ok=True)

# CAD 편집기(evac/web.py OBSTACLE_TAGS)와 **같은 3종·같은 색**을 쓴다.
# 두 화면에서 같은 것이 다른 색으로 보이면 그때부터 오해가 시작된다.
METRICS_BASE = os.environ.get("MACS_BASE", "http://127.0.0.1:8900")

TAGS = {
    "chair": {"name": "의자·집기", "rgb": "180,83,9"},
    "box":   {"name": "박스·적치물", "rgb": "124,58,237"},
    "nogo":  {"name": "통행금지", "rgb": "220,38,38"},
}
# 3층은 floor4(합동 훈련)·floor6(1차 리허설)이 같은 도면이라 **한 벌**만 찍으면 된다.
FLOORS = [
    {"id": "floor4", "name": "AI hub 3층", "map": "map_floor4.png",
     "paths": "floor4_paths.png", "alias": ["floor6"],
     "session": "sess-1789693478593"},          # 경로 오버레이용 대표 세션
    {"id": "floor5", "name": "AI hub 1층", "map": "map_floor5.png",
     "paths": "floor5_paths.png", "alias": [],
     "session": "sess-1789693709646"},
]

# 투영만으로는 못 정하는 기준점. 사람이 찍어 줘야 한다.
#   start       각본에서 10명이 모여 출발한 자리
#   stair_exit  계단을 내려온 사람이 **그 층 바닥으로 나오는** 문
#               (계단을 내려오는 사람의 발끝 투영은 계단실 벽 안쪽에 찍힌다)
ANCHORS = {"start": {"name": "시작지점", "rgb": "16,185,129"},
           "stair_exit": {"name": "계단 출구", "rgb": "234,88,12"}}

app = FastAPI(title="장애물 표시 편집기")


def anchor_path(floor: str) -> Path:
    return ANN / f"anchors_{floor}.json"


def load_anchors(floor: str) -> dict:
    p = anchor_path(floor)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"floor": floor, "points": {}}


@app.get("/api/anchors/{floor}")
def get_anchors(floor: str):
    _floor(floor)
    return load_anchors(floor)


@app.put("/api/anchors/{floor}")
def put_anchors(floor: str, body: dict):
    _floor(floor)
    pts = {}
    for k, v in (body.get("points") or {}).items():
        if k not in ANCHORS:
            raise HTTPException(422, f"기준점 이름은 {list(ANCHORS)} — 받은 값 {k!r}")
        pts[k] = [float(v[0]), float(v[1])]
    data = {"floor": floor, "points": pts}
    anchor_path(floor).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    for al in _floor(floor)["alias"]:
        anchor_path(al).write_text(json.dumps({**data, "floor": al, "_from": floor},
                                              ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    return {"saved": len(pts), "path": str(anchor_path(floor).relative_to(ROOT)),
            "alias": _floor(floor)["alias"]}


def ann_path(floor: str) -> Path:
    return ANN / f"obstacles_{floor}.json"


def _floor(fid: str) -> dict:
    for f in FLOORS:
        if f["id"] == fid:
            return f
    raise HTTPException(404, f"모르는 층: {fid}")


@app.get("/api/floors")
def floors():
    out = []
    for f in FLOORS:
        d = load_obstacles(f["id"])
        out.append({**f, "count": len(d["obstacles"])})
    return {"floors": out, "tags": TAGS, "anchors": ANCHORS}


def load_obstacles(floor: str) -> dict:
    p = ann_path(floor)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"floor": floor, "obstacles": []}


@app.get("/api/obstacles/{floor}")
def get_obstacles(floor: str):
    _floor(floor)
    return load_obstacles(floor)


@app.put("/api/obstacles/{floor}")
def put_obstacles(floor: str, body: dict):
    _floor(floor)
    obs = []
    for o in body.get("obstacles", []):
        tag = str(o.get("tag") or "nogo")
        if tag not in TAGS:
            raise HTTPException(422, f"tag 는 {list(TAGS)} — 받은 값 {tag!r}")
        pts = [[float(p[0]), float(p[1])] for p in (o.get("points") or [])]
        if len(pts) < 3:
            raise HTTPException(422, "장애물은 꼭짓점 3개 이상이 필요합니다")
        obs.append({"tag": tag, "points": pts})
    data = {"floor": floor, "obstacles": obs}
    ann_path(floor).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    # 3층처럼 같은 도면을 쓰는 층에 그대로 복사 — 두 번 찍게 하지 않는다
    for al in _floor(floor)["alias"]:
        ann_path(al).write_text(json.dumps({**data, "floor": al, "_from": floor},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
    return {"saved": len(obs), "path": str(ann_path(floor).relative_to(ROOT)),
            "alias": _floor(floor)["alias"]}


@app.get("/img/map/{floor}")
def img_map(floor: str):
    return FileResponse(SITE / _floor(floor)["map"])


@app.get("/img/paths/{floor}")
def img_paths(floor: str):
    p = ANN / _floor(floor)["paths"]
    if not p.is_file():
        raise HTTPException(404, "동선 오버레이가 아직 없습니다 — demo_viz 밑그림 생성 필요")
    return FileResponse(p)


@app.get("/img/conn/{floor}")
def img_conn(floor: str):
    """연결성 오버레이 — **어디가 뚫려 있나**를 보고 막을 자리를 고르라고 깐다.

    초록 = 출구까지 이어진 보행공간 · 주황 = 고립. 운영 서버(:8900)가 CAD 격자로
    한 번 풀어 캐시해 둔 것을 그대로 가져온다(같은 판정을 두 번 구현하지 않는다).
    """
    import urllib.error
    import urllib.request
    url = f"{METRICS_BASE}/api/floor/connectivity?floor={floor}"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return Response(r.read(), media_type="image/png")
    except (urllib.error.URLError, OSError) as e:
        raise HTTPException(503, f"연결성을 못 받았습니다 — :8900 이 떠 있어야 합니다 ({e})")


@app.get("/img/routes/{floor}")
def img_routes(floor: str):
    """재실자별 피난경로 오버레이 — 경로가 **어디로 빠지는지** 보여준다.

    장애물을 막으면 이 선이 바뀐다. 막을 자리를 고를 때 근거가 된다.
    """
    import cv2
    import numpy as np
    from system.evac import person_routes as pr
    from tools.session_viz import find_session
    fl = _floor(floor)
    sid = fl.get("session")
    if not sid:
        raise HTTPException(404, f"{floor}: 대표 세션이 없습니다")
    db, _f = find_session(sid, floor)
    rt = pr.load(db)
    if rt is None:
        raise HTTPException(404, "경로 사이드카가 없습니다 — demo_viz 를 한 번 돌리세요")
    img = cv2.imread(str(SITE / fl["map"]))
    ov = img.copy()
    for r in rt["routes"]:
        pts = np.array(r["points"], np.int32)
        cv2.polylines(ov, [pts], False, (250, 165, 96), 3, cv2.LINE_AA)
    cv2.addWeighted(ov, 0.75, img, 0.25, 0, img)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise HTTPException(500, "경로 PNG 인코딩 실패")
    return Response(buf.tobytes(), media_type="image/png")


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "obstacle_editor.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8920)
