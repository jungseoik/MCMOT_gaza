#!/usr/bin/env python3
"""디폴트 세팅(seed) 버전 보관 — 저장 · 목록 · 복원.

`data/seed/<site>/` 는 [Reset] 버튼(POST /api/site/reset-seed)이 복원하는
디폴트 세팅이다. 한 벌뿐이라 이걸 바꾸면 이전 디폴트로 못 돌아간다.
이 스크립트는 그 시점의 seed 를 이름 붙여 `data/seed_versions/<이름>/` 에
보관하고, 나중에 그 이름으로 되돌린다.

  save     현재 seed(또는 --from live 로 라이브 사이트)를 새 버전으로 보관
  list     보관된 버전 목록
  show     한 버전의 상세(층·카메라·축척)
  restore  그 버전을 seed 로 되돌림. --apply 를 주면 라이브까지 즉시 복원
           (운영서버 /api/site/reset-seed 호출 = [Reset] 버튼과 동일)

사용:
  python tools/seed_version.py save v1 --note "삼성화재 PoC 초기 3층 구성"
  python tools/seed_version.py list
  python tools/seed_version.py restore v1            # seed 만 교체
  python tools/seed_version.py restore v1 --apply    # 라이브까지 복원

세션 녹화본(sessions/)은 어느 명령에서도 건드리지 않는다.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "data" / "seed"
STORE = REPO / "data" / "seed_versions"
LIVE = REPO / "data" / "sites"
API = "http://127.0.0.1:8900"
SKIP = {"sessions", "MANIFEST.json"}


# ────────────────────────────────────────────────────────── 요약
def summarize(root: Path) -> dict:
    """site.json + cameras/ 에서 사람이 확인할 정보만 뽑는다."""
    sj = root / "site.json"
    if not sj.is_file():
        return {}
    cfg = json.loads(sj.read_text())
    floors = []
    for fl in cfg.get("floors", []):
        m = fl.get("map") or {}
        sc = m.get("scale")
        mpp = m.get("m_per_px")
        if mpp is None and sc:
            d = ((sc["p2"][0] - sc["p1"][0]) ** 2 + (sc["p2"][1] - sc["p1"][1]) ** 2) ** 0.5
            mpp = sc["meters"] / d if d else None
        floors.append({
            "id": fl["id"], "name": fl.get("name", ""),
            "map": m.get("image"), "px": [m.get("w"), m.get("h")] if m else None,
            "m_per_px": round(mpp, 5) if mpp else None,
            "scale_src": ("CAD 자동" if m.get("m_per_px") is not None
                          else ("수동 2점" if sc else "없음")),
            "routes": len(fl.get("routes", [])), "zones": len(fl.get("zones", [])),
            "bottlenecks": len(fl.get("bottlenecks", [])), "exits": len(fl.get("exits", [])),
        })
    cams = []
    for p in sorted((root / "cameras").glob("*.json")) if (root / "cameras").is_dir() else []:
        c = json.loads(p.read_text())
        cams.append({"cam_id": c.get("cam_id"), "name": c.get("name", ""),
                     "floor_id": c.get("floor_id"), "rtsp": c.get("rtsp", ""),
                     "mapped": c.get("mapping") is not None,
                     "enabled": c.get("enabled", True)})
    return {"site_id": cfg.get("site_id"), "version": cfg.get("version"),
            "floors": floors, "cameras": cams}


def copy_tree(src: Path, dst: Path) -> None:
    """sessions/ 를 뺀 전체 복사 (대상 내용은 먼저 비운다)."""
    if dst.exists():
        for item in dst.iterdir():
            if item.name in SKIP:
                continue
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in SKIP:
            continue
        tgt = dst / item.name
        shutil.copytree(item, tgt, dirs_exist_ok=True) if item.is_dir() \
            else shutil.copy2(item, tgt)


# ────────────────────────────────────────────────────────── 명령
def cmd_save(a) -> int:
    src = (LIVE / a.site) if a.source == "live" else (SEED / a.site)
    if not (src / "site.json").is_file():
        print(f"오류: {src}/site.json 없음", file=sys.stderr)
        return 1
    dst = STORE / a.name
    if dst.exists() and not a.force:
        print(f"오류: 이미 있는 버전 '{a.name}' — 덮어쓰려면 --force", file=sys.stderr)
        return 1
    copy_tree(src, dst)
    man = {"name": a.name, "note": a.note or "",
           "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "source": a.source, "site": a.site, "summary": summarize(dst)}
    (dst / "MANIFEST.json").write_text(json.dumps(man, ensure_ascii=False, indent=2))
    size = sum(p.stat().st_size for p in dst.rglob("*") if p.is_file()) / 1e6
    print(f"저장 완료: {dst}  ({size:.1f} MB · {a.source})")
    _print_summary(man["summary"])
    print("\n※ git 에 커밋해야 다른 서버에서도 복원할 수 있습니다.")
    return 0


def cmd_list(a) -> int:
    if not STORE.is_dir() or not any(STORE.iterdir()):
        print("보관된 버전 없음 — python tools/seed_version.py save v1")
        return 0
    print(f"{'이름':12} {'저장시각':20} {'층':>3} {'카메라':>5}  비고")
    for d in sorted(STORE.iterdir()):
        if not d.is_dir():
            continue
        mf = d / "MANIFEST.json"
        m = json.loads(mf.read_text()) if mf.is_file() else {}
        s = m.get("summary", {})
        print(f"{d.name:12} {m.get('saved_at', '?')[:19]:20} "
              f"{len(s.get('floors', [])):>3} {len(s.get('cameras', [])):>5}  "
              f"{m.get('note', '')}")
    cur = summarize(SEED / a.site)
    print(f"\n현재 seed: 층 {len(cur.get('floors', []))} · "
          f"카메라 {len(cur.get('cameras', []))}  ([Reset] 시 복원되는 상태)")
    return 0


def _print_summary(s: dict) -> None:
    if not s:
        return
    print(f"\n  층 {len(s.get('floors', []))}개")
    for f in s.get("floors", []):
        px = f"{f['px'][0]}x{f['px'][1]}" if f.get("px") and f["px"][0] else "맵없음"
        print(f"    {f['id']:8} {f['name'][:8]:9} {px:12} "
              f"{f['m_per_px'] or '-':>9} m/px ({f['scale_src']})  "
              f"경로{f['routes']} 구역{f['zones']} 병목{f['bottlenecks']} 출입구{f['exits']}")
    print(f"  카메라 {len(s.get('cameras', []))}대")
    for c in s.get("cameras", []):
        print(f"    {c['cam_id']:7} {c['name'][:14]:16} floor={str(c['floor_id']):8} "
              f"매핑={'O' if c['mapped'] else '✕'}")


def cmd_show(a) -> int:
    d = STORE / a.name
    mf = d / "MANIFEST.json"
    if not mf.is_file():
        print(f"오류: 버전 '{a.name}' 없음", file=sys.stderr)
        return 1
    m = json.loads(mf.read_text())
    print(f"{m['name']} — {m.get('note', '')}\n저장 {m['saved_at']} (from {m['source']})")
    _print_summary(m.get("summary", {}))
    return 0


def cmd_restore(a) -> int:
    d = STORE / a.name
    if not (d / "site.json").is_file():
        print(f"오류: 버전 '{a.name}' 없음 (또는 site.json 누락)", file=sys.stderr)
        return 1
    # 되돌리기 전 현재 seed 를 자동 보관 — 실수로 잃는 일을 막는다
    if not a.no_backup:
        auto = f"auto-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        copy_tree(SEED / a.site, STORE / auto)
        (STORE / auto / "MANIFEST.json").write_text(json.dumps(
            {"name": auto, "note": f"restore {a.name} 직전 자동 보관",
             "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
             "source": "seed", "site": a.site,
             "summary": summarize(STORE / auto)}, ensure_ascii=False, indent=2))
        print(f"직전 seed 자동 보관: {auto}")

    copy_tree(d, SEED / a.site)
    print(f"seed 복원 완료: {a.name} → data/seed/{a.site}")
    if not a.apply:
        print("라이브에는 아직 반영 안 됨 — UI [Reset] 을 누르거나 --apply 로 실행하세요.")
        return 0
    try:
        req = urllib.request.Request(f"{API}/api/site/reset-seed", method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            cfg = json.load(r)
        print(f"라이브 반영 완료 — 층 {[f['id'] for f in cfg.get('floors', [])]}")
    except Exception as e:
        print(f"라이브 반영 실패({type(e).__name__}: {e}) — 서버가 떠 있으면 "
              f"UI [Reset] 을 누르세요.", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="디폴트 세팅(seed) 버전 보관·복원")
    ap.add_argument("--site", default="default", help="사이트 id (기본 default)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("save", help="현재 세팅을 버전으로 보관")
    p.add_argument("name", help="버전 이름 (예: v1)")
    p.add_argument("--note", help="한 줄 설명")
    p.add_argument("--source", choices=("seed", "live"), default="seed",
                   help="seed=현재 디폴트(기본) / live=지금 돌고 있는 사이트")
    p.add_argument("--force", action="store_true", help="같은 이름 덮어쓰기")
    p.set_defaults(func=cmd_save)

    p = sub.add_parser("list", help="보관된 버전 목록")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="버전 상세")
    p.add_argument("name")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("restore", help="그 버전을 디폴트(seed)로 되돌림")
    p.add_argument("name")
    p.add_argument("--apply", action="store_true",
                   help="라이브까지 즉시 복원 (= UI [Reset])")
    p.add_argument("--no-backup", action="store_true",
                   help="직전 seed 자동 보관 생략")
    p.set_defaults(func=cmd_restore)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
