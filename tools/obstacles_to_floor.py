# -*- coding: utf-8 -*-
"""**장애물 표시(:8920) → 도면 메타에 반영** — 발표용 표시를 운영 경로 산출에 얹는다.

:8920 에서 찍은 장애물은 `results/demo/_annotate/obstacles_<층>.json` 에만 있고
발표 영상에서만 쓰였다. 그런데 그 자리는 실제로 사람이 못 지나가는 곳이므로,
**자동 피난경로 산출도 같은 것을 피해야** 한다.

`data/sites/default/floor_<층>.json` 의 `shapes` 에 `op: "block"` 으로 넣는다 —
그 키를 읽는 곳은 `system/evac/person_routes._edits_from_floor_json()` 이고,
거기서 막기 도형으로 건너간다.

좌표계가 다르다: 장애물은 **맵 px**, 도면 메타는 **도면 mm**. 같은 파일의
`bounds_mm`·`map_px` 가 변환의 정본이다(person_routes 의 to_px 와 정확히 역).

    python tools/obstacles_to_floor.py --floor floor4 --apply
    python tools/obstacles_to_floor.py --all --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SITE = ROOT / "data/sites/default"
ANN = ROOT / "results/demo/_annotate"
TAG = "obstacle:8920"          # 우리가 넣은 도형임을 표시 — 다시 돌려도 중복되지 않게


def floor_meta_path(floor: str) -> Path:
    return SITE / ("floor.json" if floor == "default" else f"floor_{floor}.json")


def convert(floor: str, apply: bool = False) -> int:
    op = ANN / f"obstacles_{floor}.json"
    if not op.is_file():
        print(f"  {floor}: 장애물 파일 없음 — 건너뜀")
        return 0
    raw = json.loads(op.read_text(encoding="utf-8"))
    obs = raw if isinstance(raw, list) else (raw.get("obstacles") or [])
    fp = floor_meta_path(floor)
    if not fp.is_file():
        print(f"  {floor}: 도면 메타 없음({fp.name}) — 건너뜀")
        return 0
    meta = json.loads(fp.read_text(encoding="utf-8"))
    minx, miny, maxx, maxy = meta["bounds_mm"]
    w_px, h_px = meta["map_px"]

    def to_mm(x, y):
        return [minx + x / w_px * (maxx - minx),
                maxy - y / h_px * (maxy - miny)]

    shapes = [s for s in (meta.get("shapes") or []) if s.get("src") != TAG]
    n0 = len(meta.get("shapes") or [])
    for i, o in enumerate(obs):
        pts = []
        for x, y in o["points"]:
            pts.extend(to_mm(float(x), float(y)))
        if len(pts) < 6:                       # 3점 미만은 면이 아니다
            continue
        shapes.append({"op": "block", "kind": "poly", "pts": pts,
                       "src": TAG, "tag": o.get("tag", ""),
                       "name": f"장애물 {i + 1}"})
    added = len(shapes) - len([s for s in (meta.get("shapes") or [])
                               if s.get("src") != TAG])
    print(f"  {floor}: 장애물 {len(obs)}개 → shapes {n0} → {len(shapes)}개 (추가 {added})")
    if apply:
        bak = fp.with_suffix(".json.bak")
        if not bak.exists():
            shutil.copy2(fp, bak)
        meta["shapes"] = shapes
        fp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"      → {fp.name} 반영 (원본 {bak.name})")
    return added


def main() -> int:
    ap = argparse.ArgumentParser(description="장애물 표시 → 도면 메타 shapes")
    ap.add_argument("--floor", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true", help="없으면 미리보기만")
    a = ap.parse_args()
    floors = ([p.stem.replace("obstacles_", "")
               for p in sorted(ANN.glob("obstacles_*.json"))] if a.all
              else [a.floor])
    if not floors or not floors[0]:
        ap.error("--floor <층> 또는 --all")
    tot = sum(convert(f, a.apply) for f in floors)
    if not a.apply:
        print("\n  (미리보기 — 반영하려면 --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
