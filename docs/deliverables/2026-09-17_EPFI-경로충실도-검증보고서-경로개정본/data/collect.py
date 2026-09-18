#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""EPFI 파라미터 스윕 — 경로 개정본 세션 14건. 레포 루트에서 실행."""
import json, os, glob, sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from system.metrics import journey as J                        # noqa: E402

PREFIX = "대피경로"
D_ALLOWS = [2, 4, 6, 8, 10, 12, 14, 16, 20, 25]
BASE_D = 12.0
# ReID 재구성 인자 — 기본값에서 하나씩만 바꾼다
REID = {
    "cos_th":       [0.40, 0.45, 0.50, 0.55, 0.60],
    "fragment_obs": [25, 50, 75, 100, 150, 200],
    "link_tol":     [0.0, 0.05, 0.1, 0.2],
    "slack_m":      [1.0, 2.0, 3.0, 4.0, 6.0],
}
NAMES = {
 "01": ("참고", "권장경로 2명 넘어감 + 우측 우회 2명"),
 "02": ("음성", "권장경로 — 매우 느린 속도"),
 "03": ("음성", "2초 멈췄다 움직였다 이동"),
 "04": ("양성", "2명이 완전 빙 돌아 진입"),
 "05": ("양성", "2명 시작점 복귀 후 재진입"),
 "06": ("양성", "1명이 빙빙 돌아 진입"),
 "07": ("음성", "CBS 2열로 진입"),
 "08": ("음성", "CBS 문 근처 밀집 2회"),
 "09": ("음성", "CBS 5명 + 중간 5명 합류"),
 "10": ("제외", "SEI 7:3 분산"), "11": ("제외", "SEI 7:3 + 2명 재진입"),
 "12": ("1층", "출구 나가기"), "13": ("1층", "출구 → 흡연장"), "14": ("1층", "반반 나눠 나가기"),
}


def sessions():
    out = {}
    for p in sorted(glob.glob('data/sites/default/sessions/_drills/*.json')):
        d = json.load(open(p)); lab = d.get("label") or ""
        if not lab.startswith(PREFIX):
            continue
        num = lab.split()[1]
        for fl in ("floor4", "floor5"):
            db = f"data/sites/default/sessions/{fl}/{d['session_id']}.db"
            if os.path.exists(db):
                out[num] = (db, fl)
    return out


def summarize(jr, d_allow):
    ppl = [p for p in jr["persons"] if not p.get("fragment") and p.get("dev_m") is not None]
    if not ppl:
        return None
    ep = lambda dev: max(0.0, 1 - dev / d_allow) * 100
    devs = sorted((p["dev_m"] for p in ppl), reverse=True)
    ev = [p["evac_sec"] for p in ppl if p.get("evac_sec") is not None]
    import statistics as st
    return {"n": len(ppl), "min_epfi": round(min(ep(d) for d in devs), 1),
            "med_epfi": round(st.median([ep(d) for d in devs]), 1),
            "max_dev": round(devs[0], 2), "med_dev": round(st.median(devs), 2),
            "n_evac": len(ev), "med_evac": round(st.median(ev), 1) if ev else None,
            "routes": sorted({p.get("route_id") for p in ppl if p.get("route_id")})}


def main():
    ses = sessions()
    out = {"base_d_allow": BASE_D, "d_allows": D_ALLOWS, "reid": REID,
           "names": NAMES, "rows": {}}
    for num, (db, fl) in sorted(ses.items()):
        jr = J.reconstruct(db, J.Params.from_dict({"d_allow": BASE_D}))
        rec = {"floor": fl, "role": NAMES[num][0], "intent": NAMES[num][1],
               "base": summarize(jr, BASE_D),
               "devs": sorted((p["dev_m"] for p in jr["persons"]
                               if not p.get("fragment") and p.get("dev_m") is not None),
                              reverse=True),
               "reid": {}}
        for k, vals in REID.items():
            rec["reid"][k] = {}
            for v in vals:
                j2 = J.reconstruct(db, J.Params.from_dict({"d_allow": BASE_D, k: v}))
                rec["reid"][k][str(v)] = summarize(j2, BASE_D)
        out["rows"][num] = rec
        b = rec["base"]
        print(f"S{num} {NAMES[num][0]:4} 사람 {b['n']:2} · EPFI 최저 {b['min_epfi']:5.1f} "
              f"· 이탈 최대 {b['max_dev']:5.2f}m · 경로 {b['routes']}", flush=True)
    json.dump(out, open(Path(__file__).parent / "epfi_sweep.json", "w"), ensure_ascii=False)
    print(f"\n{len(out['rows'])}건 → epfi_sweep.json")


if __name__ == "__main__":
    main()
