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
import argparse, json, os, shutil, subprocess, sys, tempfile

KRFONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
ODA_BIN = "ODAFileConverter"   # apt로 설치되면 /usr/bin/ODAFileConverter


# ---------------------------------------------------------------- DWG -> DXF
def _convert_oda(src, out_dir, out_ver):
    """ODAFileConverter(독점 프리웨어, GUI 폴더단위)를 xvfb 헤드리스로 실행.
    CLI: ODAFileConverter <inDir> <outDir> <outVer> <outType> <recurse> <audit> [filter]"""
    if not shutil.which("xvfb-run"):
        sys.exit("'xvfb-run' 없음 — tools/setup_cad_convert.sh 로 설치.")
    ext = os.path.splitext(src)[1].lower()
    out_type = "DXF" if ext == ".dwg" else "DWG"
    filt = "*.dwg" if ext == ".dwg" else "*.dxf"
    with tempfile.TemporaryDirectory() as tin:
        shutil.copy(src, tin)
        cmd = ["xvfb-run", "-a", ODA_BIN, tin, out_dir, out_ver, out_type, "0", "1", filt]
        print("[run:oda]", " ".join(cmd))
        r = subprocess.run(cmd, capture_output=True, text=True)
        for s in (r.stdout, r.stderr):
            if s and s.strip():
                print(s.strip())
    return out_type.lower()


def _convert_libredwg(src, out_dir):
    """libredwg(오픈소스, GPL)의 dwg2dxf — 단일파일, xvfb 불필요. DWG→DXF만."""
    if os.path.splitext(src)[1].lower() != ".dwg":
        sys.exit("libredwg 엔진은 DWG→DXF만 지원(역변환은 ODA 사용).")
    base = os.path.splitext(os.path.basename(src))[0]
    out = os.path.join(out_dir, base + ".dxf")
    cmd = ["dwg2dxf", "-o", out, src]
    print("[run:libredwg]", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.stderr and r.stderr.strip():
        print(r.stderr.strip())
    return "dxf"


def dwg2dxf(args):
    """엔진 선택: auto(ODA 있으면 ODA, 없으면 libredwg) / oda / libredwg."""
    src = os.path.abspath(args.infile)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    have_oda = bool(shutil.which(ODA_BIN))
    have_lib = bool(shutil.which("dwg2dxf"))
    engine = args.engine
    if engine == "auto":
        engine = "oda" if have_oda else ("libredwg" if have_lib else None)
    if engine == "oda" and not have_oda:
        sys.exit(f"'{ODA_BIN}' 없음 — tools/setup_cad_convert.sh (ODA_DEB=... ) 로 설치.")
    if engine == "libredwg" and not have_lib:
        sys.exit("'dwg2dxf'(libredwg) 없음 — tools/setup_cad_convert.sh 로 설치.")
    if engine is None:
        sys.exit("변환 엔진 없음 — tools/setup_cad_convert.sh 로 ODA 또는 libredwg 설치.")

    print(f"[engine] {engine}  (oda={have_oda}, libredwg={have_lib})")
    ext_out = (_convert_oda(src, out_dir, args.out_ver) if engine == "oda"
               else _convert_libredwg(src, out_dir))

    base = os.path.splitext(os.path.basename(src))[0]
    produced = os.path.join(out_dir, base + "." + ext_out)
    if os.path.exists(produced):
        print(f"[ok] {produced}  ({os.path.getsize(produced)//1024} KB)")
    else:
        sys.exit("[fail] 변환 산출물 없음 — 엔진/버전 인자 확인.")


# ---------------------------------------------------------------- DXF -> PNG
# $INSUNITS 코드 → 미터 환산계수(1 도면단위 = ? m)
_INSUNITS_TO_M = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0,
                  8: 2.54e-5, 9: 0.0254e-3, 14: 0.1, 13: 1e-6}


