#!/usr/bin/env python3
"""출구 카운팅 규칙 A/B 평가 — 캡처된 관측에 판정 규칙만 바꿔 재생한다.

    python tools/exit_eval.py --caps /tmp/exit_cap

추론을 다시 돌리지 않으므로 변형을 수십 개 재도 몇 초다. 정답은 사용자가
영상을 보고 확인한 값(TRUTH)이고, **기존에 맞던 것이 틀어지지 않는 것**이
개선의 조건이다.

규칙 변형
---------
  require_outside  "밖에서 본 적 있는 키만 센다"(현행). 끄면 가림으로 트랙이
                   새로 발급돼 존 안에서 시작한 사람도 셀 수 있다.
  inward           require_outside 를 끌 때의 안전장치 — 존 중심으로 **다가가는**
                   트랙만 센다. 문 안쪽에 원래 있던 사람은 안 다가가므로 빠진다.
  dwell_mode       consecutive(현행) | cumulative. 발끝이 경계에서 깜빡이면
                   연속이 계속 끊긴다(실측: 존 안 8프레임인데 최대 연속 0).
"""
from __future__ import annotations
import argparse, pickle, sys
from collections import defaultdict
from pathlib import Path
import numpy as np, cv2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from system.spatial.geometry import DirectionalLine   # noqa: E402

TRUTH = {  # 사용자가 영상으로 확인한 정답 (2026-09-16 정정본)
    # 값이 int 면 **전 출구 합계**, ("exit-1", n) 이면 **그 출구만** 본다.
    # s11·s12·s14 는 사용자가 exit-1 기준으로 확인했다.
    "scenario_01": 10, "scenario_02": 10, "scenario_03": 10, "scenario_04": 10,
    "scenario_05": 10, "scenario_06": 10, "scenario_07": 10, "scenario_08": 10,
    "scenario_09": 10, "scenario_10": 10, "scenario_13": 12,
    "scenario_11": ("exit-1", 9),    # 7명 들어감 → 2명 나가 배회 → 재진입 = 9
    "scenario_12": ("exit-1", 10),
    "scenario_14": ("exit-1", 10),
}


class Zone:
    """ZoneGate 변형 — 규칙을 켜고 끌 수 있게 다시 쓴 것."""

    def __init__(self, poly, dwell=2, overlap=0.3, require_outside=True,
                 inward=False, cumulative=False, inward_min_px=8.0,
                 dwell_mult=1, dwell_abs=None):
        self.poly = np.array(poly, np.float32).reshape(-1, 1, 2)
        self.cen = np.array(poly, np.float64).mean(axis=0)
        self.dwell = max(1, int(dwell_abs if dwell_abs else dwell * dwell_mult))
        self.overlap = float(overlap)
        self.require_outside, self.inward = require_outside, inward
        self.cumulative, self.inward_min = cumulative, float(inward_min_px)
        self.seen_out, self.streak, self.total = set(), {}, defaultdict(int)
        self.first_d, self.best_d = {}, {}

    def inside(self, pt, bbox=None):
        if cv2.pointPolygonTest(self.poly, (float(pt[0]), float(pt[1])), False) >= 0:
            return True
        if bbox is None or self.overlap <= 0:
            return False
        x1, y1, x2, y2 = bbox
        if x2 <= x1 or y2 <= y1:
            return False
        hit = sum(1 for i in range(5) for j in range(5)
                  if cv2.pointPolygonTest(self.poly,
                                          (x1 + (x2 - x1) * (i + .5) / 5,
                                           y1 + (y2 - y1) * (j + .5) / 5), False) >= 0)
        return hit / 25 >= self.overlap

    def observe(self, key, pt, bbox=None):
        d = float(np.linalg.norm(np.array(pt, np.float64) - self.cen))
        self.first_d.setdefault(key, d)
        self.best_d[key] = min(self.best_d.get(key, 1e18), d)
        ins = self.inside(pt, bbox)
        if not ins:
            self.seen_out.add(key)
            if not self.cumulative:
                self.streak.pop(key, None)
            return None
        if self.require_outside and key not in self.seen_out:
            return None
        if (not self.require_outside) and key not in self.seen_out and self.inward:
            # 밖 관측이 없으면 **다가왔는가** 로 대신한다
            if self.first_d[key] - d < self.inward_min:
                return None
        if self.cumulative:
            self.total[key] += 1
            n = self.total[key]
        else:
            n = self.streak.get(key, 0) + 1
            self.streak[key] = n
        return "out" if n == self.dwell else None


