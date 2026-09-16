#!/usr/bin/env python3
"""출구 카운팅 진단 — 왜 안 세졌는지 트랙 단위로 찍는다.

    python tools/exit_debug.py --package aihub-drill-1f3f --scenario scenario_06

rehearsal_viz 와 **같은 추론·같은 판정기**를 쓰되 영상은 안 만들고, 트랙마다
게이트에서 무슨 일이 있었는지만 남긴다. 세지지 않은 이유를 네 갈래로 나눈다:

  밖관측없음   ZoneGate 는 "밖에서 본 적 있는 키" 만 센다. 문 안쪽에서 트랙이
               새로 생기면(가려졌다 나타남 등) 영영 안 세진다.
  dwell미달    영역 안에 dwell 프레임을 못 채우고 트랙이 끝났다.
  중복차단     같은 키가 이미 세졌다. 나갔다 다시 들어와도 한 번만 센다.
  미진입       영역/선에 애초에 안 닿았다.
"""
from __future__ import annotations
import argparse, sys
from collections import defaultdict
from pathlib import Path
import cv2, numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import model_zoo                                                # noqa: E402
from system.vsource import package as vpkg                      # noqa: E402
from system.spatial.geometry import DirectionalLine, ZoneGate   # noqa: E402
from system.tracking.analyzer import AnalyzerThread             # noqa: E402
from tracker.boost_track import BoostTrack                      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--package", required=True)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--fps", type=float, default=5.0)
    ap.add_argument("--capture", default=None,
                    help="관측을 pkl 로 저장 (판정 규칙만 바꿔 재생하려면 필수)")
    a = ap.parse_args()

    pkg = vpkg.get(a.package)
    root = Path(pkg["_root"])
    scen = next(s for s in pkg["scenarios"] if s["id"] == a.scenario)
    cams = {c["cam"]: c for c in pkg["cameras"]}
    floors = sorted({cams[st["cam"]]["floor"] for st in scen["streams"]})
    import json
    site = json.load(open(ROOT / "data/sites/default/site.json"))
    fl = next(f for f in site["floors"] if f["id"] == floors[0])

    gates = []
    for e in fl["exits"]:
        cc = (e.get("count_cam") or "").replace("rh_", "")
        if cc and e.get("cam_zone"):
            gates.append({"id": e["id"], "cam": cc, "kind": "zone",
                          "g": ZoneGate(e["cam_zone"], dwell=int(e.get("cam_zone_dwell") or 2)),
                          "poly": np.array(e["cam_zone"], np.float32).reshape(-1, 1, 2)})
        elif cc and e.get("cam_line"):
            gates.append({"id": e["id"], "cam": cc, "kind": "camline",
                          "g": DirectionalLine(e["cam_line"], e["cam_inside"], margin_px=3.0)})
        elif e.get("line"):
            gates.append({"id": e["id"], "cam": None, "kind": "mapline",
                          "g": DirectionalLine(e["line"], e["inside"], margin_px=6.0)})
    print(f"[dbg] {a.scenario} · 층 {floors} · 게이트 " +
          ", ".join(f"{g['id']}({g['kind']}@{g['cam'] or 'map'})" for g in gates))

    # rehearsal_viz 와 **같은 방식**으로 모델을 만든다(프로파일 경유).
    from default_settings import GeneralSettings
    GeneralSettings.values["use_ecc"] = False      # 고정 CCTV — 라이브와 동일
    GeneralSettings.values["dataset"] = "mot20"
    GeneralSettings.values["test_dataset"] = True
    from src.inference_gpu import BoostTrackGPUInference
    inf = BoostTrackGPUInference(profile="auto")
    # ⚠ __init__ 이 use_ecc=True 로 덮어쓴다 — **모델 생성 뒤에** 다시 꺼야 한다.
    GeneralSettings.values["use_ecc"] = False

    H = {c: np.asarray(cams[c]["mapping"]["H"], np.float64).reshape(3, 3)
         for c in cams if (cams[c].get("mapping") or {}).get("H")}
    mpp = float((fl.get("map") or {}).get("m_per_px") or 0)

    streams = {st["cam"]: root / st["file"] for st in scen["streams"]}
    caps, trackers = {}, {}
    for cam, f in streams.items():
        cap = cv2.VideoCapture(str(f))
        src = cap.get(cv2.CAP_PROP_FPS) or 30.0
        caps[cam] = {"cap": cap, "stride": max(1, int(round(src / a.fps)))}
        trackers[cam] = BoostTrack(max_age=max(1, int(round(a.fps * 2))), per_instance_ids=True)
        assert trackers[cam].ecc is None, "ECC 가 켜져 있다 — 라이브와 다르다"

    # 트랙별 게이트 이력
    obs: list = []          # (k, cam, tid, foot, bbox) — 판정 규칙 재생용
    hist = defaultdict(lambda: {"in_zone": 0, "out_zone": 0, "counted": False,
                                "first": None, "last": None, "max_streak": 0})
    counted = {g["id"]: set() for g in gates}
    k = 0
    while True:
        alive = False
        for cam, c in caps.items():
            fr = None
            for _ in range(c["stride"] - 1):
                c["cap"].grab()
            ok, f0 = c["cap"].read()
            if ok:
                fr, alive = f0, True
            if fr is None:
                continue
            pred, ref = inf.detector.detect_frame(fr)
            sc = min(ref.shape[2] / fr.shape[0], ref.shape[3] / fr.shape[1])
            tg = trackers[cam].update(pred, ref, fr, f"{cam}:{k}")
            dx, ds = AnalyzerThread._frame_dets(pred, sc)
            for t in np.asarray(tg).reshape(-1, tg.shape[1] if tg.size else 6):
                x1, y1, x2, y2, tid = t[0], t[1], t[2], t[3], int(t[4])
                key = f"{cam}:{tid}"
                foot = ((x1 + x2) / 2, y2)
                obs.append((k, cam, int(tid), (float(foot[0]), float(foot[1])),
                            (float(x1), float(y1), float(x2), float(y2))))
                for g in gates:
                    if g["kind"] == "zone" and g["cam"] == cam:
                        ins = g["g"]._inside(foot, (x1, y1, x2, y2))
                        h = hist[(g["id"], key)]
                        h["first"] = h["first"] if h["first"] is not None else k
                        h["last"] = k
                        if ins:
                            h["in_zone"] += 1
                        else:
                            h["out_zone"] += 1
                        ev = g["g"].observe(key, foot, (x1, y1, x2, y2))
                        h["max_streak"] = max(h["max_streak"], g["g"]._streak.get(key, 0))
                        if ev == "out" and key not in counted[g["id"]]:
                            counted[g["id"]].add(key); h["counted"] = True
                    elif g["kind"] == "camline" and g["cam"] == cam:
                        ev = g["g"].observe(key, foot)
                        h = hist[(g["id"], key)]
                        h["first"] = h["first"] if h["first"] is not None else k
                        h["last"] = k
                        if ev == "out" and key not in counted[g["id"]]:
                            counted[g["id"]].add(key); h["counted"] = True
                    elif g["kind"] == "mapline" and cam in H:
                        q = H[cam] @ np.array([foot[0], foot[1], 1.0])
                        if abs(q[2]) < 1e-9:
                            continue
                        ev = g["g"].observe(key, (q[0] / q[2], q[1] / q[2]))
                        h = hist[(g["id"], key)]
                        h["first"] = h["first"] if h["first"] is not None else k
                        h["last"] = k
                        if ev == "out" and key not in counted[g["id"]]:
                            counted[g["id"]].add(key); h["counted"] = True
        if not alive:
            break
        k += 1
    for c in caps.values():
        c["cap"].release()

    if a.capture:
        import pickle
        cp = Path(a.capture); cp.parent.mkdir(parents=True, exist_ok=True)
        pickle.dump({"scenario": a.scenario, "package": a.package, "fps": a.fps,
                     "frames": k, "floor": floors[0], "obs": obs,
                     "exits": fl["exits"], "cams": {c: cams[c] for c in streams},
                     "map": fl.get("map")}, open(cp, "wb"))
        print(f"[dbg] 관측 {len(obs)}건 → {cp}")
    print(f"[dbg] {k}프레임 · 카운트 " +
          " · ".join(f"{gid} {len(s)}" for gid, s in counted.items()))
    for g in gates:
        gid = g["id"]
        rows = [(key, h) for (g2, key), h in hist.items() if g2 == gid]
        if g["kind"] != "zone":
            rows = [(kk, h) for kk, h in rows if h["counted"]]
            print(f"\n  [{gid}] 통과 {len(rows)}건: " +
                  ", ".join(f"{kk}@{h['first'] / a.fps:.0f}s" for kk, h in sorted(rows, key=lambda r: r[1]['first'])))
            continue
        touched = [(kk, h) for kk, h in rows if h["in_zone"] > 0]
        print(f"\n  [{gid}] 영역에 닿은 트랙 {len(touched)}개")
        print(f"  {'트랙':>12}{'안':>4}{'밖':>4}{'최대연속':>8}{'구간':>13}  판정")
        for kk, h in sorted(touched, key=lambda r: r[1]["first"]):
            if h["counted"]:
                v = "✓ 카운트"
            elif h["out_zone"] == 0:
                v = "✗ 밖관측 없음 (문 안쪽에서 트랙 생성)"
            elif h["max_streak"] < 2:
                v = f"✗ dwell 미달 (최대 {h['max_streak']})"
            else:
                v = "✗ 원인 미상"
            print(f"  {kk:>12}{h['in_zone']:4d}{h['out_zone']:4d}{h['max_streak']:8d}"
                  f"{h['first'] / a.fps:6.1f}~{h['last'] / a.fps:5.1f}s  {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
