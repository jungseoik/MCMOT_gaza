#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""녹화 스냅샷에 빠진 **리허설 시나리오 출입구 오버라이드**를 채워 넣는다.

왜 필요한가
-----------
세션 녹화기가 `rt.site()` 원본을 스냅샷으로 저장했는데, 엔진은 실제로는
`_site_plus_rehearsal()` 뷰(시나리오별 `exit_overrides` 가 얹힌 것)로 돌았다.
그래서 ④ 리플레이 [재계산] 이 훈련 실행과 **다른 통과 인원**을 냈다
(AI hub 3층 1차 실측: 8건 중 2건 불일치 — s06 5/5→6/5, s07 10/0→10/2).

서버는 고쳤지만(_attach_recorder), **이미 녹화된 세션**은 스냅샷에 값이 없다.
이 도구가 그 값을 채워 넣는다 — 녹화 당시 **실제로 적용돼 있던 값**을 되돌려
놓는 것이지, 없던 설정을 새로 만드는 것이 아니다.

    python tools/backfill_session_overrides.py            # 무엇이 바뀌는지만 출력
    python tools/backfill_session_overrides.py --apply    # 실제로 기록
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from system.vsource import package as vpkg          # noqa: E402

SITE = ROOT / "data" / "sites" / "default"
VSRC = ROOT / "media" / "vsource"


def packages() -> list[dict]:
    out = []
    for man in sorted(VSRC.glob("*/*/rehearsal.json")):
        try:
            pkg = json.loads(man.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pkg["_root"] = str(man.parent)
        out.append(pkg)
    return out


CAM_RE = re.compile(r"^rh_(?:[^_]+_)?(cam\d+)$", re.I)


def find_overrides(label: str, cam_ids: set[str]) -> tuple[dict, str]:
    """세션 → 패키지·시나리오 → 출입구 오버라이드.

    패키지 특정은 **카메라 이름 일치도**로 한다. 네임스페이스(rh_<ns>_camN)는
    2026-09-17 에 도입돼 그 전 녹화에는 없다 — 네임스페이스만 믿으면 구 녹화에서
    엉뚱한 패키지를 집어 **없던 오버라이드를 심게 된다**(실측: 1·3층 s06 에
    rehearsal 의 값이 붙을 뻔했다).
    """
    m = re.search(r"(scenario_\d+|combo_\d+)", label or "")
    if not m:
        return {}, ""
    scen = m.group(1)
    want = {c.group(1).lower() for c in
            (CAM_RE.match(x) for x in cam_ids) if c}
    best = None
    for pkg in packages():
        sc = next((x for x in pkg.get("scenarios", []) if x.get("id") == scen), None)
        if sc is None:
            continue
        have = {st["cam"].lower() for st in (sc.get("streams") or [])}
        score = len(want & have) - len(want - have)      # 겹치는 만큼 +, 없는 만큼 −
        if best is None or score > best[0]:
            best = (score, pkg, scen)
    if best is None or best[0] <= 0:
        return {}, ""
    _, pkg, scen = best
    return vpkg.scenario_exit_overrides(pkg, scen), f"{pkg['id']}/{scen}"


def patch(db: Path, ov: dict, apply: bool) -> list[str]:
    """meta.site_view 의 출입구에 오버라이드를 얹는다. 바뀐 항목 설명을 돌려준다."""
    con = sqlite3.connect(str(db))
    try:
        row = con.execute("SELECT value FROM meta WHERE key='site_view'").fetchone()
        if not row:
            return []
        sv = json.loads(row[0])
        changed = []
        holders = [sv] + list(sv.get("floors") or [])
        for h in holders:
            for ex in (h.get("exits") or []):
                patch_ = ov.get(ex.get("id"))
                if not patch_:
                    continue
                for k, v in patch_.items():
                    if ex.get(k) != v:
                        msg = f"{ex['id']}.{k}: {ex.get(k)} → {v}"
                        if msg not in changed:      # 같은 출구가 사이트·층 양쪽에 있다
                            changed.append(msg)
                        ex[k] = v
        if changed and apply:
            con.execute("UPDATE meta SET value=? WHERE key='site_view'",
                        (json.dumps(sv, ensure_ascii=False),))
            con.commit()
        return changed
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="녹화 스냅샷에 시나리오 출입구 오버라이드 보정")
    ap.add_argument("--apply", action="store_true", help="실제로 기록 (없으면 미리보기)")
    a = ap.parse_args()

    n_ses = n_fix = 0
    for meta_f in sorted((SITE / "sessions" / "_drills").glob("*.json")):
        d = json.loads(meta_f.read_text(encoding="utf-8"))
        sid, label = d["session_id"], d.get("label") or ""
        for fdir in sorted((SITE / "sessions").glob("floor*")):
            db = fdir / f"{sid}.db"
            if not db.is_file():
                continue
            n_ses += 1
            con = sqlite3.connect(str(db))
            try:
                r = con.execute("SELECT value FROM meta WHERE key='cameras'").fetchone()
                cams = {c["cam_id"] for c in json.loads(r[0])} if r else set()
            finally:
                con.close()
            ov, where = find_overrides(label, cams)
            if not ov:
                continue
            ch = patch(db, ov, a.apply)
            if ch:
                n_fix += 1
                print(f"{label}  [{fdir.name}]  ({where})")
                for c in ch:
                    print(f"    {c}")
    print(f"\n세션 {n_ses}건 검사 · 보정 대상 {n_fix}건"
          + ("" if a.apply else "  — 미리보기입니다. --apply 로 기록하세요."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
