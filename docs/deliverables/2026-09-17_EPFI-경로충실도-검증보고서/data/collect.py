#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""EPFI 검증 자료 수집 — 트랙렛별 배정(현행) vs 사람별 단일 배정.

레포 루트에서 실행. 결과를 epfi_data.json 으로 저장한다.
"""
import json, os, glob, sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[4]   # <루트>/docs/deliverables/<보고서>/data
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from system.metrics import journey as J, recorder            # noqa: E402
from system.metrics.replay import run_replay                 # noqa: E402
from system.spatial import nearest_on_polyline               # noqa: E402

D_ALLOW = 4.0
INTENT = {
    "scenario_01": ("기준", "권장경로 + 우측 우회 2명", 2),
    "scenario_02": ("음성", "매우 느린 속도 — 경로는 정상", 0),
    "scenario_03": ("음성", "2초 멈췄다 이동 — 경로는 정상", 0),
    "scenario_04": ("양성", "2명 완전 빙 돌아", 2),
    "scenario_05": ("양성", "2명 시작점 복귀 후 재진입", 2),
    "scenario_06": ("양성", "1명 빙빙 돌기", 1),
}


def analyse(db, d_allow=D_ALLOW):
    meta = recorder.load_meta(db)
    sv = meta["site_view"]
    mpp = (sv.get("map") or {}).get("m_per_px")
    routes = [(r["id"], np.asarray(r["points"], float)) for r in sv.get("routes", [])
              if len(r.get("points") or []) >= 2]
    res, _tl, _frames, _ = run_replay(db, {"thresholds": {"d_allow": d_allow}}, fps=5.0)
    r = res.model_dump()
    jr = J.reconstruct(db, J.Params.from_dict({"d_allow": d_allow}))
    tlm = {p["global_track_id"]: p for p in r["person_metrics"]}

    people = []
    for p in jr["persons"]:
        if p.get("fragment"):
            continue
        # 사람 단위 값은 **제품 코드(journey.reconstruct)가 낸 것을 그대로 쓴다** —
        # 보고서에서 따로 계산하면 화면·API 와 숫자가 갈린다.
        dev_person, rid, T = p.get("dev_m"), p.get("route_id"), p.get("epfi_dur_sec") or 0.0
        num = den = 0.0
        for s in p.get("segments", []):
            m = tlm.get(s["key"])
            if m and m.get("mean_deviation_m") is not None and m.get("duration_sec"):
                num += m["duration_sec"] * m["mean_deviation_m"]
                den += m["duration_sec"]
        if dev_person is None or den <= 0:
            continue
        dev_track = num / den                      # 현행 화면: 트랙렛별 배정의 가중 평균
        people.append({
            "pid": p["person_id"], "obs": p["obs"], "cams": len(p["cams"]),
            "n_tracklets": p["n_tracklets"], "dur": T,
            "dev_track": dev_track, "dev_person": dev_person,
            "epfi_track": max(0.0, 1 - dev_track / d_allow) * 100,
            "epfi_person": p.get("epfi"),          # 제품 코드가 낸 값
            "evac_sec": p.get("evac_sec"),         # 첫 관측 → 출구 통과
            "route": rid})
    people.sort(key=lambda x: -x["dev_person"])
    return people, r.get("epfi_avg")


def main():
    labels = {}
    for f in sorted(glob.glob('data/sites/default/sessions/_drills/*.json')):
        d = json.load(open(f)); labels[d["session_id"]] = d["label"]
    out = []
    for sid, lab in sorted(labels.items(), key=lambda x: x[1]):
        scen = lab.split()[-1]
        if scen not in INTENT:
            continue
        pkg = "1·3층" if "1·3층" in lab else "3층1차"
        for fl in ("floor4", "floor6"):
            db = f"data/sites/default/sessions/{fl}/{sid}.db"
            if not os.path.exists(db):
                continue
            ppl, avg = analyse(db)
            role, intent, k = INTENT[scen]
            out.append(dict(pkg=pkg, floor=fl, scen=scen, sid=sid, role=role,
                            intent=intent, k=k, epfi_avg_engine=avg, people=ppl))
            print(f"{pkg:6} {scen:12} {role:3} n={len(ppl):2} "
                  f"이탈(사람) 최대 {ppl[0]['dev_person']:5.2f}m "
                  f"· 이탈(트랙렛) 최대 {max(p['dev_track'] for p in ppl):5.2f}m", flush=True)
    json.dump({"d_allow": D_ALLOW, "intent": INTENT, "rows": out},
              open(Path(__file__).parent / "epfi_data.json", "w"), ensure_ascii=False)
    print(f"\n{len(out)}건 → epfi_data.json")


if __name__ == "__main__":
    main()
