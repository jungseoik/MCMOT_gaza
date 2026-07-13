"""
evac.cli — 커맨드라인 진입점.

서브커맨드
  route    : 경로 산출 + PNG 렌더 (Exit는 DXF레이어/--exits/--exits-json 중 하나)
  pick     : 맵 띄워 Exit 클릭 → JSON 저장(+선택: DXF 역주입) (GUI 필요)
  connect  : 보행공간 연결성 진단 PNG (도면 품질 점검)

예)
  python -m evac.cli route --dxf Egress_Review-Test.dxf --out out.png --show-ref
  python -m evac.cli route --dxf plan.dxf --exits "10,5;60,65" --mode worstn --worst-n 8
  python -m evac.cli pick  --dxf plan.dxf --n 2 --out-json exits.json --write-exits-dxf plan_tagged.dxf
  python -m evac.cli connect --dxf 17F.dxf --out conn.png
"""
import argparse
import sys

from . import cad, core, render


def _parse_exits_m(s):
    out = []
    for tok in s.replace(" ", "").split(";"):
        if tok:
            x, y = tok.split(","); out.append((float(x), float(y)))
    return out


def _resolve_exits(dxf, args):
    """우선순위: --exits-json > --exits(미터) > DXF의 Evac_Exit 레이어."""
    if getattr(args, "exits_json", None):
        return [dxf.m_to_world(x, y) for x, y in cad.load_exits_json(args.exits_json)]
    if getattr(args, "exits", None):
        return [dxf.m_to_world(x, y) for x, y in _parse_exits_m(args.exits)]
    return list(dxf.exits)


def cmd_route(args):
    dxf = cad.load_dxf(args.dxf)
    print(f"[load] {args.dxf}  장애물 {len(dxf.obstacles)} · Exit(DXF) {len(dxf.exits)} "
          f"· Occupant {len(dxf.occupants)}")
    exits = _resolve_exits(dxf, args)
    if not exits:
        sys.exit("Exit 없음 — DXF의 Evac_Exit, 또는 --exits / --exits-json 로 지정.")
    print(f"[exits] {len(exits)}개 사용")

    if args.mode == "occupant":
        starts = ([dxf.m_to_world(x, y) for x, y in _parse_exits_m(args.starts)]
                  if args.starts else list(dxf.occupants))
        if not starts:
            sys.exit("occupant 모드: 출발점 없음 — DXF Evac_Occupant 또는 --starts 지정(또는 --mode worstn).")
    else:
        starts = None

    an = core.analyze(dxf.obstacles, exits, dxf.bounds, starts=starts, mode=args.mode,
                      worst_n=args.worst_n, cell=args.cell, clearance=args.clearance,
                      threshold_mm=args.threshold_m * 1000.0)
    n_pass = sum(p["is_pass"] for p in an.paths)
    print(f"[grid] {an.cols}x{an.rows} · 통행 {an.n_free:,}/벽 {an.n_wall:,}")
    print(f"[result] 경로 {len(an.paths)} · Pass {n_pass} · Fail {len(an.paths)-n_pass} · 도달불가 {an.skipped}")
    for i, p in enumerate(sorted(an.paths, key=lambda x: -x["dist_mm"])):
        print(f"   #{i+1:2d} {p['dist_mm']/1000:6.1f}m [{'PASS' if p['is_pass'] else 'FAIL'}] "
              f"start=({p['start_m'][0]:.0f},{p['start_m'][1]:.0f})")

    ref = None; extra = ""
    if args.show_ref:
        ref = cad.load_reference_paths(dxf.doc)
        if ref["pass"] or ref["fail"]:
            extra = f" · 원본 {len(ref['pass'])}P/{len(ref['fail'])}F"
            print(f"[ref] 매크로 원본: Pass {len(ref['pass'])} · Fail {len(ref['fail'])}")

    if args.write_exits_dxf:
        cad.write_exits_dxf(args.dxf, args.write_exits_dxf, exits)
        print(f"[dxf] Exit 역주입 → {args.write_exits_dxf} (Evac_Exit 레이어)")

    render.render_routes(args.out, dxf.obstacles, dxf.bounds, an, exits=exits, ref=ref,
                         threshold_mm=args.threshold_m * 1000.0, dpi=args.dpi, title_extra=extra)
    print(f"[ok] {args.out}")


