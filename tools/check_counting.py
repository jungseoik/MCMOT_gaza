# -*- coding: utf-8 -*-
"""**출구 카운팅 채점** — 리허설 패키지의 정답지와 엔진 측정값을 대조한다.

정답은 사람이 원본 영상을 보고 확정한 값이고, `answers.json` 에 있다. 이 스크립트는
세션을 리플레이로 재계산해 그 정답과 맞춰 본다 — 카운팅 로직을 손볼 때마다
**한 줄로 전수 확인**할 수 있어야 회귀를 놓치지 않는다.

    python tools/check_counting.py                      # 기본 패키지(aihub/drill-1f3f)
    python tools/check_counting.py --answers <경로>
    python tools/check_counting.py --live               # 녹화 당시 집계본과도 비교

정답지 형식(`media/vsource/<site>/<set>/answers.json`):

    {"scenarios": [{"id","session","floor","total","by_exit":{"exit-0":3,…}}, …],
     "common_issues": [...]}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from system.metrics.replay import run_replay          # noqa: E402
from tools.session_viz import find_session            # noqa: E402

DEFAULT = ROOT / "media/vsource/aihub/drill-1f3f/answers.json"


def measured(session: str, floor: str, overrides: dict | None = None):
    db, _fl = find_session(session, floor)
    _res, tl, _fr, _m = run_replay(db, overrides or {}, fps=5.0)
    if not tl:
        return {}, 0
    last = tl[-1].model_dump() if hasattr(tl[-1], "model_dump") else tl[-1]
    ec = last.get("exit_counts") or {}
    return ec, sum(ec.values())


def live_counts(session: str, floor: str):
    """녹화 당시 집계본(<session>.json) — 실시간으로 센 값."""
    p = ROOT / "data/sites/default/sessions" / floor / f"{session}.json"
    if not p.is_file():
        return None, None
    r = (json.loads(p.read_text(encoding="utf-8")).get("result") or {})
    ec = {e["exit_id"]: e.get("actual_count", 0) for e in (r.get("exit_metrics") or [])}
    return ec, sum(ec.values())


def main() -> int:
    ap = argparse.ArgumentParser(description="출구 카운팅 채점")
    ap.add_argument("--answers", default=str(DEFAULT))
    ap.add_argument("--live", action="store_true", help="녹화 당시 집계본과도 비교")
    ap.add_argument("--only", default="", help="시나리오 id 만(콤마)")
    a = ap.parse_args()

    ans = json.loads(Path(a.answers).read_text(encoding="utf-8"))
    want = {x.strip() for x in a.only.split(",") if x.strip()}
    rows, bad = [], 0
    hdr = f"{'':4}{'정답':>6}{'측정':>6}"
    if a.live:
        hdr += f"{'집계본':>8}"
    print(f"{ans.get('package','')} — {ans.get('note','')[:60]}\n")
    print(hdr + "   출구별")
    print("-" * (len(hdr) + 34))
    for sc in ans["scenarios"]:
        if want and sc["id"] not in want:
            continue
        try:
            ec, tot = measured(sc["session"], sc["floor"])
        except Exception as e:                       # 세션이 지워졌을 수 있다
            print(f"  {sc['id']}  — 측정 실패: {e}")
            bad += 1
            continue
        ok = tot == sc["total"]
        if sc.get("by_exit"):
            ok = ok and all(ec.get(k, 0) == v for k, v in sc["by_exit"].items())
        if not ok:
            bad += 1
        line = f"  {sc['id']}{sc['total']:6}{tot:6}"
        if a.live:
            _lec, ltot = live_counts(sc["session"], sc["floor"])
            line += f"{(ltot if ltot is not None else '—'):>8}"
        print(f"{line}   {dict(sorted(ec.items()))} {'✓' if ok else '✗'}")
        rows.append(ok)
    print(f"\n  {sum(rows)}/{len(rows)} 일치" + ("" if not bad else f" · 어긋남 {bad}건"))
    for ci in (ans.get("common_issues") or []):
        print(f"\n  ⚠ 공통 — {ci['what']} ({', '.join(ci.get('where') or [])})")
        print(f"     {ci.get('detail','')[:100]}")
        print(f"     상태: {ci.get('status','')}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