def build(cap, **kw):
    """게이트 구성. kw 는 Zone 에만 넘긴다(통과선은 규칙 변형이 없다)."""
    gates = []
    for e in cap["exits"]:
        cc = (e.get("count_cam") or "").replace("rh_", "")
        if cc and e.get("cam_zone"):
            gates.append({"id": e["id"], "cam": cc, "kind": "zone",
                          "g": Zone(e["cam_zone"], dwell=int(e.get("cam_zone_dwell") or 2), **kw)})
        elif cc and e.get("cam_line"):
            gates.append({"id": e["id"], "cam": cc, "kind": "camline",
                          "g": DirectionalLine(e["cam_line"], e["cam_inside"], margin_px=3.0)})
        elif e.get("line"):
            gates.append({"id": e["id"], "cam": None, "kind": "mapline",
                          "g": DirectionalLine(e["line"], e["inside"], margin_px=6.0)})
    return gates


def run(cap, min_conf=0.0, min_box_h=0.0, **kw):
    """캡처 재생 → {exit_id: set(트랙키)}"""
    gates = build(cap, **kw)
    H = {c: np.asarray(v["mapping"]["H"], np.float64).reshape(3, 3)
         for c, v in cap["cams"].items() if (v.get("mapping") or {}).get("H")}
    counted = {g["id"]: set() for g in gates}
    # 카메라마다 fps 가 다를 수 있다(출구만 10fps 실험) → **시각순**으로 재생한다
    obs = cap["obs"]
    if obs and len(obs[0]) >= 6:
        obs = sorted(obs, key=lambda o: (o[5], o[1]))
    for o in obs:
        k, cam, tid, foot, bbox = o[0], o[1], o[2], o[3], o[4]
        # 서버(엔진)와 같은 게이트 — 캡처에 conf 가 있을 때만 적용된다
        if min_conf > 0 and len(o) >= 7 and o[6] < min_conf:
            continue
        if min_box_h > 0 and (bbox[3] - bbox[1]) < min_box_h:
            continue
        key = f"{cam}:{tid}"
        for g in gates:
            if g["kind"] == "zone":
                if g["cam"] != cam:
                    continue
                ev = g["g"].observe(key, foot, bbox)
            elif g["kind"] == "camline":
                if g["cam"] != cam:
                    continue
                ev = g["g"].observe(key, foot)
            else:
                h = H.get(cam)
                if h is None:
                    continue
                q = h @ np.array([foot[0], foot[1], 1.0])
                if abs(q[2]) < 1e-9:
                    continue
                ev = g["g"].observe(key, (q[0] / q[2], q[1] / q[2]))
            if ev == "out":
                counted[g["id"]].add(key)
    return counted



