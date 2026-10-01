# -*- coding: utf-8 -*-
"""**v2 지표** — 좌측 2D 맵에 그려지는 **그 점들로** 4대 지표를 다시 낸다.

지금(v1)까지 영상 하단의 수치는 **원 관측**으로 계산한 값이다. 그런데 좌측 맵에
보이는 건 사람이 그린(또는 이어 붙인) 연속 궤적이라, 보는 것과 세는 것이 다르다.
끊긴 트랙·카메라 사각·붙박이 오검출이 수치에는 그대로 들어간다.

v2 는 **화면에 보이는 점**을 그대로 엔진에 먹인다. 세 가지가 달라진다:

1. **점 = 관측.** 10명이 끝까지 이어지므로 트랙 끊김으로 인한 손실이 없다.
2. **개인 경로.** 공통 경로 하나에 전원을 재는 대신, 재실자별 경로를 깔고
   각자 **자기 위치에서 가장 가까운 경로**에 묶는다(`route_bind`).
   공통 경로 기준이면 우회한 사람이 전부 '이탈'로 잡힌다.
3. **속도 임계(v_th) 를 낮춘다.** 그린 점은 실측보다 매끄러워 순간 속도가
   낮게 나온다 — 같은 0.5 m/s 기준이면 피난 개시가 늦게 잡힌다.

엔진은 카메라 px 를 받아 호모그래피로 맵에 투영한다. 그린 점은 이미 맵 좌표라
**항등 매핑 카메라 1대**를 만들어 그대로 통과시킨다.

    python tools/demo_metrics_v2.py --session <id> --floor <층> [--v-th 0.3]
    python tools/demo_metrics_v2.py --all            # 4건 v1/v2 비교표
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from system.config.schema import CameraConfig, Route, SiteConfig   # noqa: E402
from system.contracts import TrackedObject                          # noqa: E402
from system.evac import person_routes as pr                         # noqa: E402
from system.evac.route_solver import build_distfield, solve_path    # noqa: E402
from system.metrics import recorder                                 # noqa: E402
from system.metrics.engine import MetricsEngine                     # noqa: E402
from system.metrics.replay import run_replay                        # noqa: E402
from system.spatial import nearest_on_polyline                      # noqa: E402
from tools import demo_bottleneck as dbn                            # noqa: E402
from tools import demo_ghosts as dghost                             # noqa: E402
from tools import demo_obstacles as dobs                            # noqa: E402
from tools import demo_paths as dpaths                              # noqa: E402
from tools import demo_tracks as dtrk                               # noqa: E402
from tools.session_viz import find_session                          # noqa: E402

DEMOS = [
    ("sess-1789693149252", "floor4", "IDR", "01_IDR", "drawn"),
    ("sess-1789693292022", "floor4", "EPFI", "02_EPFI", "drawn"),
    ("sess-1789693478593", "floor4", "CBS", "03_CBS", "drawn"),
    ("sess-1789693709646", "floor5", "SEI", "04_SEI", "drawn"),
]
DOT_CAM = "dots"
V_TH_V2 = 0.30        # 그린 점 기준 피난개시 임계 (사이트 기본 0.5)


def load_dots(sid: str, floor: str, tracks: str, m_per_px: float, t_end: float):
    """좌측 맵에 그려지는 점 — demo_viz 와 **같은 것**을 쓴다."""
    obstacles = dobs.load(floor)
    if tracks == "drawn":
        d = dpaths.load(sid, t_end=t_end, obstacles=obstacles, m_per_px=m_per_px)
    else:
        d = dtrk.load(sid)
    if d is None:
        raise SystemExit(f"{sid}: 그린 동선이 없습니다")
    return d


def identity_cam(site: SiteConfig, floor: str) -> CameraConfig:
    """맵 좌표를 그대로 통과시키는 카메라. 그린 점은 이미 맵 px 다."""
    w = float(site.map.w or 4000)
    h = float(site.map.h or 4000)
    pts = [[0.0, 0.0], [w, 0.0], [w, h], [0.0, h]]
    return CameraConfig.model_validate({
        "cam_id": DOT_CAM, "name": "그린 점", "rtsp": "none://dots",
        "floor_id": floor, "analyze_fps": 5.0,
        "mapping": {"cctv_pts": pts, "map_pts": pts,
                    "H": [1, 0, 0, 0, 1, 0, 0, 0, 1]},
        "min_conf": 0.0, "min_box_h": 0.0,
    })


def solve_from_dots(db, site, floor, dots, obstacles, m_per_px):
    """그린 점 **10명의 출발 자리**에서 각각 출구까지 — 사람당 경로 1개.

    "개별 경로 산출하고 가장 가까운 개별 경로로 잰다"를 그대로 따른다. 경로를
    사람 수만큼 깔고, 각자 자기 경로에 묶는다(`route_bind`).
    """
    site_dir = ROOT / "data/sites/default"
    exits = [[tuple(e.line[0]), tuple(e.line[1])] for e in site.exits if e.line]
    png = site_dir / ("map.png" if floor == "default" else f"map_{floor}.png")
    carve, blocks, hard = pr._edits_from_floor_json(site_dir, floor, m_per_px)
    if not carve:
        carve = [r.points for r in site.routes if len(r.points) >= 2]
    df = build_distfield(str(png), exits, m_per_px,
                         carve_paths=carve or None, carve_w_m=2.0,
                         clearance_m=0.15, extra_walls=blocks or None,
                         hard_walls=([list(o["points"]) for o in obstacles]
                                     + [list(w) for w in hard]) or None)
    out = []
    for i, p in enumerate(dots["people"]):
        x, y = p["xy"][0]
        r = solve_path(df, float(x), float(y))
        if r is None:
            continue
        out.append({"id": f"dot-p{i + 1:02d}", "name": f"개인경로 {i + 1}",
                    "points": r["points"], "_dot": p["id"]})
    return {"routes": out}


def run_v2(sid: str, floor: str, tracks: str = "drawn", v_th: float = V_TH_V2,
           fps: float = 5.0, verbose: bool = True, route_mode: str = "all",
           drop_exits=None, drop_bn=None):
    db, fl = find_session(sid, floor)
    meta = recorder.load_meta(db)
    site = SiteConfig.model_validate(meta["site_view"])
    m_per_px = site.map.resolve_m_per_px()
    t0 = float(meta["alarm_ts"])

    # 영상 길이 — v1 리플레이와 같은 구간을 본다
    ghosts = [g["key"] for g in dghost.load(sid)]
    v1_res, v1_tl, _, _ = run_replay(db, {"exclude_tracks": ghosts} if ghosts else {},
                                     fps=fps)
    t_end = max((getattr(p, "ts", 0.0) for p in v1_tl), default=t0 + 60.0) - t0
    dots = load_dots(sid, fl, tracks, m_per_px, t_end + 1.0)

    # 개인 경로 — **그 점이 실제로 출발한 자리**에서 푼다. 녹화 트랙의 첫 투영
    # 위치로 푼 경로(사이드카)를 쓰면 그린 동선과 출발점이 달라 준수도가 무너진다
    # (실측 12 SEI: EPFI 60.9 → 23.6. 계단 안에서 시작한 경로였다).
    obstacles = dobs.load(fl)
    dbn.load(fl)        # 병목 편집본(:8940)
    # "그 출구가 없었다면" — 출구·병목을 빼고 **그 가정대로** 경로까지 다시 푼다.
    drop_e = set(drop_exits or ())
    drop_b = set(drop_bn or ())
    if drop_e or drop_b:
        sv0 = site.model_dump()
        if drop_e:
            sv0["exits"] = [e for e in (sv0.get("exits") or [])
                            if e["id"] not in drop_e]
        if drop_b:
            sv0["bottlenecks"] = [b for b in (sv0.get("bottlenecks") or [])
                                  if b["id"] not in drop_b]
        site = SiteConfig.model_validate(sv0)
        if verbose:
            print(f"[v2] 없앤 것 — 출구 {sorted(drop_e) or '—'} · "
                  f"병목 {sorted(drop_b) or '—'}")
    if route_mode == "dots":
        routes = solve_from_dots(db, site, fl, dots, obstacles, m_per_px)
    else:
        # 재실자별 경로 **전부**를 깔고, 각자 자기 궤적에 가장 잘 맞는 것에 묶는다.
        # 점의 출발 자리에서만 푼 10개로 좁히면 그 최단 경로가 실제 걸은 길과
        # 달라 이탈이 통째로 잡힌다(실측 03 CBS: EPFI 78.5 → 15.1).
        if drop_e or drop_b:
            routes = pr.generate(db, ROOT / "data/sites/default",
                                 extra_walls=[o["points"] for o in obstacles] or None,
                                 site_override=site)      # 캐시 쓰지 않는다
        else:
            routes = pr.load(db)
            if routes is None or routes.get("algo_ver") != pr.ALGO_VER:
                routes = pr.generate(db, ROOT / "data/sites/default",
                                     extra_walls=[o["points"] for o in obstacles] or None)
                routes["_obstacles_sig"] = dobs.signature(obstacles)
                pr.save(db, routes)

    # 사이트를 v2 로 바꿔 끼운다 — 개인 경로 + 낮춘 임계 + 그 카메라 1대
    sv = site.model_dump()
    sv["routes"] = [{"id": r["id"], "name": r.get("name", ""),
                     "points": r["points"]} for r in routes["routes"]]
    sv["thresholds"] = {**sv.get("thresholds", {}), "v_th": float(v_th)}
    # 병목(밀집구역)을 표출과 **같은 크기**로 키운다. 화면에는 키워 그리고
    # 계산은 원래 크기로 두면 "저기 저렇게 모였는데 왜 0인가"가 된다.
    for b in (sv.get("bottlenecks") or []):
        dbn.scale_shape(b)
    # 출구는 **맵 통과선**으로만 센다. 화면 게이트(cam_line·cam_zone)가 걸려
    # 있으면 엔진이 "그 카메라가 이미 셌다"고 보고 맵 좌표 판정을 건너뛴다 —
    # 그런데 v2 엔진에는 그 카메라가 없으니 아무도 세지 않는다(출구 0명, SEI 없음).
    for e in sv.get("exits") or []:
        for k in ("count_cam", "cam_line", "cam_inside", "cam_zone"):
            e[k] = None
    # 통과선의 '안쪽'을 **사람이 있던 쪽**으로 잡는다. 맵 통과선의 inside 가
    # 반대로 잡혀 있으면 나가는 사람이 전부 '진입'으로 세어진다(12 SEI 의
    # exit-1 이 그랬다 — 10명이 나갔는데 out 0건, SEI 산출 불가).
    # v1 은 화면 통과선(cam_line)으로 세서 이 문제가 드러나지 않았다.
    hub = [0.0, 0.0]
    for p in dots["people"]:
        hub[0] += p["xy"][0][0] / len(dots["people"])
        hub[1] += p["xy"][0][1] / len(dots["people"])
    for e in sv.get("exits") or []:
        if e.get("line"):
            e["inside"] = [round(hub[0], 1), round(hub[1], 1)]
    site2 = SiteConfig.model_validate(sv)
    cam = identity_cam(site2, fl)

    eng = MetricsEngine(site2, [cam])
    # 각 점을 개인 경로에 묶는다 — **궤적 전체**로 맞춘다. 시작 자리만 보면
    # 바로 옆에서 다른 방향으로 간 사람의 경로가 더 가까워 그쪽에 붙는다
    # (실측: 시작점 최근접으로 묶었더니 EPFI 78.0 → 15.1 로 무너졌다).
    by_dot = {r["_dot"]: r["id"] for r in routes["routes"] if r.get("_dot")}
    bind = {}
    for i, p in enumerate(dots["people"]):
        rid = by_dot.get(p["id"])
        if rid is None:                       # 자기 경로가 안 풀린 사람만 최근접
            samp = p["xy"][::max(1, len(p["xy"]) // 25)]
            rid = min(site2.routes,
                      key=lambda r: sum(nearest_on_polyline((x, y), r.points).dist_px
                                        for x, y in samp)).id
        bind[f"{DOT_CAM}:{i + 1}"] = rid
    eng._route_bind = bind
    origins = [tuple(o) for o in (meta.get("alarm_origins") or [])] or None
    eng.start_session(t_alarm=t0, alarm_origins=origins)

    dt = 1.0 / max(0.5, fps)
    # **문을 넘는 그 순간**을 격자에 꼭 넣는다. 5fps 격자와 궤적의 0.1초 격자가
    # 안 맞아 마지막 점이 반올림에서 빠지면, 출구선을 넘은 유일한 점을 못 보고
    # 통과가 안 세어진다(실측 10명 중 2명만 잡혔다).
    grid = [i * dt for i in range(int(t_end / dt) + 2) if i * dt <= t_end + 1e-6]
    for p in dots["people"]:
        if p.get("exit_at") is not None:
            grid.append(float(p["exit_at"]) - 1e-4)
        grid.append(p["t0"] + (len(p["xy"]) - 1) * dots["dt"] - 1e-4)
    grid = sorted(set(round(v, 4) for v in grid if 0 <= v <= t_end + 1.0))
    n_pts = 0
    for t in grid:
        objs = dtrk.at(dots, t)
        tracks_now = []
        for o in objs:
            idx = next((i for i, p in enumerate(dots["people"])
                        if p["id"] == o["gid"]), None)
            if idx is None:
                continue
            tracks_now.append(TrackedObject(
                cam_id=DOT_CAM, local_track_id=idx + 1,
                foot_uv=(float(o["x"]), float(o["y"])),
                bbox_xyxy=(float(o["x"]) - 5, float(o["y"]) - 20,
                           float(o["x"]) + 5, float(o["y"])),
                conf=0.99, ts=t0 + t))
        if tracks_now:
            eng.on_tracks(DOT_CAM, t0 + t, tracks_now)
            n_pts += len(tracks_now)

    res2 = eng.stop_session()
    tl2 = eng.session_timeline()
    d1 = v1_res if isinstance(v1_res, dict) else v1_res.model_dump()
    d2 = res2 if isinstance(res2, dict) else res2.model_dump()
    if verbose:
        print(f"[v2] {sid} · 점 {dots['n']}명 · 표본 {n_pts}개 · "
              f"개인경로 {len(site2.routes)}개 · v_th {v_th}")
    return d1, d2, dots, site2, tl2


def _idr(d):
    zs = [z for z in (d.get("zone_metrics") or []) if z.get("idr") is not None]
    return (sum(z["idr"] for z in zs) / len(zs)) if zs else None


def _started(d):
    zs = d.get("zone_metrics") or []
    return sum(1 for z in zs if z.get("evacuation_start_at") is not None), len(zs)


def table(rows):
    hdr = ("지표", "v1 (원 관측)", "v2 (그린 점)", "차이")
    w = [max(len(str(r[i])) for r in [hdr] + rows) for i in range(4)]
    line = "  ".join("-" * x for x in w)
    print("  ".join(str(h).ljust(x) for h, x in zip(hdr, w)))
    print(line)
    for r in rows:
        print("  ".join(str(c).ljust(x) for c, x in zip(r, w)))


def main() -> int:
    ap = argparse.ArgumentParser(description="v2 지표 — 그린 점 기준")
    ap.add_argument("--session")
    ap.add_argument("--floor")
    ap.add_argument("--tracks", default="drawn", choices=("drawn", "ideal"))
    ap.add_argument("--v-th", type=float, default=V_TH_V2)
    ap.add_argument("--route-mode", default="all", choices=("all", "dots"),
                    help="all=재실자별 경로 전부에서 최근접 · dots=점 출발자리에서만 산출")
    ap.add_argument("--drop-exits", default="",
                    help="없앤 셈 칠 출구 id (콤마). 예: exit-0 — 아무도 안 쓴 "
                         "가까운 출구를 빼면 SEI 가 실제 쓰임새를 반영한다")
    ap.add_argument("--drop-bn", default="", help="없앤 셈 칠 병목 id (콤마)")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()

    targets = DEMOS if a.all else [(a.session, a.floor, "", "", a.tracks)]
    if not a.all and not a.session:
        ap.error("--session 또는 --all")

    for sid, fl, metric, tag, tracks in targets:
        d1, d2, dots, site2, _tl = run_v2(
            sid, fl, tracks, a.v_th, route_mode=a.route_mode,
            drop_exits=[x for x in a.drop_exits.split(",") if x.strip()],
            drop_bn=[x for x in a.drop_bn.split(",") if x.strip()])
        print(f"\n■ {tag or sid} ({metric or '—'})")
        rows = []
        for key, name, fmt in (("epfi_avg", "EPFI", "{:.1f}"),
                               ("sei", "SEI", "{:.1f}"),
                               ("cbs_total", "CBS", "{:.2f}")):
            v1, v2 = d1.get(key), d2.get(key)
            f = lambda v: "—" if v is None else fmt.format(v)
            df = "—" if (v1 is None or v2 is None) else fmt.format(v2 - v1)
            rows.append((name, f(v1), f(v2), df))
        i1, i2 = _idr(d1), _idr(d2)
        rows.append(("IDR(m/s)", "—" if i1 is None else f"{i1:.2f}",
                     "—" if i2 is None else f"{i2:.2f}",
                     "—" if (i1 is None or i2 is None) else f"{i2 - i1:+.2f}"))
        s1, n1 = _started(d1); s2, n2 = _started(d2)
        rows.append(("개시 구역", f"{s1}/{n1}", f"{s2}/{n2}", f"{s2 - s1:+d}"))
        table(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
