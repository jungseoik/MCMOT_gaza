#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""경로 개정본(r1·r2·r3) 세션의 IDR·EPFI 수집. 레포 루트에서 실행."""
import json, os, glob, sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from system.metrics import journey as J                       # noqa: E402
from system.metrics.replay import run_replay                  # noqa: E402

PREFIX = "경로v2"
NAMES = {
 "01": ("IDR", "권장경로 2명 넘어감 + 우측 우회 2명"),
 "02": ("IDR", "권장경로 — 매우 느린 속도"),
 "03": ("IDR", "2초 멈췄다 움직였다 이동"),
 "04": ("EPFI", "2명이 완전 빙 돌아 진입"),
 "05": ("EPFI", "2명 시작점 복귀 후 재진입"),
 "06": ("EPFI", "1명이 빙빙 돌아 진입"),
 "07": ("CBS", "2열로 진입 (밀도 아님)"),
 "08": ("CBS", "문 근처 밀집 2회 (밀도)"),
 "09": ("CBS", "5명 + 중간 5명 합류 (밀도 아님)"),
 "10": ("SEI", "7:3 분산"),
 "11": ("SEI", "7:3 + 2명 재진입 (7+2=9)"),
 "12": ("1층", "출구 나가기"),
 "13": ("1층", "출구 → 흡연장"),
 "14": ("1층", "반반 나눠 나가기"),
}
GT_EXIT = {"01":(10,0),"02":(10,0),"03":(10,0),"04":(10,0),"05":(10,0),"06":(10,0),
           "07":(10,0),"08":(10,0),"09":(10,0),"10":(3,7),"11":(3,9),
           "12":(None,10),"13":(12,0),"14":(None,10)}
# 권장 IDR 설정 — 본문 §4 스윕 결과
IDR_OV = {"a_th": 0.707, "v_th": 0.5, "r_th": 0.7, "dt_hold": 1.0}
D_ALLOW = 12.0          # EPFI 판정용 (기존 보고서 §3.4 권장 구간 하한)


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
                out[num] = (d["session_id"], fl, db, lab)
    return out


def main():
    rows = []
    for num, (sid, fl, db, lab) in sorted(sessions().items()):
        base = run_replay(db, {}, fps=1.0)[0].model_dump()
        ex = {e["exit_id"]: e["actual_count"] for e in base["exit_metrics"]}
        # IDR — 권장 설정으로
        z = run_replay(db, {"thresholds": IDR_OV}, fps=1.0)[0].model_dump()["zone_metrics"][0]
        # EPFI — ReID 재구성(사람) 기준
        jr = J.reconstruct(db, J.Params.from_dict({"d_allow": D_ALLOW}))
        ppl = [{"pid": p["person_id"], "epfi": p.get("epfi"), "dev": p.get("dev_m"),
                "route": p.get("route_id"), "evac": p.get("evac_sec"), "obs": p["obs"]}
               for p in jr["persons"] if not p.get("fragment") and p.get("dev_m") is not None]
        ppl.sort(key=lambda x: x["epfi"] if x["epfi"] is not None else 999)
        rows.append(dict(num=num, sid=sid, floor=fl, label=lab,
                         kind=NAMES[num][0], intent=NAMES[num][1],
                         gt_exit=GT_EXIT[num],
                         exit=(ex.get("exit-0", 0), ex.get("exit-1", 0)),
                         sei=base["sei"], cbs=base["cbs_total"],
                         epfi_engine=base["epfi_avg"],
                         idr={"status": z["status"], "delay": z.get("response_delay_sec"),
                              "idr": z.get("idr"), "ratio": z.get("participant_ratio"),
                              "dist": z.get("graph_distance")},
                         people=ppl, n_persons=jr["n_persons"]))
        print(f"S{num} {NAMES[num][0]:5} 출구{(ex.get('exit-0',0), ex.get('exit-1',0))} "
              f"IDR {z['status']:11} EPFI 사람 {len(ppl):2}명 "
              f"최저 {ppl[0]['epfi'] if ppl else 0:5.1f}", flush=True)
    json.dump({"idr_overrides": IDR_OV, "d_allow": D_ALLOW, "names": NAMES, "rows": rows},
              open(Path(__file__).parent / "data.json", "w"), ensure_ascii=False)
    print(f"\n{len(rows)}건 → data.json")


if __name__ == "__main__":
    main()