def _load_segments(dxf_path, units_override=None):
    """DXF → (segs(N,4), ext, to_m). 블록(INSERT) 재귀 전개, 단위 자동감지.
    to_m = 도면단위 1당 미터. units_override: 'mm/cm/m/in/ft' 또는 None."""
    from collections import deque
    import numpy as np
    import ezdxf
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    # --- 단위 결정
    if units_override:
        to_m = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "ft": 0.3048}[units_override]
        usrc = f"override={units_override}"
    else:
        code = doc.header.get("$INSUNITS", 0)
        to_m = _INSUNITS_TO_M.get(code)
        if to_m is None:
            to_m = 0.001; usrc = f"$INSUNITS={code} 미지원 → mm 가정"
        else:
            usrc = f"$INSUNITS={code}"
    flat = max(1e-6, 0.02 / to_m)      # 곡선 평탄화 정밀도 ≈ 2cm

    segs = []

    def add_poly(pts, closed=False):
        pts = [(p[0], p[1]) for p in pts]
        if closed and len(pts) >= 2:
            pts = pts + [pts[0]]
        for a, b in zip(pts[:-1], pts[1:]):
            segs.append((a[0], a[1], b[0], b[1]))

    def emit(e):
        t = e.dxftype()
        try:
            if t == "LWPOLYLINE":
                add_poly(list(e.get_points()), e.closed)
            elif t == "POLYLINE":
                add_poly([(p[0], p[1]) for p in e.points()], e.is_closed)
            elif t == "LINE":
                segs.append((e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y))
            elif t in ("ARC", "CIRCLE", "ELLIPSE", "SPLINE"):
                pts = list(e.flattening(distance=flat))
                add_poly([(p[0], p[1]) for p in pts], closed=(t == "CIRCLE"))
        except Exception:
            pass

    # --- INSERT(블록참조) 재귀 전개하며 순회
    stack = deque(msp)
    n_insert = 0
    guard = 0
    while stack:
        guard += 1
        if guard > 5_000_000:          # 폭주 방지
            break
        e = stack.popleft()
        if e.dxftype() == "INSERT":
            n_insert += 1
            try:
                for ve in e.virtual_entities():
                    stack.append(ve)
            except Exception:
                pass
            continue
        emit(e)

    arr = np.array(segs, dtype=float) if segs else np.zeros((0, 4))
    # --- 범위: 헤더 우선, 없거나 0크기면 형상에서 계산
    h = doc.header
    ext = None
    try:
        (xmin, ymin, _), (xmax, ymax, _) = h.get("$EXTMIN"), h.get("$EXTMAX")
        if xmax - xmin > 1e-6 and ymax - ymin > 1e-6:
            ext = (xmin, ymin, xmax, ymax)
    except Exception:
        pass
    if ext is None and len(arr):
        xs = np.r_[arr[:, 0], arr[:, 2]]; ys = np.r_[arr[:, 1], arr[:, 3]]
        ext = (xs.min(), ys.min(), xs.max(), ys.max())
    if ext is None:
        raise SystemExit("범위를 구할 수 없음(형상/헤더 없음)")
    print(f"[units] {usrc} → 1단위={to_m} m | INSERT 전개 {n_insert}개")
    return arr, ext, to_m


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

    segs, ext, to_m = _load_segments(args.dxf, getattr(args, "units", None))
    xmin, ymin, xmax, ymax = ext
    upm = 1.0 / to_m                      # 미터당 도면단위
    Wm, Hm = (xmax - xmin) * to_m, (ymax - ymin) * to_m
    lines = [[(s[0], s[1]), (s[2], s[3])] for s in segs]
    print(f"[load] segments={len(segs)}  전체 {Wm:.1f} m x {Hm:.1f} m")

    # (1) 깨끗한 도면
    fig = plt.figure(figsize=(16, 16 * Hm / Wm)); ax = fig.add_axes([0, 0, 1, 1])
    ax.add_collection(LineCollection(lines, colors="#222", linewidths=0.25))
    ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax); ax.set_aspect("equal"); ax.axis("off")
    p1 = args.out_prefix + ".png"
    fig.savefig(p1, dpi=args.dpi, facecolor="white", bbox_inches="tight", pad_inches=0)
    plt.close(fig)

    # (2) 미터 척도/격자/스케일바  (모든 치수는 미터→도면단위(upm)로 환산)
    G = args.grid_m * upm
    fig = plt.figure(figsize=(17, 17 * Hm / Wm)); ax = fig.add_axes([0.06, 0.06, 0.92, 0.90])
    ax.add_collection(LineCollection(lines, colors="#444", linewidths=0.25))
    ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax); ax.set_aspect("equal")
    ax.xaxis.set_major_locator(MultipleLocator(G)); ax.yaxis.set_major_locator(MultipleLocator(G))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{(v-xmin)*to_m:.0f}"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{(v-ymin)*to_m:.0f}"))
    for gx in np.arange(0, (xmax - xmin) + 1, G): ax.axvline(xmin + gx, color="#1f77ff", lw=0.5, alpha=0.30)
    for gy in np.arange(0, (ymax - ymin) + 1, G): ax.axhline(ymin + gy, color="#1f77ff", lw=0.5, alpha=0.30)
    ax.set_xlabel("X (m, 남서코너=0)", fontsize=15); ax.set_ylabel("Y (m, 남서코너=0)", fontsize=15)
    ax.set_title(f"평면도 · 척도(미터) · 격자 {args.grid_m:.0f} m · 전체 {Wm:.1f} m × {Hm:.1f} m", fontsize=17)
    bar = 10 * upm                       # 10 m 스케일바
    bx, by = xmin + 3 * upm, ymin + 2.5 * upm
    ax.plot([bx, bx + bar], [by, by], "-", color="k", lw=4, solid_capstyle="butt")
    ax.text(bx + bar / 2, by + 1.2 * upm, "10 m", ha="center", fontsize=14, weight="bold")
    for k in range(11):
        ax.plot([bx + k * upm] * 2, [by - 0.4 * upm, by + 0.4 * upm], "-", color="k", lw=1)
    p2 = args.out_prefix + "_scale.png"
    fig.savefig(p2, dpi=args.dpi, facecolor="white")

    # (3) m/px 메타 JSON — MACS 멀티카메라 시스템 맵 업로드용
    #     (POST /api/site/map 의 meta 필드로 넘기면 축척 자동 설정)
    fig.set_dpi(args.dpi)
    fig.canvas.draw()                     # 저장 dpi 기준으로 axes 픽셀 bbox 확정
    bb = ax.get_window_extent()
    m_per_px = Wm / bb.width              # 플롯영역 가로 px ↔ 실폭(m)
    meta = {
        "m_per_px": round(m_per_px, 6),
        "plot_bbox_px": [round(bb.x0, 1), round(bb.y0, 1), round(bb.x1, 1), round(bb.y1, 1)],
        "total_m": [round(Wm, 2), round(Hm, 2)],
        "grid_m": args.grid_m,
        "dpi": args.dpi,
        "source_dxf": str(args.dxf),
    }
    p3 = args.out_prefix + "_scale.meta.json"
    with open(p3, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    plt.close(fig)
    print(f"[ok] {p1}\n[ok] {p2}\n[ok] {p3}  (m/px={m_per_px:.5f})")


def main():
    ap = argparse.ArgumentParser(description="CAD 변환 파이프라인(재현용)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("dwg2dxf", help="ODAFileConverter 헤드리스 DWG↔DXF")
    a.add_argument("--in", dest="infile", required=True, help="입력 .dwg 또는 .dxf")
    a.add_argument("--out", required=True, help="출력 폴더")
    a.add_argument("--out-ver", default="ACAD2018", help="ODA 출력 버전(기본 ACAD2018)")
    a.add_argument("--engine", choices=["auto", "oda", "libredwg"], default="auto",
                   help="변환 엔진(기본 auto: ODA 있으면 ODA, 없으면 libredwg)")
    a.set_defaults(func=dwg2dxf)

    b = sub.add_parser("dxf2png", help="DXF → 도면 PNG + 척도 PNG")
    b.add_argument("--dxf", required=True)
    b.add_argument("--out-prefix", required=True, help="예: cad/17F_plan → *.png, *_scale.png")
    b.add_argument("--grid-m", type=float, default=5.0, help="격자 간격 m(기본 5)")
    b.add_argument("--units", choices=["mm", "cm", "m", "in", "ft"], default=None,
                   help="도면 단위 강제(미지정 시 $INSUNITS 자동감지, 실패 시 mm)")
    b.add_argument("--dpi", type=int, default=200)
    b.set_defaults(func=dxf2png)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
