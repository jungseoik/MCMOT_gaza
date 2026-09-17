#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""IDR 파라미터 스윕 — 경로 개정본 세션 14건. 레포 루트에서 실행."""
import json, os, glob, sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from system.metrics.replay import run_replay                   # noqa: E402

PREFIX = "경로v2"
BASE = {"v_th": 0.5, "a_th": 0.707, "r_th": 0.7, "dt_hold": 3.0}   # 현재 라이브 기본값
SWEEP = {
    "v_th":    [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.9],
    "a_th":    [0.2, 0.35, 0.5, 0.6, 0.707, 0.8, 0.866, 0.95],
    "r_th":    [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
    "dt_hold": [1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0],
}
NAMES = {
 "01": ("IDR", "권장경로 2명 넘어감 + 우측 우회 2명"),
 "02": ("IDR", "권장경로 — 매우 느린 속도"),
 "03": ("IDR", "2초 멈췄다 움직였다 이동"),
 "04": ("EPFI", "2명이 완전 빙 돌아 진입"),
 "05": ("EPFI", "2명 시작점 복귀 후 재진입"),
 "06": ("EPFI", "1명이 빙빙 돌아 진입"),
 "07": ("CBS", "2열로 진입"), "08": ("CBS", "문 근처 밀집 2회"),
 "09": ("CBS", "5명 + 중간 5명 합류"),
 "10": ("SEI", "7:3 분산"), "11": ("SEI", "7:3 + 2명 재진입"),
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


def idr_of(db, th):
    z = run_replay(db, {"thresholds": th}, fps=1.0)[0].model_dump()["zone_metrics"][0]
    return {"status": z["status"], "delay": z.get("response_delay_sec"),
            "idr": z.get("idr"), "ratio": z.get("participant_ratio"),
            "dist": z.get("graph_distance")}


def main():
    ses = sessions()
    out = {"base": BASE, "sweep": SWEEP, "names": NAMES, "rows": {}}
    for num, (db, fl) in sorted(ses.items()):
        rec = {"floor": fl, "kind": NAMES[num][0], "intent": NAMES[num][1],
               "base": idr_of(db, BASE), "sweep": {}}
        for k, vals in SWEEP.items():
            rec["sweep"][k] = {}
            for v in vals:
                th = dict(BASE); th[k] = v
                rec["sweep"][k][str(v)] = idr_of(db, th)
        out["rows"][num] = rec
        b = rec["base"]
        print(f"S{num} {NAMES[num][0]:5} 기본 {b['status']:11} "
              + (f"{b['delay']:.0f}s IDR {b['idr']:.2f}" if b["idr"] else ""), flush=True)
    json.dump(out, open(Path(__file__).parent / "idr_sweep.json", "w"), ensure_ascii=False)
    print(f"\n{len(out['rows'])}건 × {sum(len(v) for v in SWEEP.values())}설정 → idr_sweep.json")


if __name__ == "__main__":
    main()