def cmd_pick(args):
    from . import pick
    dxf = cad.load_dxf(args.dxf)
    if args.html:
        pick.make_html(dxf, args.html)
        print(f"[html] 브라우저 피커 생성 → {args.html}")
        print("  → 브라우저로 열어 Exit를 클릭하고 '명령어 복사' 후 route에 붙여넣기.")
        return
    try:
        exits_m = pick.pick_points(dxf, n=args.n, kind="Exit")
    except RuntimeError as e:
        sys.exit(f"[pick 불가] {e}\n  (디스플레이 없는 서버면 `pick --html out.html` 로 브라우저 피커 생성)")
    print(f"[pick] {len(exits_m)}개: " + " ".join(f"({x:.1f},{y:.1f})" for x, y in exits_m))
    if args.out_json:
        cad.save_exits_json(args.out_json, exits_m, meta={"src": args.dxf})
        print(f"[json] {args.out_json}")
    if args.write_exits_dxf:
        exits_w = [dxf.m_to_world(x, y) for x, y in exits_m]
        cad.write_exits_dxf(args.dxf, args.write_exits_dxf, exits_w)
        print(f"[dxf] Evac_Exit 역주입 → {args.write_exits_dxf}")


def cmd_connect(args):
    dxf = cad.load_dxf(args.dxf)
    conn = core.connectivity(dxf.obstacles, dxf.bounds, cell=args.cell, clearance=args.clearance)
    print(f"[connect] 연결영역 {conn['n_components']}개 · 최대영역 {100*conn['largest_frac']:.1f}% of free")
    render.render_connectivity(args.out, dxf.obstacles, dxf.bounds, conn, dpi=args.dpi)
    print(f"[ok] {args.out}")


def build_parser():
    ap = argparse.ArgumentParser(prog="evac", description="피난경로 산출(삼성 매크로 파이썬 포팅)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--dxf", required=True)
        p.add_argument("--cell", type=float, default=core.CELL_SIZE)
        p.add_argument("--clearance", type=float, default=core.CLEARANCE)
        p.add_argument("--dpi", type=int, default=200)

    r = sub.add_parser("route", help="경로 산출 + 렌더")
    common(r)
    r.add_argument("--out", default="evac_result.png")
    r.add_argument("--mode", choices=["occupant", "worstn"], default="occupant")
    r.add_argument("--worst-n", type=int, default=5)
    r.add_argument("--threshold-m", type=float, default=30.0)
    r.add_argument("--exits", default=None, help="'xm,ym;...' SW코너=0 미터")
    r.add_argument("--exits-json", default=None)
    r.add_argument("--starts", default=None, help="occupant 출발점 'xm,ym;...' 미터")
    r.add_argument("--show-ref", action="store_true")
    r.add_argument("--write-exits-dxf", default=None, help="사용한 Exit를 DXF에 역주입 저장")
    r.set_defaults(func=cmd_route)

    p = sub.add_parser("pick", help="맵 띄워 Exit 클릭(GUI)")
    common(p)
    p.add_argument("--n", type=int, default=None, help="클릭 개수(미지정=Enter까지)")
    p.add_argument("--out-json", default="exits.json")
    p.add_argument("--write-exits-dxf", default=None)
    p.add_argument("--html", default=None, help="GUI 대신 브라우저 클릭 피커 HTML 생성")
    p.set_defaults(func=cmd_pick)

    c = sub.add_parser("connect", help="연결성 진단(도면 품질)")
    common(c)
    c.add_argument("--out", default="connectivity.png")
    c.set_defaults(func=cmd_connect)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
