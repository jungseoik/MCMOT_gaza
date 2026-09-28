#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""사람이 그린 동선을 **실제 출구 통과 시각에 정렬**한다.

편집기에서 [출구 시각에 맞추기]를 사람마다 누르지 않았어도, 여기서 한 번에
맞춘다. 하는 일은 둘뿐이다.

  ① 통과 시각 배정 — **찍은 끝 시각 순서를 그대로 지켜** 배정한다. P01→첫 번째로
     고정하면 먼저 도착하게 그려 둔 사람이 뒤로 밀려 서로 추월한다.
  ② 시각 늘이기 — 시작은 그대로 두고 **마지막 점이 그 통과 시각에** 오도록
     전체를 비례로 늘인다. 자리는 건드리지 않는다 — 그린 사람의 선이 정본이다.

    python tools/demo_paths_fit.py --session <sess-id> [--apply]
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from system.config.schema import SiteConfig          # noqa: E402
from system.metrics import recorder                  # noqa: E402
from system.metrics.replay import run_replay         # noqa: E402
from tools import demo_ghosts as dghost              # noqa: E402
from tools import demo_paths as dp                   # noqa: E402
from tools.session_viz import find_session           # noqa: E402

V_MAX = 1.6            # 훈련 보행 속도 상한 — 이보다 빠르면 달리는 그림이 된다


def passage_times(sid: str, floor: str | None):
    db, fl = find_session(sid, floor)
    meta = recorder.load_meta(db)
    t0 = float(meta["alarm_ts"])
    gh = [g["key"] for g in dghost.load(sid)]
    _, tl, _, _ = run_replay(db, {"exclude_tracks": gh} if gh else {}, fps=5.0)
    out, prev = [], {}
    for p in tl:
        d = p.model_dump() if hasattr(p, "model_dump") else p
        for k, v in (d.get("exit_counts") or {}).items():
            n = v - prev.get(k, 0)
            if n > 0:
                out.extend([round(d["ts"] - t0, 1)] * n)
                prev[k] = v
    return sorted(out), fl


def main() -> int:
    ap = argparse.ArgumentParser(description="그린 동선 → 실제 통과 시각에 정렬")
    ap.add_argument("--session", required=True)
    ap.add_argument("--floor")
    ap.add_argument("--apply", action="store_true", help="파일에 반영(원본은 .bak)")
    a = ap.parse_args()

    p = dp.path(a.session)
    if not p.is_file():
        print(f"그린 동선이 없습니다 — {p}")
        return 1
    raw = json.loads(p.read_text(encoding="utf-8"))
    people = raw.get("people") or {}
    want, floor = passage_times(a.session, a.floor)
    print(f"실제 통과 시각 {len(want)}건: {want}")

    db, _ = find_session(a.session, floor)
    site = SiteConfig.model_validate(recorder.load_meta(db)["site_view"])
    mpp = site.map.resolve_m_per_px()

    order = sorted(people, key=lambda k: people[k][-1]["t"])
    if len(want) < len(order):
        print(f"통과 시각이 {len(want)}건뿐이라 {len(order)}명을 다 못 맞춥니다")
        return 1

    exit_at = dict(raw.get("exit_at") or {})
    print(f"\n{'':5} {'찍은 끝':>7} {'→ 통과':>7} {'늘임':>6} {'속도':>13}")
    for i, pid in enumerate(order):
        ks = people[pid]
        t_a, t_b = ks[0]["t"], ks[-1]["t"]
        ks_t0 = t_a
        tgt = want[i]
        if tgt <= t_a:
            print(f"{pid:5} 통과 {tgt}s 가 출발 {t_a}s 보다 빠릅니다 — 출발을 0 으로 당깁니다")
            ks[0]["t"] = t_a = max(0.0, tgt - 3.0)
        L = sum(math.hypot(b["x"] - c["x"], b["y"] - c["y"])
                for c, b in zip(ks, ks[1:])) * mpp
        v0 = L / max(0.1, t_b - t_a)
        # 늘일 여유가 없어 너무 빨라지면 **출발을 당긴다.** 각본상 10명은 경보
        # 순간부터 그 자리에 있었으니 일찍 나서는 건 이상하지 않다 — 반대로
        # 2.8 m/s 로 달리는 건 이상하다.
        need = L / V_MAX
        if tgt - t_a < need:
            t_a = max(0.0, tgt - need)
            ks[0]["t"] = min(ks[0]["t"], t_a)
        r = (tgt - t_a) / max(0.1, t_b - t_a)
        base = ks[0]["t"]
        span = max(0.1, t_b - base)
        r = (tgt - base) / span
        for q in ks:
            q["t"] = round(base + (q["t"] - base) * r, 2)
        exit_at[pid] = float(tgt)
        print(f"{pid:5} {t_b:7.1f} {tgt:7.1f} {r:6.2f}x "
              f"{v0:5.2f} → {L / max(0.1, tgt - base):4.2f} m/s"
              + (f"  (출발 {base:.1f}s 로 당김)" if base < ks_t0 else ""))

    raw["exit_at"] = exit_at
    if a.apply:
        shutil.copy2(p, p.with_suffix(".json.bak"))
        p.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n반영 → {p.relative_to(ROOT)} (원본 {p.name}.bak)")
    else:
        print("\n--apply 를 붙이면 파일에 반영합니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
