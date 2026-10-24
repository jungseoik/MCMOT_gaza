#!/usr/bin/env python
"""출구 선(line) 카운트 최적화 하네스 — CJ 실훈련(drill-1022) 근사 정답 대비.

문제: 글로벌 ID 가 꺼져 있으면 같은 문을 두 카메라가 보면 한 사람이 카메라마다 한 번씩
세진다(16층 e2: cam22 37 + cam26 42 = 79, 정답 61). 이 도구는 **녹화본을 결정적으로
재생**해 선 기하·카메라 자격·카메라 간 중복 제거 규칙의 조합을 정답(answers.json)에 채점한다.

    python tools/exit_line_tune.py collect            # 재생 → 통과 이벤트 캐시 (전체 ~15초)
    python tools/exit_line_tune.py events [--exit floor8/e2] [--list]  # 카메라별 통과 이벤트
    python tools/exit_line_tune.py grid [--family]    # 규칙 조합 그리드(8만여 개) → MAE 순위표
    python tools/exit_line_tune.py one '{"s":3,"o":0,"x":2,"dedup_dt":2,"one2one":true}'
    python tools/exit_line_tune.py loo [--family]     # 출구 하나 빼고 고른 설정으로 빠진 출구 채점
    python tools/exit_line_tune.py null -s 3          # 융합이 '같은 사람'을 지우나 — 시간 이동 우연 기준선
    python tools/exit_line_tune.py verify --pad-m 1 --fuse 2   # 제안 엔진 변경을 온라인 순서 그대로 재생
    python tools/exit_line_tune.py cbs [-r 0.8] [--fresh 0.5]  # 병목 밀도(CBS)의 카메라 중복·잔상 영향

캐시 위치: 환경변수 EXIT_TUNE_CACHE (기본 $TMPDIR/exit_line_tune). 결과 정리:
media/vsource/cj/drill-1022/docs/선카운트-최적화.md

collect 는 한 번의 재생에 **가상 출구 여러 개**(원래 선을 길이 ×s·안쪽 오프셋 o m 로 변형)를
같이 넣어 센다 — 카운터는 출구별로 독립이라 서로 간섭하지 않는다. 출구·병목 외의 설정,
원본 세션 저장물, site.json 은 건드리지 않는다(읽기 전용).
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SITE = ROOT / "data/sites/default"
ANS = ROOT / "media/vsource/cj/drill-1022/answers.json"
SESS = {"floor8": "sess-1792779535311", "floor7": "sess-1792776296361"}
CACHE = Path(os.environ.get("EXIT_TUNE_CACHE", Path(tempfile.gettempdir()) / "exit_line_tune"))

SCALES = (1.0, 1.5, 2.0, 3.0, 4.0)        # 선 길이 배율 (중심 고정, 선 방향으로 늘림)
OFFSETS = (0.0, 0.5, 1.0, 1.5)            # 안쪽(복도)으로 평행이동 (m)
EXTRAPS = (2.0, 1.0, 0.0, 3.0, 4.0)           # thresholds.exit_extrap_m — 헐 밖 외삽 허용 (m)


# ---------------------------------------------------------------- 기하
def _variant_line(ex: dict, scale: float, off_m: float, mpp: float):
    (ax, ay), (bx, by) = ex["line"]
    ix, iy = ex["inside"]
    cx, cy = (ax + bx) / 2, (ay + by) / 2
    dx, dy = (bx - ax) / 2, (by - ay) / 2
    L = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / L, dx / L                       # 법선 — 안쪽(inside) 방향으로 맞춘다
    if (ix - cx) * nx + (iy - cy) * ny < 0:
        nx, ny = -nx, -ny
    o = off_m / mpp
    cx, cy = cx + nx * o, cy + ny * o
    line = [[cx - dx * scale, cy - dy * scale], [cx + dx * scale, cy + dy * scale]]
    inside = [cx + nx * 10.0, cy + ny * 10.0]      # 새 선 기준 안쪽 점
    return line, inside, (nx, ny)


def _vid(eid: str, s: float, o: float) -> str:
    return f"{eid}|s{s:g}|o{o:g}"


# ---------------------------------------------------------------- 수집
def _replay_collect(fid: str, extrap: float) -> dict:
    from system.config.schema import CameraConfig, SiteConfig
    from system.metrics import recorder
    from system.metrics import engine as E

    db = SITE / "sessions" / fid / f"{SESS[fid]}.db"
    meta = recorder.load_meta(str(db))
    sv = copy.deepcopy(meta["site_view"])
    site = json.loads((SITE / "site.json").read_text(encoding="utf-8"))
    fl = next(f for f in site["floors"] if f["id"] == fid)
    mpp = float(sv["map"]["m_per_px"])
    exits, geo = [], {}
    for ex in fl["exits"]:
        for s, o in itertools.product(SCALES, OFFSETS):
            line, inside, n = _variant_line(ex, s, o, mpp)
            v = copy.deepcopy(ex)
            v.update(id=_vid(ex["id"], s, o), line=line, inside=inside,
                     count_cam=None, cam_line=None, cam_inside=None, cam_zone=None)
            exits.append(v)
            geo[v["id"]] = {"line": line, "n": n}
    sv["exits"], sv["bottlenecks"] = exits, fl["bottlenecks"]
    sv.setdefault("thresholds", {})["exit_extrap_m"] = extrap
    eng = E.MetricsEngine(SiteConfig.model_validate(sv),
                          [CameraConfig.model_validate(c) for c in meta.get("cameras", [])])
    t0 = float(meta["alarm_ts"])
    eng.start_session(t_alarm=t0,
                      alarm_origins=[tuple(o) for o in meta.get("alarm_origins") or []] or None)

    events: list[dict] = []
    ctx = {"extrap": False, "ts": 0.0, "h": 0.0}
    prev_pt: dict = {}                                # (vid, line_key) -> 직전 관측점
    orig_obs = E._ExitCounter.observe
    orig_ext = E.MetricsEngine._observe_exits_extrap
    id_of = {}

    def obs(self, line_key, count_key, pt):
        vid = id_of.get(id(self))
        if vid is None:
            vid = id_of[id(self)] = next(k for k, v in eng._exits.items() if v is self)
        before = self.out_count
        r = orig_obs(self, line_key, count_key, pt)
        pp = prev_pt.get((vid, line_key))
        prev_pt[(vid, line_key)] = pt
        if self.out_count > before:
            events.append({"x": vid, "k": line_key, "cam": line_key.split(":")[0].replace("rh_cj1022_", ""),
                           "t": round(ctx["ts"] - t0, 3), "pt": [round(pt[0], 2), round(pt[1], 2)],
                           "pp": [round(pp[0], 2), round(pp[1], 2)] if pp else None,
                           "ext": ctx["extrap"], "h": round(ctx["h"], 1)})
        return r

    def ext(self, cam_id, line_key, count_key, ts, proj, foot_uv):
        ctx["extrap"] = True
        try:
            return orig_ext(self, cam_id, line_key, count_key, ts, proj, foot_uv)
        finally:
            ctx["extrap"] = False

    E._ExitCounter.observe = obs
    E.MetricsEngine._observe_exits_extrap = ext
    try:
        for cam_id, ts, tracks in recorder.iter_calls(str(db)):
            if ts - t0 > 1200:
                break
            ctx["ts"] = ts
            ctx["h"] = max((t.bbox_xyxy[3] - t.bbox_xyxy[1] for t in tracks), default=0.0)
            eng.on_tracks(cam_id, ts, tracks)
    finally:
        E._ExitCounter.observe = orig_obs
        E.MetricsEngine._observe_exits_extrap = orig_ext
    # 카메라별 헐(맵 px) — 기하 자격 규칙용
    hulls = {cid.replace("rh_cj1022_", ""): p.roi_map_polygon() for cid, p in eng._projectors.items()}
    return {"floor": fid, "extrap": extrap, "mpp": mpp, "t0": t0, "events": events,
            "geo": geo, "hulls": hulls,
            "orig": {e["id"]: {"line": e["line"], "inside": e["inside"]} for e in fl["exits"]}}


def cmd_collect(a) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    for fid in SESS:
        for x in (a.extrap or EXTRAPS):
            d = _replay_collect(fid, x)
            p = CACHE / f"{fid}_x{x:g}.json"
            p.write_text(json.dumps(d))
            print(f"{fid} extrap {x:g} m → 이벤트 {len(d['events'])}개 → {p}")


def _load(fid: str, x: float) -> dict:
    """캐시 로드 + 채점용 전처리(출구별 이벤트 색인·선 축 위치·카메라 헐-문 거리)."""
    d = json.loads((CACHE / f"{fid}_x{x:g}.json").read_text())
    idx = defaultdict(list)
    for e in d["events"]:
        eid = e["x"].split("|")[0]
        e["a"] = _along_m(d, eid, e["pt"])
        idx[e["x"]].append(e)
    for v in idx.values():
        v.sort(key=lambda e: e["t"])
    d["idx"] = idx
    d["cdist"] = {(eid, c): _cam_line_dist_m(d, eid, c) for eid in d["orig"] for c in d["hulls"]}
    return d


def _gt() -> dict:
    ans = json.loads(ANS.read_text(encoding="utf-8"))
    return {g["floor"]: (float(g["floor_end_sec"]), {k: v["count"] for k, v in g["by_exit"].items()})
            for g in ans["floors"]}


# ---------------------------------------------------------------- 규칙
def _pdist_poly(x, y, poly) -> float:
    """점→다각형 거리 (안이면 0)."""
    from system.spatial.geometry import point_in_polygon
    if point_in_polygon((x, y), poly):
        return 0.0
    best = 1e18
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]; bx, by = poly[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy or 1.0
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L2))
        best = min(best, math.hypot(x - ax - t * dx, y - ay - t * dy))
    return best


def _cam_line_dist_m(d: dict, eid: str, cam: str) -> float:
    """카메라 헐(맵)에서 원래 출구 선 중점까지 거리 (m) — 0 이면 헐이 문을 덮는다."""
    (ax, ay), (bx, by) = d["orig"][eid]["line"]
    return _pdist_poly((ax + bx) / 2, (ay + by) / 2, d["hulls"][cam]) * d["mpp"]


def _along_m(d: dict, eid: str, pt) -> float:
    """원래 선 축으로 사영한 위치 (m, 중심=0)."""
    (ax, ay), (bx, by) = d["orig"][eid]["line"]
    cx, cy = (ax + bx) / 2, (ay + by) / 2
    L = math.hypot(bx - ax, by - ay) or 1.0
    return ((pt[0] - cx) * (bx - ax) + (pt[1] - cy) * (by - ay)) / L * d["mpp"]


def count_exit(d: dict, eid: str, cfg: dict, cutoff: float) -> tuple[int, dict]:
    """한 출구의 카운트 — cfg: s·o(선 변형), elig_m(헐-문 거리 자격, None=전부),
    no_ext(헐 밖 외삽 이벤트 버림), dedup_dt·dedup_d(카메라 간 중복 제거), prio(문 카메라 우선)."""
    vid = _vid(eid, cfg["s"], cfg["o"])
    ev = [e for e in d["idx"].get(vid, ()) if e["t"] <= cutoff]
    if cfg.get("no_ext"):
        ev = [e for e in ev if not e["ext"]]
    dist = {e["cam"]: d["cdist"][(eid, e["cam"])] for e in ev}
    if cfg.get("elig_m") is not None:
        ev = [e for e in ev if dist[e["cam"]] <= cfg["elig_m"]]
    ev.sort(key=lambda e: e["t"])
    dt, dd = cfg.get("dedup_dt"), cfg.get("dedup_d")
    kept: list[dict] = []
    if dt:
        ev = [dict(e) for e in ev]
        # 시간순(온라인과 같음)으로 받되, 다른 카메라가 Δt(·Δd) 안에 이미 센 이벤트가 있으면
        # 같은 사람으로 보고 버린다. prio 면 헐이 문에 가까운 카메라 이벤트를 먼저 받는다(오프라인 전용).
        order = sorted(ev, key=lambda e: (dist[e["cam"]], e["t"])) if cfg.get("prio") else ev
        for e in order:
            best = None
            for k in kept:
                if k["cam"] == e["cam"] or abs(k["t"] - e["t"]) > dt:
                    continue
                if dd is not None and abs(k["a"] - e["a"]) > dd:
                    continue
                # 1:1 — 대표 이벤트 하나는 **카메라마다 최대 한 번**만 다른 이벤트를 흡수한다
                # (한 사람은 한 카메라에서 한 번만 넘는다). 끄면 군중 속 여러 명을 한 명이 삼킨다.
                if cfg.get("one2one") and e["cam"] in k.setdefault("_m", set()):
                    continue
                if best is None or abs(k["t"] - e["t"]) < abs(best["t"] - e["t"]):
                    best = k                     # 시간상 가장 가까운 짝
            if best is None:
                kept.append(e)
            elif cfg.get("one2one"):
                best["_m"].add(e["cam"])
    else:
        kept = ev
    per = defaultdict(int)
    for e in kept:
        per[e["cam"]] += 1
    return len(kept), dict(per)


def evaluate(cfg: dict, data: dict, gt: dict, skip: tuple | None = None):
    rows, errs = {}, []
    for fid, (cut, by) in gt.items():
        d = data[fid]
        for eid, g in by.items():
            n, per = count_exit(d, eid, cfg, cut)
            rows[f"{fid}/{eid}"] = (n, g, per)
            if skip != (fid, eid):
                errs.append(abs(n - g))
    return rows, (sum(errs) / len(errs) if errs else 0.0)


def _grid():
    base = []
    for s, o in itertools.product(SCALES, OFFSETS):
        for elig in (None, 0.0, 1.0, 2.0, 3.0):
            for no_ext in (False, True):
                base.append(dict(s=s, o=o, elig_m=elig, no_ext=no_ext))
    out = []
    for b in base:
        out.append(dict(b))
        for dt in (0.5, 1.0, 1.5, 2.0, 3.0):
            for dd in (None, 0.3, 0.5, 0.75, 1.0):
                for prio in (False, True):
                    for o2o in (False, True):
                        out.append(dict(b, dedup_dt=dt, dedup_d=dd, prio=prio, one2one=o2o))
    return out


def _fmt(cfg: dict) -> str:
    s = f"x{cfg.get('x', 2):g} s{cfg['s']:g} o{cfg['o']:g}"
    if cfg.get("elig_m") is not None:
        s += f" elig≤{cfg['elig_m']:g}m"
    if cfg.get("no_ext"):
        s += " 헐안만"
    if cfg.get("dedup_dt"):
        s += f" dedup Δt{cfg['dedup_dt']:g}"
        if cfg.get("dedup_d") is not None:
            s += f"/Δd{cfg['dedup_d']:g}"
        if cfg.get("prio"):
            s += " prio"
        if cfg.get("one2one"):
            s += " 1:1"
    return s


def _all_data():
    out = {}
    for x in EXTRAPS:
        try:
            out[x] = {fid: _load(fid, x) for fid in SESS}
        except FileNotFoundError:
            pass
    return out


def _family(cfg: dict) -> bool:
    """권장 계열 — 전역 파라미터 2개(선 연장 s · 융합 Δt)만 움직이는 단순 규칙."""
    return (cfg["o"] == 0 and cfg.get("x", 2.0) == 2.0 and cfg.get("elig_m") is None
            and not cfg.get("no_ext") and cfg.get("dedup_d") is None and not cfg.get("prio")
            and (not cfg.get("dedup_dt") or cfg.get("one2one")))


def _run_grid(skip=None, family=False):
    gt = _gt()
    res = []
    for x, data in _all_data().items():
        for cfg in _grid():
            cfg = dict(cfg, x=x)
            if family and not _family(cfg):
                continue
            rows, mae = evaluate(cfg, data, gt, skip)
            res.append((mae, cfg, rows))
    res.sort(key=lambda r: (round(r[0], 4), _complexity(r[1])))
    return res


def _complexity(cfg: dict) -> int:
    return (int(cfg["s"] != 1) + int(cfg["o"] != 0) + int(cfg.get("elig_m") is not None)
            + int(bool(cfg.get("no_ext"))) + 2 * int(bool(cfg.get("dedup_dt")))
            + int(cfg.get("dedup_d") is not None) + int(bool(cfg.get("prio")))
            + int(bool(cfg.get("one2one"))) + int(cfg.get("x", 2) != 2))


def cmd_grid(a) -> None:
    res = _run_grid(family=a.family)
    keys = list(res[0][2])
    print(f"조합 {len(res)}개. 상위 {a.top} (동률은 단순한 것 먼저)")
    print("MAE  | " + " | ".join(keys) + " | 설정")
    for mae, cfg, rows in res[:a.top]:
        print(f"{mae:4.2f} | " + " | ".join(f"{rows[k][0]:3d}" for k in keys) + f" | {_fmt(cfg)}")
    if a.json:
        Path(a.json).write_text(json.dumps([[m, c, {k: list(v) for k, v in r.items()}] for m, c, r in res],
                                           ensure_ascii=False))


def cmd_one(a) -> None:
    """특정 설정 한 개 채점 — JSON 문자열로."""
    cfg = json.loads(a.cfg)
    data = _all_data()[float(cfg.get("x", 2.0))]
    rows, mae = evaluate(cfg, data, _gt())
    for k, (n, g, per) in rows.items():
        print(f"  {k}: {n:3d} (정답 {g}, {n - g:+d}) {per}")
    print(f"MAE {mae:.2f} — {_fmt(cfg)}")


def cmd_loo(a) -> None:
    """leave-one-exit-out: 3개 출구로 최적 설정을 고르고 빠진 출구 오차를 본다."""
    gt = _gt()
    tot = []
    for fid, (_c, by) in gt.items():
        for eid in by:
            res = _run_grid(skip=(fid, eid), family=a.family)
            mae, cfg, rows = res[0]
            n, g, _ = rows[f"{fid}/{eid}"]
            tot.append(abs(n - g))
            # 동률 상위 설정들 사이 빠진 출구 오차 분포
            ties = [r for r in res if abs(r[0] - mae) < 1e-9]
            held = sorted(abs(r[2][f"{fid}/{eid}"][0] - g) for r in ties)
            print(f"빼고: {fid}/{eid} | 3출구 MAE {mae:.2f} · 동률 {len(ties)}개 | 최단순 {_fmt(cfg)}"
                  f" → {n} (정답 {g}, 오차 {abs(n - g)}) | 동률들 빠진출구 오차 중앙 {held[len(held)//2]} 범위 {held[0]}~{held[-1]}")
    print(f"LOO 평균 오차(최단순 선택) {sum(tot)/len(tot):.2f}")


def cmd_events(a) -> None:
    gt = _gt()
    targets = [a.floor_exit] if a.floor_exit else [f"{f}/{e}" for f, (_c, by) in gt.items() for e in by]
    for fe in targets:
        fid, eid = fe.split("/")
        d = _load(fid, a.x)
        cut = gt[fid][0]
        vid = _vid(eid, a.s, a.o)
        ev = [e for e in d["events"] if e["x"] == vid and e["t"] <= cut]
        by = defaultdict(lambda: [0, 0])
        for e in ev:
            by[e["cam"]][int(e["ext"])] += 1
        print(f"{fe} (s{a.s:g} o{a.o:g} x{a.x:g}) 합 {len(ev)} 정답 {gt[fid][1][eid]}")
        for c, (hin, hext) in sorted(by.items()):
            print(f"   {c:6s} 헐안 {hin:3d} 외삽 {hext:3d} | 헐-문 거리 {_cam_line_dist_m(d, eid, c):5.2f} m")
        if a.list:
            for e in sorted(ev, key=lambda e: e["t"]):
                print(f"      t={e['t']:7.2f} {e['cam']:6s} {'ext' if e['ext'] else 'in '} along={_along_m(d, eid, e['pt']):+5.2f}m h={e['h']}")


def cmd_null(a) -> None:
    """중복 제거가 '같은 사람'을 지우는지 검증 — 카메라 간 시간차 분포 vs 우연 일치(시간 이동) 기준선.

    문 카메라(헐-문 거리 최소) 이벤트에 다른 카메라 이벤트를 1:1 로 짝짓되, 다른 카메라 시각을
    ±shift 초 밀어 같은 방식으로 짝지은 수를 '우연 일치'(군중 속 다른 사람)로 본다.
    """
    gt = _gt()
    shifts = [s for s in range(-60, 61, 5) if abs(s) >= 10]
    for fid, (cut, by) in gt.items():
        d = _load(fid, a.x)
        for eid in by:
            ev = [e for e in d["idx"].get(_vid(eid, a.s, a.o), ()) if e["t"] <= cut]
            cams = sorted({e["cam"] for e in ev}, key=lambda c: d["cdist"][(eid, c)])
            if len(cams) < 2:
                continue
            door = cams[0]
            A = sorted(e["t"] for e in ev if e["cam"] == door)
            for oc in cams[1:]:
                B = sorted(e["t"] for e in ev if e["cam"] == oc)

                def match(B2, dt):
                    used, n, dts = set(), 0, []
                    for b in B2:
                        best = None
                        for i, t in enumerate(A):
                            if i in used or abs(t - b) > dt:
                                continue
                            if best is None or abs(t - b) < abs(A[best] - b):
                                best = i
                        if best is not None:
                            used.add(best); n += 1; dts.append(b - A[best])
                    return n, dts
                cells = []
                for dt in (0.5, 1.0, 1.5, 2.0, 3.0):
                    n, _ = match(B, dt)
                    null = [match([b + s for b in B], dt)[0] for s in shifts]
                    cells.append(f"Δt{dt:g}: {n:2d} (우연 {sum(null)/len(null):4.1f})")
                _n, dts = match(B, 3.0)
                med = sorted(dts)[len(dts) // 2] if dts else float("nan")
                print(f"{fid}/{eid} 문={door}({len(A)}) vs {oc}({len(B)}) | " + " | ".join(cells)
                      + f" | 짝 시간차 중앙 {med:+.2f}s")


def cmd_cbs(a) -> None:
    """병목 밀도(CBS)도 같은 중복을 겪는지 — 1초 샘플마다 병목 안 객체를 세는 세 방식 비교.

    raw   지금 엔진: _objects 전부(카메라 로컬 키 — 두 카메라가 보면 2명, 소실 후 3초 잔상 포함)
    fresh 최근 --fresh 초(기본 0.5) 안에 관측된 객체만 (잔상 제거)
    fused fresh 에서 **다른 카메라** 점끼리 r m 안이면 한 명으로 (같은 카메라 점은 안 합친다)
    """
    from system.config.schema import CameraConfig, SiteConfig
    from system.metrics import recorder
    from system.metrics import session as S
    from system.metrics.engine import MetricsEngine
    from system.spatial.geometry import point_in_polygon
    gt = _gt()
    for fid, (cut, _by) in gt.items():
        db = SITE / "sessions" / fid / f"{SESS[fid]}.db"
        meta = recorder.load_meta(str(db))
        sv = copy.deepcopy(meta["site_view"])
        site = json.loads((SITE / "site.json").read_text(encoding="utf-8"))
        fl = next(f for f in site["floors"] if f["id"] == fid)
        sv["exits"], sv["bottlenecks"] = fl["exits"], fl["bottlenecks"]
        mpp = float(sv["map"]["m_per_px"])
        eng = MetricsEngine(SiteConfig.model_validate(sv),
                            [CameraConfig.model_validate(c) for c in meta.get("cameras", [])])
        t0 = float(meta["alarm_ts"])
        eng.start_session(t_alarm=t0,
                          alarm_origins=[tuple(o) for o in meta.get("alarm_origins") or []] or None)
        samples = []                               # (t, {bid: (raw, fresh, fused)})
        orig = S.EvaluationSession._bn_densities

        def dens(self):
            now = self.last_sample_ts
            objs = [(st.cam_id, st.hist[-1][1], st.hist[-1][2], st.last_ts)
                    for st in self._eng._objects.values() if st.hist]
            row = {}
            for b, area in self._bn_cfg:
                ins = [o for o in objs if point_in_polygon((o[1], o[2]), b.polygon)]
                fr = [o for o in ins if now - o[3] <= a.fresh + 1e-6]
                used, n_f = set(), 0
                for i, o in enumerate(fr):          # 다른 카메라 점과 r 안이면 한 명
                    if i in used:
                        continue
                    n_f += 1
                    cams = {o[0]}
                    for j in range(i + 1, len(fr)):
                        q = fr[j]
                        if j in used or q[0] in cams:
                            continue
                        if math.hypot(q[1] - o[1], q[2] - o[2]) * mpp <= a.r:
                            used.add(j); cams.add(q[0])
                row[b.id] = (len(ins), len(fr), n_f, area)
            samples.append((now - t0, row))
            return orig(self)

        S.EvaluationSession._bn_densities = dens
        try:
            for cam_id, ts, tracks in recorder.iter_calls(str(db)):
                if ts - t0 > cut + 2:
                    break
                eng.on_tracks(cam_id, ts, tracks)
        finally:
            S.EvaluationSession._bn_densities = orig
        rc = {b["id"]: b.get("rho_crit", 2.0) for b in fl["bottlenecks"]}
        print(f"== {fid} (≤{cut:.0f}s, r={a.r} m)")
        for bid in rc:
            seq = [(t, r[bid]) for t, r in samples if bid in r and t <= cut]
            area = seq[0][1][3]
            cbs = [0.0, 0.0, 0.0]; peak = [0, 0, 0]; occ = [0, 0, 0]
            for k in range(len(seq) - 1):
                dt = seq[k + 1][0] - seq[k][0]
                for m in range(3):
                    n = seq[k][1][m]
                    peak[m] = max(peak[m], n)
                    occ[m] += n * dt
                    cbs[m] += max(0.0, n / area - rc[bid]) * dt
            print(f"   {bid} 면적 {area:4.1f}m² ρcrit {rc[bid]} | CBS raw {cbs[0]:6.1f} · fresh {cbs[1]:6.1f} · fused {cbs[2]:6.1f}"
                  f" | 최대 인원 {peak[0]}/{peak[1]}/{peak[2]} | 인원·초 {occ[0]:.0f}/{occ[1]:.0f}/{occ[2]:.0f}")


def cmd_verify(a) -> None:
    """권장 엔진 변경을 **온라인 방식 그대로** 흉내 내 재생 — 오프라인 그리드 결과와 맞는지 검증.

    선은 그리지 않고(폭 W_eff 보존) 판정 여유만 늘린다: DirectionalLine.seg_pad = 0.06 + pad_m / 선길이_m.
    융합은 도착 순서대로: 다른 카메라가 fuse 초 안에 이미 센 OUT 이 있고 그 대표가 아직 이 카메라
    것을 흡수하지 않았으면 이번 OUT 은 같은 사람으로 보고 세지 않는다(가장 가까운 시각의 대표).
    """
    from system.config.schema import CameraConfig, SiteConfig
    from system.metrics import recorder
    from system.metrics import engine as E
    gt = _gt()
    errs = []
    for fid, (cut, by) in gt.items():
        db = SITE / "sessions" / fid / f"{SESS[fid]}.db"
        meta = recorder.load_meta(str(db))
        sv = copy.deepcopy(meta["site_view"])
        site = json.loads((SITE / "site.json").read_text(encoding="utf-8"))
        fl = next(f for f in site["floors"] if f["id"] == fid)
        sv["exits"], sv["bottlenecks"] = fl["exits"], fl["bottlenecks"]
        sv["thresholds"]["exit_extrap_m"] = a.extrap
        mpp = float(sv["map"]["m_per_px"])
        eng = E.MetricsEngine(SiteConfig.model_validate(sv),
                              [CameraConfig.model_validate(c) for c in meta.get("cameras", [])])
        t0 = float(meta["alarm_ts"])
        eng.start_session(t_alarm=t0,
                          alarm_origins=[tuple(o) for o in meta.get("alarm_origins") or []] or None)
        for ec in eng._exits.values():                       # 판정 여유 (선 길이·W_eff 불변)
            ec.line.seg_pad = 0.06 + a.pad_m / (ec.line.L * mpp)
        now = {"ts": 0.0}
        recent = {eid: [] for eid in eng._exits}             # [ts, cam, 흡수한 카메라 set]
        ids = {id(ec): eid for eid, ec in eng._exits.items()}
        orig = E._ExitCounter.observe

        def obs(self, line_key, count_key, pt):
            ev = self.line.observe(line_key, pt)
            if ev == "out" and count_key not in self.counted_out:
                self.counted_out.add(count_key)
                cam, ts = line_key.split(":")[0], now["ts"]
                if a.fuse > 0:
                    rq = recent[ids[id(self)]]
                    while rq and ts - rq[0][0] > a.fuse:
                        rq.pop(0)
                    cand = [r for r in rq if r[1] != cam and cam not in r[2]]
                    if cand:
                        min(cand, key=lambda r: abs(ts - r[0]))[2].add(cam)
                        return None                          # 같은 사람 — 세지 않음
                    rq.append([ts, cam, set()])
                self.out_count += 1
                return "out"
            if ev == "in" and count_key not in self.counted_in:
                self.counted_in.add(count_key)
                self.in_count += 1
                return "in"
            return None

        E._ExitCounter.observe = obs
        try:
            for cam_id, ts, tracks in recorder.iter_calls(str(db)):
                if ts - t0 > cut:
                    break
                now["ts"] = ts
                eng.on_tracks(cam_id, ts, tracks)
        finally:
            E._ExitCounter.observe = orig
        for eid, g in by.items():
            n = eng._exits[eid].out_count
            errs.append(abs(n - g))
            print(f"  {fid}/{eid}: {n:3d} (정답 {g}, {n - g:+d})")
    print(f"MAE {sum(errs)/len(errs):.2f} — pad {a.pad_m:g} m · fuse {a.fuse:g} s · extrap {a.extrap:g} m (온라인)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("collect"); p.add_argument("--extrap", type=float, nargs="*"); p.set_defaults(fn=cmd_collect)
    p = sub.add_parser("events"); p.add_argument("--exit", dest="floor_exit")
    p.add_argument("-s", type=float, default=1.0); p.add_argument("-o", type=float, default=0.0)
    p.add_argument("-x", type=float, default=2.0); p.add_argument("--list", action="store_true")
    p.set_defaults(fn=cmd_events)
    p = sub.add_parser("grid"); p.add_argument("--top", type=int, default=40); p.add_argument("--json")
    p.add_argument("--family", action="store_true", help="권장 계열(s·Δt 1:1 만)로 제한")
    p.set_defaults(fn=cmd_grid)
    p = sub.add_parser("one"); p.add_argument("cfg"); p.set_defaults(fn=cmd_one)
    p = sub.add_parser("loo"); p.add_argument("--family", action="store_true"); p.set_defaults(fn=cmd_loo)
    p = sub.add_parser("cbs"); p.add_argument("-r", type=float, default=0.8)
    p.add_argument("--fresh", type=float, default=0.5); p.set_defaults(fn=cmd_cbs)
    p = sub.add_parser("verify"); p.add_argument("--pad-m", type=float, default=1.0)
    p.add_argument("--fuse", type=float, default=2.0); p.add_argument("--extrap", type=float, default=2.0)
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("null"); p.add_argument("-s", type=float, default=3.0)
    p.add_argument("-o", type=float, default=0.0); p.add_argument("-x", type=float, default=2.0)
    p.set_defaults(fn=cmd_null)
    a = ap.parse_args()
    a.fn(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
