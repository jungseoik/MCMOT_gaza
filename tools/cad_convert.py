#!/usr/bin/env python3
"""
cad_convert.py — CAD 변환 파이프라인(재현용). DWG↔DXF + DXF→PNG(도면/척도).

이 프로젝트의 도면 처리(17F.dwg → 17F.dxf → 17F_plan*.png)를 그대로 재현한다.
설치 전제·상세는 스킬 문서 참조: .claude/skills/cad-convert/SKILL.md

서브명령
  dwg2dxf : ODAFileConverter(GUI 앱)를 xvfb 헤드리스로 돌려 DWG→DXF(또는 반대) 변환.
  dxf2png : ezdxf+matplotlib로 DXF를 (1)깨끗한 도면 (2)미터 척도/격자/스케일바 PNG로 렌더.

예)
  python tools/cad_convert.py dwg2dxf --in cad/17F.dwg --out cad/
  python tools/cad_convert.py dxf2png --dxf cad/17F.dxf --out-prefix cad/17F_plan
"""
import argparse, os, shutil, subprocess, sys, tempfile

KRFONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
ODA_BIN = "ODAFileConverter"   # apt로 설치되면 /usr/bin/ODAFileConverter


# ---------------------------------------------------------------- DWG -> DXF
def dwg2dxf(args):
    """ODAFileConverter는 '폴더 단위' GUI 앱. 입력 파일을 임시 입력폴더에 넣고,
    xvfb-run으로 헤드리스 실행한다.
    CLI: ODAFileConverter <inDir> <outDir> <outVer> <outType> <recurse> <audit> [filter]
    """
    if not shutil.which(ODA_BIN):
        sys.exit(f"'{ODA_BIN}' 없음 — 스킬 문서의 설치 절차를 먼저 수행하라.")
    if not shutil.which("xvfb-run"):
        sys.exit("'xvfb-run' 없음 — apt install xvfb 필요.")
    src = os.path.abspath(args.infile)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    ext = os.path.splitext(src)[1].lower()
    out_type = "DXF" if ext == ".dwg" else "DWG"
    filt = "*.dwg" if ext == ".dwg" else "*.dxf"
    with tempfile.TemporaryDirectory() as tin:
        shutil.copy(src, tin)
        cmd = ["xvfb-run", "-a", ODA_BIN, tin, out_dir,
               args.out_ver, out_type, "0", "1", filt]
        print("[run]", " ".join(cmd))
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.stdout.strip():
            print(r.stdout.strip())
        if r.stderr.strip():
            print(r.stderr.strip(), file=sys.stderr)
    base = os.path.splitext(os.path.basename(src))[0]
    produced = os.path.join(out_dir, base + ("." + out_type.lower()))
    if os.path.exists(produced):
        print(f"[ok] {produced}  ({os.path.getsize(produced)//1024} KB)")
    else:
        sys.exit("[fail] 변환 산출물이 없음 — ODA 버전/출력버전 인자 확인.")


# ---------------------------------------------------------------- DXF -> PNG
def _load_segments(dxf_path):
    import numpy as np
    import ezdxf
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    segs = []

    def add_poly(pts, closed=False):
        pts = [(p[0], p[1]) for p in pts]
        if closed and len(pts) >= 2:
            pts = pts + [pts[0]]
        for a, b in zip(pts[:-1], pts[1:]):
            segs.append((a[0], a[1], b[0], b[1]))

    for e in msp:
        t = e.dxftype()
        if t == "LWPOLYLINE":
            add_poly(list(e.get_points()), e.closed)
        elif t == "LINE":
            segs.append((e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y))
        elif t in ("ARC", "CIRCLE", "ELLIPSE", "SPLINE"):
            try:
                pts = list(e.flattening(distance=20))
                add_poly([(p.x, p.y) for p in pts], closed=(t == "CIRCLE"))
            except Exception:
                pass
    arr = np.array(segs, dtype=float)
    h = doc.header
    (xmin, ymin, _), (xmax, ymax, _) = h.get("$EXTMIN"), h.get("$EXTMAX")
    return arr, (xmin, ymin, xmax, ymax)


