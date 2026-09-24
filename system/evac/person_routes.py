# -*- coding: utf-8 -*-
"""녹화 세션 → **재실자별 피난경로** 산출 (오프라인).

왜 오프라인인가: 훈련 실행 중에 뽑을 이유가 없다. ④ 리플레이는 녹화본(.db)을
다시 흘려보내 그리는 화면이라, 경로를 세션이 끝난 뒤 한 번 만들어 저장해 두면
재생할 때 "마치 실시간처럼" 그대로 재현된다. 실행 중 부하 0.

왜 트랙 **첫 등장**마다인가: 경보 순간에는 트래커가 아직 워밍업이라 잡힌 트랙이
거의 없다 — 실측(세션 27건) 경보시점 평균 2.8명, 절반이 0명이었다. 1층처럼
사람이 나중에 내려오는 각본은 +5초에도 0~2명이다. 그래서 경보 스냅샷이 아니라
**각 트랙이 처음 도면에 투영된 위치**를 시드로 쓴다.

산출된 경로는 site.json 에 넣지 않는다 — 다음 훈련에 섞인다. 세션 옆 사이드카
(<session_id>.routes.json)에 두고, 리플레이가 geometry 오버라이드로 얹는다.
"""
from __future__ import annotations

import json
from pathlib import Path

from system.config.schema import SiteConfig
from system.evac.route_solver import build_distfield, solve_path
from system.metrics import recorder

ROUTES_SUFFIX = ".routes.json"
MAX_ROUTES = 200          # 안전 상한 — 트랙이 수백 개인 세션에서 경로가 폭발하지 않게


def routes_path(db_path) -> Path:
    p = Path(db_path)
    return p.with_name(p.stem + ROUTES_SUFFIX)


def _map_png(site: SiteConfig, floor_id: str, site_dir: Path) -> Path:
    name = "map.png" if floor_id == site.floors[0].id else f"map_{floor_id}.png"
    # 층 id 가 default 가 아니면 map_<id>.png (store.map_path 규칙과 정합)
    if floor_id != "default":
        name = f"map_{floor_id}.png"
    else:
        name = "map.png"
    return site_dir / name


def _edits_from_floor_json(site_dir: Path, floor_id: str, m_per_px: float):
    """CAD 편집기가 남긴 뚫기/막기 도형 → (뚫을 선들, 막을 다각형들) 맵 px.

    floor_<층>.json 의 shapes(도면 mm)를 맵 px 로 옮긴다. 그 파일의 bounds_mm·
    map_px 가 변환의 정본이다(편집기 apply 가 같은 식으로 경로·출구를 옮겼다).
    파일이 없거나 shapes 가 비면 빈 값 — 호출부가 경로 기반 우회로 넘어간다.
    """
    name = "floor.json" if floor_id == "default" else f"floor_{floor_id}.json"
    p = Path(site_dir) / name
    if not p.is_file():
        return [], []
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], []
    shapes = meta.get("shapes") or []
    if not shapes:
        return [], []
    minx, miny, maxx, maxy = meta["bounds_mm"]
    w_px, h_px = meta["map_px"]

    def to_px(x, y):
        return [(x - minx) / (maxx - minx) * w_px,
                (maxy - y) / (maxy - miny) * h_px]

    carve, blocks = [], []
    for sh in shapes:
        pts = sh.get("pts") or []
        xy = [to_px(pts[i], pts[i + 1]) for i in range(0, len(pts) - 1, 2)]
        if len(xy) < 2:
            continue
        if sh.get("op") == "open":
            carve.append(xy if sh.get("kind") != "rect" else
                         [xy[0], [xy[1][0], xy[0][1]], xy[1], [xy[0][0], xy[1][1]], xy[0]])
        else:
            if sh.get("kind") == "rect":
                xy = [xy[0], [xy[1][0], xy[0][1]], xy[1], [xy[0][0], xy[1][1]]]
            if len(xy) >= 3:
                blocks.append(xy)
    return carve, blocks


