# 세션 결과 수집 — :8900 서버가 떠 있어야 한다. 결과를 sessions.json 으로 저장.
import json, glob, os, urllib.request
rows=[]
for p in sorted(glob.glob('data/sites/default/sessions/_drills/*.json')):
    d=json.load(open(p)); sid=d["session_id"]; lab=d["label"]
    r=json.load(urllib.request.urlopen(f"http://127.0.0.1:8900/api/drill/{sid}/result"))
    for pf in r["per_floor"]:
        res=pf["result"]
        ex={e["exit_id"]:e for e in res.get("exit_metrics",[])}
        rows.append({
          "label":lab, "floor":pf["floor_id"], "sid":sid,
          "sei":res.get("sei"),
          "exits":{k:{"n":v.get("actual_count"),"cap":v.get("design_capacity"),
                      "a_share":v.get("actual_share"),"d_share":v.get("design_share"),
                      "w":v.get("width_m")} for k,v in ex.items()},
          "total":r["summary"]["total_passed"],
        })
json.dump(rows, open(os.path.join(os.path.dirname(__file__), "sessions.json"), "w"),
          ensure_ascii=False, indent=1)
print(f"{'시나리오':26} {'층':7} {'SEI':>6} {'e0':>4} {'e1':>4} {'합':>4}")
for o in rows:
    e0=o["exits"].get("exit-0",{}).get("n"); e1=o["exits"].get("exit-1",{}).get("n")
    print(f"{o['label']:26} {o['floor']:7} {o['sei']:6.1f} {str(e0):>4} {str(e1):>4} {o['total']:>4}")
