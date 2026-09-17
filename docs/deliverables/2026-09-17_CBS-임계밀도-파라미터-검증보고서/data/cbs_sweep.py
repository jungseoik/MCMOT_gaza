# ρcrit 스윕 수집 — 레포 루트에서 실행. 결과를 cbs_sweep.json 으로 저장한다.
import json, glob, os, time, warnings; warnings.filterwarnings("ignore")
from system.metrics.replay import run_replay

RHOS = [0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.5,3.0,3.5]
labels={}
for p in sorted(glob.glob('data/sites/default/sessions/_drills/*.json')):
    d=json.load(open(p)); labels[d["session_id"]]=d["label"]

out=[]
t0=time.time()
for sid,lab in sorted(labels.items(), key=lambda x:x[1]):
    for fl in ("floor4","floor5","floor6"):
        db=f"data/sites/default/sessions/{fl}/{sid}.db"
        if not os.path.exists(db): continue
        # 기준 1회 — 타임라인 밀도 시계열 + 피크
        res,tl,_,_ = run_replay(db, {}, fps=1.0)
        r=res.model_dump()
        series=[(x.ts, dict(x.bottleneck_density)) for x in tl]
        peaks={b["bottleneck_id"]:b["peak_density"] for b in r["bottleneck_metrics"]}
        rec={"label":lab,"floor":fl,"sid":sid,"peaks":peaks,
             "dur": round(series[-1][0]-series[0][0],1) if series else 0,
             "series":[[round(ts-series[0][0],2), {k:round(v,4) for k,v in d.items()}]
                       for ts,d in series],
             "sweep":{}}
        # ρcrit 스윕 — 엔진 직접 재계산
        for rho in RHOS:
            rr = run_replay(db, {"rho_crit": rho}, fps=1.0)[0].model_dump()
            rec["sweep"][str(rho)] = {
                "total": rr["cbs_total"],
                "per": {b["bottleneck_id"]: {"cbs": b["cbs"],
                                             "over": b["over_threshold_sec"],
                                             "risk": b["risk_level"]}
                        for b in rr["bottleneck_metrics"]},
            }
        out.append(rec)
        print(f"{lab:26} {fl:7} peak={max(peaks.values()):5.2f} "
              + " ".join(f"{rec['sweep'][str(r)]['total']:6.2f}" for r in RHOS))
json.dump({"rhos": RHOS, "rows": out},
          open(os.path.join(os.path.dirname(__file__), "cbs_sweep.json"), "w"),
          ensure_ascii=False)
print(f"\n{time.time()-t0:.1f}s")
