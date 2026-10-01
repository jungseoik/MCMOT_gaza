# -*- coding: utf-8 -*-
"""**루프 겹쳐 사람별 궤적 복원** — 같은 15초를 여러 바퀴 본 걸 포개서 한 벌로.

17F RTSP 는 15.041667초짜리 클립을 무한 루프한다. **매 바퀴가 같은 내용**이므로,
여러 바퀴의 관측을 위상(phase = t mod 주기)으로 포개면 한 사람의 궤적을 여러 번
본 셈이 된다. 한 바퀴에서 검출이 빠진 구간도 다른 바퀴에는 있으니, 포개면 구멍이
메워지고 id 도 한 사람당 하나로 정리된다.

  · 한 바퀴에만 나온 조각은 **버린다** — 진짜 사람은 매 바퀴 나온다. 반사·의자
    오검출, id 분절 찌꺼기, 한자리에 멈춘 점이 여기서 같이 걸러진다.
  · 같은 위상·같은 자리의 점들은 **평균**낸다 — 발끝점 지터가 √N 로 줄어든다.

**표출 전용이다.** 4대 지표는 서버가 원 관측으로 낸 값을 그대로 쓴다. 이건
"화면에 보이는 점"만 다듬는 것이고, demo 영상의 `tools/demo_tracks.py`(이상적
궤적)와 같은 성격이다 — 다만 사람이 손으로 그리는 대신 **반복 관측이 근거**다.

    python tools/demo_loopfold.py --sec 95 --out results/floor_viz/_loop/default.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np                                  # noqa: E402

BASE = "http://localhost:8900"
PERIOD = 15.041667          # 원본 클립 길이(ffprobe). 실측 간격 15.03~15.05 와 일치
BIN = 0.10                  # 위상 격자(초)


def api(path, timeout=30):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.load(r)


def capture(floor: str, sec: float) -> list[dict]:
    """SSE 로 스냅샷을 모은다 — 폴링은 주기가 어긋나 같은 걸 두 번 받는다."""
    req = urllib.request.Request(f"{BASE}/api/map/stream?floor={floor}")
    out, t0 = [], time.time()
    with urllib.request.urlopen(req, timeout=sec + 30) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            d = json.loads(line[5:].strip())
            if out and out[-1]["ts"] == d["ts"]:
                continue
            out.append({"ts": d["ts"],
                        "objects": [{k: o[k] for k in ("gid", "cam_id", "x", "y")}
                                    for o in (d.get("objects") or [])]})
            if time.time() - t0 >= sec:
                break
    return out


def phase_ref(snaps: list[dict], cam: str) -> float | None:
    """그 카메라의 **되감기 시각**(빈 구간 시작)을 위상 0 으로 잡는다."""
    ts = [(d["ts"], sum(1 for o in d["objects"] if o["cam_id"] == cam)) for d in snaps]
    starts = []
    for t, n in ts:
        if n == 0:
            if not starts or t - starts[-1][1] > 1.0:
                starts.append([t, t])
            else:
                starts[-1][1] = t
    st = [a for a, b in starts if b - a > 0.4]
    if not st:
        return None
    # 여러 번 관측한 되감기 시각을 주기로 접어 평균 — 한 번만 쓰면 오차가 남는다
    base = st[0]
    off = [(x - base) % PERIOD for x in st]
    off = [o - PERIOD if o > PERIOD / 2 else o for o in off]
    return base + float(np.mean(off))


def refine(snaps, cam, ref, people):
    """위상 기준점을 **관측과 맞춰** 미세 조정한다.

    되감기(빈 구간) 시작만으로 잡은 기준점은 0.2~0.5초 어긋날 수 있다(빈 구간의
    시작 자체가 마지막 사람이 사라진 시각이라 검출 지연이 섞인다). 그대로 두면
    사람이 등·퇴장하는 순간에 **화면의 인원수가 영상과 안 맞는다.**
    복원 인원수 곡선과 실제 관측 인원수 곡선의 상관이 최대가 되는 쪽으로 민다.
    """
    obs_t = [d["ts"] for d in snaps]
    obs_n = np.array([sum(1 for o in d["objects"] if o["cam_id"] == cam)
                      for d in snaps], float)
    if obs_n.std() == 0 or not people:
        return ref, 0.0
    rng = [(p["path"][0][0], p["path"][-1][0]) for p in people]
    best, br = 0.0, -2.0
    for sh in np.arange(-1.5, 1.5001, 0.02):
        mod = np.array([sum(1 for a, b in rng
                            if a <= ((t - ref + sh) % PERIOD) <= b) for t in obs_t], float)
        if mod.std() == 0:
            continue
        r = float(np.corrcoef(obs_n, mod)[0, 1])
        if r > br:
            best, br = float(sh), r
    return ref - best, br


def fold(snaps, cam, ref):
    """관측 → (위상, x, y, 바퀴번호) 점 목록."""
    pts = []
    for d in snaps:
        k = int((d["ts"] - ref) // PERIOD)
        ph = (d["ts"] - ref) % PERIOD
        for o in d["objects"]:
            if o["cam_id"] == cam:
                pts.append((ph, o["x"], o["y"], k, o["gid"]))
    return pts


def build(pts, gate_px, min_loops=2, min_net_px=0.0):
    """위상 격자를 훑으며 **사람 줄기**를 키운다.

    같은 위상 칸의 점들을, 직전 칸에서 이어온 줄기 중 가장 가까운 것에 붙인다.
    줄기 하나가 한 사람이고, 바퀴마다 한 점씩 붙으므로 칸마다 `바퀴수` 개가 모인다.
    그 평균이 그 위상에서의 자리다.
    """
    bins = defaultdict(list)
    for ph, x, y, k, g in pts:
        bins[int(ph / BIN)].append((x, y, k, g))
    stems: list[dict] = []          # {last:(x,y), b:마지막칸, pts:{칸:[(x,y,k)]}, loops:set}
    for b in sorted(bins):
        for x, y, k, g in bins[b]:
            best, bd = None, None
            for s in stems:
                if b - s["b"] > 6:                 # 0.6초 넘게 비면 다른 사람
                    continue
                d = math.hypot(x - s["last"][0], y - s["last"][1])
                # 비어 있던 칸 수만큼 게이트를 늘린다(그동안 움직였을 테니)
                if d <= gate_px * (1 + 0.5 * (b - s["b"])) and (bd is None or d < bd):
                    best, bd = s, d
            if best is None:
                best = {"last": (x, y), "b": b, "pts": defaultdict(list),
                        "loops": set()}
                stems.append(best)
            best["pts"][b].append((x, y, k))
            best["loops"].add(k)
            n = len(best["pts"][b])
            bx = sum(p[0] for p in best["pts"][b]) / n
            by = sum(p[1] for p in best["pts"][b]) / n
            best["last"], best["b"] = (bx, by), b
    out = []
    for s in stems:
        if len(s["loops"]) < min_loops:            # 한 바퀴만 나온 건 사람이 아니다
            continue
        path = []
        for b in sorted(s["pts"]):
            q = s["pts"][b]
            path.append([round(b * BIN, 2),
                         round(sum(p[0] for p in q) / len(q), 1),
                         round(sum(p[1] for p in q) / len(q), 1),
                         len(q)])
        if len(path) < 6:
            continue
        # **순이동**(시작→끝 직선거리)이 거의 없으면 사람이 아니라 제자리에서
        # 맴도는 오검출이다. 경로 길이로 거르면 지터가 쌓여 통과해 버린다
        # (실측: 3.3초 동안 경로 3.8m 인데 시작·끝이 0.26m 차이).
        if math.hypot(path[-1][1] - path[0][1], path[-1][2] - path[0][2]) < min_net_px:
            continue
        out.append({"path": path, "loops": sorted(s["loops"])})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--floor", default="default")
    ap.add_argument("--cams", nargs="+", default=["cam01", "cam02", "cam03"])
    ap.add_argument("--sec", type=float, default=95.0,
                    help="관측 시간(초). 주기 15.04초라 6바퀴 남짓")
    ap.add_argument("--gate-m", type=float, default=0.9,
                    help="한 위상 칸(0.1초) 안에서 같은 사람으로 볼 거리(m)")
    ap.add_argument("--min-loops", type=int, default=2,
                    help="이 바퀴 수 이상 나와야 사람으로 친다")
    ap.add_argument("--min-net-m", type=float, default=1.5,
                    help="시작→끝 **순이동**이 이보다 작으면 제자리 오검출로 버린다")
    ap.add_argument("--cap", default="", help="관측 대신 쓸 캡처 json")
    ap.add_argument("--out", default="results/floor_viz/_loop/default.json")
    a = ap.parse_args()

    site = api(f"/api/site?floor={a.floor}")
    fl = next(f for f in site["floors"] if f["id"] == a.floor)
    sc = fl["map"]["scale"]
    mpp = sc["meters"] / math.hypot(sc["p2"][0] - sc["p1"][0],
                                    sc["p2"][1] - sc["p1"][1])
    if a.cap:
        snaps = json.loads(Path(a.cap).read_text())
    else:
        print(f"[fold] {a.sec:.0f}초 관측 (주기 {PERIOD}s → {a.sec / PERIOD:.1f}바퀴)…")
        snaps = capture(a.floor, a.sec)
    print(f"[fold] 스냅샷 {len(snaps)}개 · {snaps[-1]['ts'] - snaps[0]['ts']:.1f}초")

    res = {"period": PERIOD, "m_per_px": mpp, "cams": {}}
    for cam in a.cams:
        ref = phase_ref(snaps, cam)
        if ref is None:
            print(f"  {cam}: 되감기를 못 찾음 — 건너뜀")
            continue
        pts = fold(snaps, cam, ref)
        people = build(pts, a.gate_m / mpp, a.min_loops, a.min_net_m / mpp)
        ngid = len({p[4] for p in pts})
        ref, r = refine(snaps, cam, ref, people)
        res["cams"][cam] = {"ref": ref, "people": people}
        print(f"  {cam}: 원 gid {ngid}개 → **복원 {len(people)}명** "
              f"(바퀴별 지지 {[len(p['loops']) for p in people]}) · 위상 상관 {r:.3f}")
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print(f"[fold] 저장 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