def dxf2png(args):
    import numpy as np
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    from matplotlib.collections import LineCollection
    from matplotlib.ticker import MultipleLocator, FuncFormatter
    try:
        fm.fontManager.addfont(KRFONT)
        plt.rcParams["font.family"] = fm.FontProperties(fname=KRFONT).get_name()
    except Exception:
        pass

    segs, ext = _load_segments(args.dxf)
    xmin, ymin, xmax, ymax = ext
    Wm, Hm = (xmax - xmin) / 1000, (ymax - ymin) / 1000
    lines = [[(s[0], s[1]), (s[2], s[3])] for s in segs]
    print(f"[load] segments={len(segs)}  전체 {Wm:.1f} m x {Hm:.1f} m")

    # (1) 깨끗한 도면
    fig = plt.figure(figsize=(16, 16 * Hm / Wm)); ax = fig.add_axes([0, 0, 1, 1])
    ax.add_collection(LineCollection(lines, colors="#222", linewidths=0.25))
    ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax); ax.set_aspect("equal"); ax.axis("off")
    p1 = args.out_prefix + ".png"
    fig.savefig(p1, dpi=args.dpi, facecolor="white", bbox_inches="tight", pad_inches=0)
    plt.close(fig)

    # (2) 미터 척도/격자/스케일바
    G = args.grid_m * 1000
    fig = plt.figure(figsize=(17, 17 * Hm / Wm)); ax = fig.add_axes([0.06, 0.06, 0.92, 0.90])
    ax.add_collection(LineCollection(lines, colors="#444", linewidths=0.25))
    ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax); ax.set_aspect("equal")
    ax.xaxis.set_major_locator(MultipleLocator(G)); ax.yaxis.set_major_locator(MultipleLocator(G))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{(v-xmin)/1000:.0f}"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{(v-ymin)/1000:.0f}"))
    for gx in np.arange(0, Wm * 1000 + 1, G): ax.axvline(xmin + gx, color="#1f77ff", lw=0.5, alpha=0.30)
    for gy in np.arange(0, Hm * 1000 + 1, G): ax.axhline(ymin + gy, color="#1f77ff", lw=0.5, alpha=0.30)
    ax.set_xlabel("X (m, 남서코너=0)", fontsize=15); ax.set_ylabel("Y (m, 남서코너=0)", fontsize=15)
    ax.set_title(f"평면도 · 척도(미터) · 격자 {args.grid_m:.0f} m · 전체 {Wm:.1f} m × {Hm:.1f} m", fontsize=17)
    bx, by = xmin + 3000, ymin + 2500
    ax.plot([bx, bx + 10000], [by, by], "-", color="k", lw=4, solid_capstyle="butt")
    ax.text(bx + 5000, by + 1200, "10 m", ha="center", fontsize=14, weight="bold")
    for k in range(11):
        ax.plot([bx + k * 1000] * 2, [by - 400, by + 400], "-", color="k", lw=1)
    p2 = args.out_prefix + "_scale.png"
    fig.savefig(p2, dpi=args.dpi, facecolor="white"); plt.close(fig)
    print(f"[ok] {p1}\n[ok] {p2}")


def main():
    ap = argparse.ArgumentParser(description="CAD 변환 파이프라인(재현용)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("dwg2dxf", help="ODAFileConverter 헤드리스 DWG↔DXF")
    a.add_argument("--in", dest="infile", required=True, help="입력 .dwg 또는 .dxf")
    a.add_argument("--out", required=True, help="출력 폴더")
    a.add_argument("--out-ver", default="ACAD2018", help="출력 버전(기본 ACAD2018)")
    a.set_defaults(func=dwg2dxf)

    b = sub.add_parser("dxf2png", help="DXF → 도면 PNG + 척도 PNG")
    b.add_argument("--dxf", required=True)
    b.add_argument("--out-prefix", required=True, help="예: cad/17F_plan → *.png, *_scale.png")
    b.add_argument("--grid-m", type=float, default=5.0, help="격자 간격 m(기본 5)")
    b.add_argument("--dpi", type=int, default=200)
    b.set_defaults(func=dxf2png)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