def generate(db_path, site_dir, *, max_routes: int = MAX_ROUTES) -> dict:
    """녹화본 → 자동 피난경로 목록. 반환값이 곧 사이드카 내용.

    {"floor_id", "m_per_px", "routes":[{id,name,points}],
     "stats":{seeds, made, unreachable, snapped_out}, "generated_from"}
    """
    site_dir = Path(site_dir)
    meta = recorder.load_meta(db_path)
    site = SiteConfig.model_validate(meta["site_view"])
    floor_id = meta.get("floor_id") or "default"

    mp = site.map
    m_per_px = mp.resolve_m_per_px() if mp else None
    if not m_per_px:
        raise ValueError(f"{floor_id}: 도면 축척이 없어 경로를 만들 수 없습니다.")
    exits = [[tuple(e.line[0]), tuple(e.line[1])] for e in site.exits if e.line]
    if not exits:
        raise ValueError(f"{floor_id}: 출구가 없어 경로를 만들 수 없습니다.")
    png = _map_png(site, floor_id, site_dir)
    if not png.is_file():
        raise ValueError(f"{floor_id}: 도면 이미지가 없습니다 — {png.name}")

    # 문을 연다. 우선순위:
    #  ① CAD 편집기가 floor_<층>.json 에 남긴 **뚫기/막기 도형**(정본)
    #  ② 없으면 사람이 ① 맵 설정에서 그린 **피난경로를 따라** 뚫기(우회 수단)
    #
    # 왜 필요한가: map.png 는 도면을 '그린 그림'이라 편집기의 개구부 뚫기가
    # 반영돼 있지 않다(carve_free 는 격자에만 적용). 문짝·스윙 호가 선으로 남아
    # **실제 문이 벽으로 잡힌다**. 실측(AI hub 3층, 관측 1,512개):
    # 사람이 걸어간 자리의 32.2%가 '벽', 38.2%가 '출구 도달 불가' 였다.
    # ②로 뚫으면 도달 불가가 0.1%(578→1개)로 떨어진다 — 사람이 그린 경로는
    # 실제 문을 지나도록 그어져 있기 때문이다.
    carve, blocks = _edits_from_floor_json(site_dir, floor_id, m_per_px)
    if not carve:
        carve = [r.points for r in site.routes if len(r.points) >= 2]
    df = build_distfield(str(png), exits, m_per_px,
                         carve_paths=carve or None, carve_w_m=2.0,
                         clearance_m=0.15, extra_walls=blocks or None)

    # 트랙별 **첫 투영 위치** — 엔진과 같은 투영기(CameraProjector)를 쓴다.
    # 좌표계가 조금이라도 다르면 경로가 엉뚱한 데서 출발한다.
    from system.config.schema import CameraConfig
    from system.spatial import CameraProjector
    cams = [CameraConfig.model_validate(c) for c in (meta.get("cameras") or [])]
    mw = site.map.w if site.map else None
    mh = site.map.h if site.map else None
    projs = {c.cam_id: CameraProjector(c, mw, mh) for c in cams if c.mapping is not None}

    first: dict[str, tuple[float, float]] = {}
    for cam_id, ts, tracks in recorder.iter_calls(db_path):
        pr = projs.get(cam_id)
        if pr is None:
            continue
        for tr in tracks:
            key = f"{cam_id}:{tr.local_track_id}"
            if key in first:
                continue
            p = pr.project(tr.foot_uv)
            # 헐 밖(valid_roi 밖) 투영은 좌표를 못 믿는다 — 경로 시드로 쓰지 않는다
            if p is not None and getattr(p, "in_bounds", True):
                first[key] = (p.x, p.y)

    routes, unreachable = [], 0
    for i, (key, (x, y)) in enumerate(first.items()):
        if len(routes) >= max_routes:
            break
        r = solve_path(df, x, y)
        if r is None:
            unreachable += 1
            continue
        routes.append({"id": f"auto-p{len(routes):03d}",
                       "name": f"개인경로 {len(routes) + 1}",
                       "points": r["points"],
                       "_dist_m": r["dist_m"], "_from": key})
    return {
        "floor_id": floor_id, "m_per_px": m_per_px,
        "routes": routes,
        "stats": {"seeds": len(first), "made": len(routes),
                  "unreachable": unreachable},
        "generated_from": Path(db_path).name,
    }


def save(db_path, data: dict) -> Path:
    p = routes_path(db_path)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def load(db_path) -> dict | None:
    p = routes_path(db_path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