# ---------------------------------------------------------------- 사람 단위
def person_merge(cap, counted: dict, max_gap_sec: float = 2.0,
                 max_px: float = 260.0) -> dict:
    """게이트를 통과한 트랙들을 **같은 사람끼리 묶는다**.

    문 앞에서 가림으로 트랙이 끊기면 같은 사람이 여러 키로 세진다(초과).
    같은 카메라에서 앞 트랙이 끝난 직후(max_gap_sec) 그 자리 근처(max_px)에서
    새 트랙이 시작하면 이어진 것으로 본다 — 사람이 순간이동하지 않는다.

    ReID 재구성(journey)과 같은 발상이되, 문 앞 구간만 보므로 임베딩 없이
    시공간만으로 충분하다.
    """
    fps = cap["fps"]
    span = {}                       # key -> (first_k, last_k, first_pt, last_pt)
    for o in cap["obs"]:
        k, cam, tid, foot = o[0], o[1], o[2], o[3]
        key = f"{cam}:{tid}"
        if key not in span:
            span[key] = [k, k, foot, foot]
        else:
            span[key][1] = k
            span[key][3] = foot
    out = {}
    for eid, keys in counted.items():
        ks = sorted(keys, key=lambda x: span[x][0])
        parent = {k: k for k in ks}
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]; x = parent[x]
            return x
        for i, a in enumerate(ks):
            for b in ks[i + 1:]:
                if a.split(":")[0] != b.split(":")[0]:
                    continue                      # 다른 카메라는 안 묶는다
                gap = (span[b][0] - span[a][1]) / fps
                if gap < 0 or gap > max_gap_sec:
                    continue
                d = ((span[a][3][0] - span[b][2][0]) ** 2 +
                     (span[a][3][1] - span[b][2][1]) ** 2) ** 0.5
                if d <= max_px:
                    parent[find(b)] = find(a)
        out[eid] = {find(k) for k in ks}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--caps", default="/tmp/exit_cap")
    ap.add_argument("--min-conf", type=float, default=0.0,
                    help="서버 엔진과 같은 min_conf 게이트 (사이트 기본 0.5)")
    ap.add_argument("--caps2", default=None, help="비교할 두 번째 캡처(예: 출구 10fps)")
    a = ap.parse_args()
    caps = {}
    for f in sorted(Path(a.caps).glob("*.pkl")):
        caps[f.stem] = pickle.load(open(f, "rb"))
    if not caps:
        raise SystemExit(f"캡처 없음: {a.caps}")

    def load(d):
        return {f.stem: pickle.load(open(f, "rb")) for f in sorted(Path(d).glob("*.pkl"))}
    A = load(a.caps)
    B = load(a.caps2) if a.caps2 else {}
    if not A:
        raise SystemExit(f"캡처 없음: {a.caps}")

    # 현행(5fps dwell2) 대비, 출구만 10fps 로 올렸을 때 dwell 2~5 가 어떻게 되나
    COLS = [("현행 5fps·d2", A, dict(), None)]
    if B:
        COLS += [(f"10fps·d{d}", B, dict(), d) for d in (2, 3, 4, 5)]

    print(f"{'시나리오':>10}{'정답':>6}" + "".join(f"{n:>20}" for n, *_ in COLS))
    print(f"{'':>16}(* = 그 출구만)")
    tot = {n: [0, 0] for n, *_ in COLS}
    for sid in sorted(A):
        tr = TRUTH.get(sid)
        scope, t = (tr if isinstance(tr, tuple) else (None, tr))
        row = f"{sid.replace('scenario_','s'):>10}{f'{t}{chr(42) if scope else chr(32)}':>6}"
        for n, caps, kw, mult in COLS:
            c0 = caps.get(sid)
            if c0 is None:
                row += f"{'—':>20}"; continue
            cnt = (run(c0, dwell_abs=mult, min_conf=a.min_conf, **kw) if mult
                   else run(c0, min_conf=a.min_conf, **kw))
            c = len(cnt.get(scope, ())) if scope else sum(len(v) for v in cnt.values())
            if t:
                e = c - t
                tot[n][0] += (e == 0); tot[n][1] += abs(e)
                row += f"{c:>15}{'★' if e == 0 else f'{e:+d}':>5}"
            else:
                row += f"{c:>20}"
        print(row)
    n_tot = len(A)
    print(f"\n{'GT 정확 일치':>16}" + "".join(
        f"{'%d/%d' % (tot[n][0], n_tot):>20}" for n, *_ in COLS))
    print(f"{'(참고) 총 오차':>16}" + "".join(f"{tot[n][1]:>20}" for n, *_ in COLS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
