#!/usr/bin/env python
"""출입구 카운트 방식(선 ↔ 영역) 전환·비교 — CJ 실훈련 패키지용.

같은 출입구(위치·안쪽 방향은 site.json 정본)를 두 방식으로 센다.
  line  도면 통과선 (DirectionalLine) — 안쪽(복도) → 바깥(계단)으로 넘으면 OUT
  zone  문 카메라 화면 영역 (ZoneGate) — 문짝 영역에 dwell 프레임 머물면 OUT
방식 정의는 media/vsource/cj/drill-1022/exit_variants.json. 출입구별로 바뀌는 건
count_cam·cam_zone·cam_zone_dwell 뿐이다(line 은 이 셋을 비운다).

    python tools/exit_variants.py show                       # 지금 운영 설정이 어느 방식인지
    python tools/exit_variants.py apply zone                 # 운영 설정을 영역 방식으로 (서버 반영)
    python tools/exit_variants.py compare sess-…  sess-…     # 녹화본을 두 방식으로 재계산
    python tools/exit_variants.py score [sess-… …]           # 근사 정답(answers.json) 대비 MAE

score 는 층별 '피난층 종료' 시각까지의 출구별 카운트를 정답과 비교한다. 세션을 안 주면
각 층의 가장 최근 CJ1022 단독 세션을 고른다. 녹화 당시 집계(live)도 같이 보여준다.

compare 는 녹화 db 를 엔진에 다시 흘려 두 방식의 카운트·SEI 를 나란히 낸다. 출입구·
병목은 **지금 site.json** 것을 쓴다(녹화 당시가 아니라) — 출입구를 고친 뒤 같은 관측으로
비교하려는 도구다. 원본 세션 저장물은 건드리지 않는다.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VAR = ROOT / "media/vsource/cj/drill-1022/exit_variants.json"
SITE = ROOT / "data/sites/default"
FIELDS = ("count_cam", "cam_zone", "cam_zone_dwell")


def _variants() -> dict:
    return json.loads(VAR.read_text(encoding="utf-8"))["variants"]


def _apply_to_exits(exits: list[dict], spec: dict) -> list[dict]:
    """출입구 목록 사본에 방식 하나를 입힌다. spec 에 없는 출입구는 그대로."""
    out = copy.deepcopy(exits)
    for e in out:
        if e["id"] not in spec:
            continue
        for k in FIELDS:
            e.pop(k, None)
        e["count_cam"] = None
        e["cam_zone"] = None
        e.update(spec[e["id"]])
        e.setdefault("cam_zone_dwell", 2)
    return out


def _mode_of(exits: list[dict]) -> str:
    return "zone" if any(e.get("count_cam") and e.get("cam_zone") for e in exits) else "line"


def cmd_show(_a) -> None:
    site = json.loads((SITE / "site.json").read_text(encoding="utf-8"))
    fl = {f["id"]: f for f in site["floors"]}
    for fid in sorted(next(iter(_variants().values()))["floors"]):
        ex = fl[fid]["exits"]
        print(f"{fid} {fl[fid]['name']}: {_mode_of(ex)} 방식 · "
              + ", ".join(f"{e['id']}→{e.get('count_cam') or '도면선'}" for e in ex))


def cmd_apply(a) -> None:
    from system.config.schema import SiteConfig
    from system.config.store import SiteStore
    spec = _variants()[a.variant]["floors"]
    site = json.loads((SITE / "site.json").read_text(encoding="utf-8"))
    for f in site["floors"]:
        if f["id"] in spec:
            f["exits"] = _apply_to_exits(f["exits"], spec[f["id"]])
    SiteStore(str(SITE.parent)).save_site(SiteConfig.model_validate(site))
    try:                                    # 서버 엔진 재구성 (층 이름 그대로 PUT)
        fid = next(iter(spec))
        name = next(f["name"] for f in site["floors"] if f["id"] == fid)
        r = urllib.request.Request(f"http://127.0.0.1:8900/api/floors/{fid}",
                                   data=json.dumps({"name": name}).encode(), method="PUT",
                                   headers={"Content-Type": "application/json"})
        urllib.request.urlopen(r, timeout=10).read()
        print(f"적용: {a.variant} 방식 → {sorted(spec)} (서버 반영)")
    except Exception as e:                  # noqa: BLE001 — 파일은 이미 저장됐다
        print(f"적용: {a.variant} 방식 → {sorted(spec)} (서버 반영 실패: {e} — 재시작 시 반영)")
    cmd_show(a)


def _replay(db: Path, exits: list[dict], bottlenecks: list[dict]):
    from system.config.schema import CameraConfig, SiteConfig
    from system.metrics import recorder
    from system.metrics.engine import MetricsEngine
    meta = recorder.load_meta(str(db))
    sv = copy.deepcopy(meta["site_view"])
    sv["exits"], sv["bottlenecks"] = exits, bottlenecks
    eng = MetricsEngine(SiteConfig.model_validate(sv),
                        [CameraConfig.model_validate(c) for c in meta.get("cameras", [])])
    eng.start_session(t_alarm=float(meta["alarm_ts"]),
                      alarm_origins=[tuple(o) for o in meta.get("alarm_origins") or []] or None)
    for cam_id, ts, tracks in recorder.iter_calls(str(db)):
        eng.on_tracks(cam_id, ts, tracks)
    counts = {eid: e.out_count for eid, e in eng._exits.items()}
    r = eng.stop_session()
    return counts, r, list(eng.session_timeline()), float(meta["alarm_ts"])


def cmd_compare(a) -> None:
    site = json.loads((SITE / "site.json").read_text(encoding="utf-8"))
    fl = {f["id"]: f for f in site["floors"]}
    var = _variants()
    for sid in a.sessions:
        hits = sorted((SITE / "sessions").glob(f"*/{sid}.db"))
        if not hits:
            print(f"{sid}: 녹화 db 없음"); continue
        db = hits[0]; fid = db.parent.name
        if fid not in fl or fid not in next(iter(var.values()))["floors"]:
            print(f"{sid}: {fid} 는 비교 대상 층이 아님"); continue
        print(f"== {sid} · {fid} {fl[fid]['name']}")
        for name, v in var.items():
            ex = _apply_to_exits(fl[fid]["exits"], v["floors"][fid])
            counts, r, _tl, _t0 = _replay(db, ex, fl[fid]["bottlenecks"])
            tot = sum(counts.values())
            print(f"   {name:5s} " + " · ".join(f"{k} {c:3d}" for k, c in counts.items())
                  + f"  = {tot:3d}명 | SEI {r.sei:5.1f}")


ANS = ROOT / "media/vsource/cj/drill-1022/answers.json"


def _counts_at(timeline, t0: float, cutoff: float) -> dict:
    """타임라인에서 경보 후 cutoff 초 시점의 출구별 누적 OUT."""
    last = {}
    for p in timeline:
        ts = p["ts"] if isinstance(p, dict) else p.ts
        ec = p["exit_counts"] if isinstance(p, dict) else p.exit_counts
        if ts - t0 > cutoff + 1e-6:
            break
        last = dict(ec or {})
    return last


def _latest_session(fid: str) -> str | None:
    """그 층의 가장 최근 CJ1022 세션 (라벨 기준)."""
    best = None
    for j in (SITE / "sessions" / "_drills").glob("sess-*.json"):
        d = json.loads(j.read_text(encoding="utf-8"))
        if fid in (d.get("floors") or []) and str(d.get("label", "")).startswith("CJ1022"):
            if (SITE / "sessions" / fid / f"{d['session_id']}.db").is_file():
                if best is None or d["alarm_ts"] > best[0]:
                    best = (d["alarm_ts"], d["session_id"])
    return best[1] if best else None


def cmd_score(a) -> None:
    ans = json.loads(ANS.read_text(encoding="utf-8"))
    site = json.loads((SITE / "site.json").read_text(encoding="utf-8"))
    fl = {f["id"]: f for f in site["floors"]}
    var = _variants()
    given = {}
    for sid in a.sessions or []:
        hit = next(iter(sorted((SITE / "sessions").glob(f"*/{sid}.db"))), None)
        if hit:
            given[hit.parent.name] = sid
    err = {"live": [], **{k: [] for k in var}}
    rows = []
    for g in ans["floors"]:
        fid, cut = g["floor"], float(g["floor_end_sec"])
        sid = given.get(fid) or _latest_session(fid)
        if not sid:
            print(f"{g['name']}: 세션 없음"); continue
        db = SITE / "sessions" / fid / f"{sid}.db"
        saved = json.loads((SITE / "sessions" / fid / f"{sid}.json").read_text(encoding="utf-8"))
        label = json.loads((SITE / "sessions/_drills" / f"{sid}.json").read_text(encoding="utf-8")).get("label", "")
        live = _counts_at(saved["timeline"], saved["result"]["alarm_ts"], cut)
        got = {"live": live}
        for name, v in var.items():
            ex = _apply_to_exits(fl[fid]["exits"], v["floors"][fid])
            _c, _r, tl, t0 = _replay(db, ex, fl[fid]["bottlenecks"])
            got[name] = _counts_at([x.model_dump() if hasattr(x, "model_dump") else x for x in tl], t0, cut)
        print(f"== {g['name']} · {sid} ({label}) · 기준 {g['floor_end_clip']} ({cut:.0f}s)")
        for eid, gt in g["by_exit"].items():
            cells = []
            for k in ("live", *var):
                v = got[k].get(eid)
                if v is None:                  # 녹화 당시 출구 id 가 달랐다 (옛 설정)
                    cells.append(f"{k} —"); continue
                err[k].append(abs(v - gt["count"]))
                cells.append(f"{k} {v:3d} ({v - gt['count']:+d})")
            print(f"   {eid} {gt['cam']:5s} 정답 {gt['count']:3d} | " + " | ".join(cells))
    print("\nMAE (출구별 절대오차 평균)")
    for k, e in err.items():
        if e:
            print(f"   {k:5s} {sum(e)/len(e):5.1f}명  (출구 {len(e)}개)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").set_defaults(fn=cmd_show)
    p = sub.add_parser("apply"); p.add_argument("variant", choices=["line", "zone"]); p.set_defaults(fn=cmd_apply)
    p = sub.add_parser("compare"); p.add_argument("sessions", nargs="+"); p.set_defaults(fn=cmd_compare)
    p = sub.add_parser("score"); p.add_argument("sessions", nargs="*"); p.set_defaults(fn=cmd_score)
    a = ap.parse_args()
    a.fn(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
