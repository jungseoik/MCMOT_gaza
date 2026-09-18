#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""IDR 시나리오별 파라미터 민감도 — 경로 개정본 세션 14건. 레포 루트에서 실행.

설계: 각본이 겨눈 인자를 시나리오마다 하나씩 잡고, **그 인자만** 움직여
개시/미개시가 뒤집히는 지점을 찾는다.
  S01(우측 우회 2명) → r_th(동시만족 비율)    S02(매우 느린 속도) → v_th
  S03(2초 멈췄다 이동) → dt_hold(연속 유지시간)
정상 보행 시나리오(S04~S06)를 같은 스윕에 태워 대조군으로 쓴다.

수집물 3종:
  grid   — base 선정 근거 (dt_hold × v_th 격자에서 S01~S03 개시 여부)
  series — base 임계에서의 1초 샘플 원자료 (구역 내 객체별 speed·align)
  sweep  — base 에서 인자 1개씩 미세 스윕
"""
import json, os, glob, sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from system.metrics.replay import run_replay                   # noqa: E402

PREFIX = "대피경로"
# 운영 기본값(v1.13 라이브)에서 dt_hold 만 내린 값 — 근거는 grid 결과(보고서 §2)
BASE = {"v_th": 0.5, "a_th": 0.707, "r_th": 0.7, "dt_hold": 1.0}
LIVE = {"v_th": 0.5, "a_th": 0.707, "r_th": 0.7, "dt_hold": 3.0}   # 현행 배포 기본값

SWEEP = {
    "r_th":    [0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0],
    "v_th":    [0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    "dt_hold": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0],
    "a_th":    [0.2, 0.35, 0.5, 0.6, 0.707, 0.8, 0.866, 0.95],
}
GRID_DT = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0]
GRID_V = [0.3, 0.4, 0.5, 0.6]
TARGET = {"01": "r_th", "02": "v_th", "03": "dt_hold"}   # 각본이 겨눈 인자
NAMES = {
 "01": ("IDR", "권장경로 8명 + 우측 우회 2명"),
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
                out[num] = (db, fl, lab)
    return out


def idr_of(db, th):
    """임계 th 로 리플레이 → 첫 구역의 IDR 판정."""
    z = run_replay(db, {"thresholds": th}, fps=1.0)[0].model_dump()["zone_metrics"][0]
    return {"status": z["status"], "delay": z.get("response_delay_sec"),
            "idr": z.get("idr"), "ratio": z.get("participant_ratio"),
            "dist": z.get("graph_distance")}


def series_of(db, t_alarm=None):
    """1초 프레임에서 구역 내 객체별 (speed, align) 원자료를 뽑는다.

    엔진의 IDR 판정과 같은 근거 — zone_id 도 같은 point_in_polygon 결과다.
    임계를 바꿔가며 r_e·v_e 를 **재계산 없이** 그릴 수 있다.
    """
    _r, _tl, frames, meta = run_replay(db, {"thresholds": BASE}, fps=1.0)
    t0 = float(meta["alarm_ts"])
    ser = []
    for f in frames:
        mem = [(o.get("speed_mps"), o.get("align")) for o in f["objects"]
               if o.get("zone_id")]
        ser.append({"t": round(f["ts"] - t0, 2), "m": mem})
    return ser


def main():
    ses = sessions()
    out = {"base": BASE, "live": LIVE, "sweep_grid": SWEEP, "target": TARGET,
           "names": NAMES, "grid_dt": GRID_DT, "grid_v": GRID_V, "rows": {}}
    for num, (db, fl, lab) in sorted(ses.items()):
        rec = {"floor": fl, "label": lab, "kind": NAMES[num][0],
               "intent": NAMES[num][1],
               "base": idr_of(db, BASE), "live": idr_of(db, LIVE),
               "sweep": {}, "grid": {}, "series": series_of(db)}
        for k, vals in SWEEP.items():
            rec["sweep"][k] = {str(v): idr_of(db, {**BASE, k: v}) for v in vals}
        for dt in GRID_DT:
            for v in GRID_V:
                rec["grid"][f"{dt}|{v}"] = idr_of(
                    db, {**BASE, "dt_hold": dt, "v_th": v})
        out["rows"][num] = rec
        b, L = rec["base"], rec["live"]
        print(f"S{num} {NAMES[num][0]:5} base {b['status']:11}"
              f"{b['delay'] if b['delay'] else '-':>6}  /  현행 {L['status']:11}"
              f"{L['delay'] if L['delay'] else '-':>6}", flush=True)
    p = Path(__file__).parent / "idr_sweep.json"
    json.dump(out, open(p, "w"), ensure_ascii=False)
    n = sum(len(v) for v in SWEEP.values()) + len(GRID_DT) * len(GRID_V) + 2
    print(f"\n{len(out['rows'])}건 × {n}설정 → {p.name} ({p.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
