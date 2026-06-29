#!/usr/bin/env python3
"""
cctv_synth.py — 평면도(DXF)에서 가상 CCTV 시점의 '구조 레퍼런스' 아티팩트를 생성한다.

GPT / Gemini 이미지 생성에 넣을 입력물을 만든다(데모 등급, 기하 근사):
  1) <out>_persp.png  : 카메라 시점의 원근 와이어프레임 (바닥 선 + 긴 선분 입체화)
  2) <out>_plan.png   : 카메라 위치 + FOV 부채꼴을 표시한 탑다운 평면도
  3) stdout           : 이미지 생성기에 붙여넣을 카메라 스펙 + 프롬프트

좌표계: 평면도 남서(좌하단) 코너 = (0,0) m, +X 오른쪽, +Y 위. (DXF 단위는 mm)

예)
  python tools/cctv_synth.py --dxf cad/17F.dxf --out cad/cctv_A \
     --cam 20 20 --look 37 40 --cam_h 2.7 --hfov 90
"""
import argparse, math
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.font_manager as fm
import ezdxf

KRFONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def load_segments(dxf_path):
    """DXF의 모든 선형 엔티티를 2D 선분 리스트로 평탄화. 단위 mm. 반환 (segs, (xmin,ymin,xmax,ymax))."""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    segs = []  # 각 원소: (x0,y0,x1,y1)
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
                pts = list(e.flattening(distance=20))  # 20mm 정밀도
                add_poly([(p.x, p.y) for p in pts], closed=(t == "CIRCLE"))
            except Exception:
                pass
    arr = np.array(segs, dtype=float)
    h = doc.header
    (xmin, ymin, _), (xmax, ymax, _) = h.get("$EXTMIN"), h.get("$EXTMAX")
    return arr, (xmin, ymin, xmax, ymax)


def project(P, C, fwd, right, up, hfov_deg, W, H, near=100.0):
    """월드 점(N,3 mm) -> 화면 픽셀(N,2). 카메라 앞(near 이상)만 유효, 나머지는 NaN."""
    rel = P - C  # (N,3)
    xc = rel @ right
    yc = rel @ up
    zc = rel @ fwd  # depth
    hf = math.radians(hfov_deg)
    fx = (W / 2.0) / math.tan(hf / 2.0)
    fy = fx  # 정사각 픽셀
    sx = np.full_like(xc, np.nan)
    sy = np.full_like(yc, np.nan)
    valid = zc > near
    sx[valid] = W / 2.0 + fx * xc[valid] / zc[valid]
    sy[valid] = H / 2.0 - fy * yc[valid] / zc[valid]
    return np.stack([sx, sy], axis=1), zc


def render_perspective(segs, ext, cam, look, cam_h, hfov, W, H,
                       wall_thresh_mm, wall_h_mm, out_path):
    xmin, ymin, xmax, ymax = ext
    cx, cy = xmin + cam[0] * 1000, ymin + cam[1] * 1000
    lx, ly = xmin + look[0] * 1000, ymin + look[1] * 1000
    C = np.array([cx, cy, cam_h * 1000.0])
    T = np.array([lx, ly, 0.0])  # 바닥 한 점을 본다
    fwd = T - C; fwd = fwd / np.linalg.norm(fwd)
    world_up = np.array([0, 0, 1.0])
    right = np.cross(fwd, world_up); right = right / np.linalg.norm(right)
    up = np.cross(right, fwd)

    p0 = segs[:, 0:2]; p1 = segs[:, 2:4]
    seglen = np.linalg.norm(p1 - p0, axis=1)

    # 바닥(z=0) 선분
    A = np.column_stack([p0, np.zeros(len(p0))])
    B = np.column_stack([p1, np.zeros(len(p1))])
    sA, zA = project(A, C, fwd, right, up, hfov, W, H)
    sB, zB = project(B, C, fwd, right, up, hfov, W, H)

    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(H, 0)
    ax.set_facecolor("white"); ax.axis("off")

    # 바닥 라인: 깊이에 따라 연하게(원근감)
    seg_lines = []
    both = (~np.isnan(sA[:, 0])) & (~np.isnan(sB[:, 0]))
    for i in np.where(both)[0]:
        seg_lines.append([(sA[i, 0], sA[i, 1]), (sB[i, 0], sB[i, 1])])
    from matplotlib.collections import LineCollection
    ax.add_collection(LineCollection(seg_lines, colors="#888888", linewidths=0.4))

    # 긴 선분 -> 입체화(파티션/벽). 양 끝을 wall_h까지 세우고 윗변 연결
    longidx = np.where(seglen >= wall_thresh_mm)[0]
    top = wall_h_mm
    At = np.column_stack([p0[longidx], np.full(len(longidx), top)])
    Bt = np.column_stack([p1[longidx], np.full(len(longidx), top)])
    sAt, _ = project(At, C, fwd, right, up, hfov, W, H)
    sBt, _ = project(Bt, C, fwd, right, up, hfov, W, H)
    sA_l, sB_l = sA[longidx], sB[longidx]
    wall_lines = []
    for k in range(len(longidx)):
        q = [sA_l[k], sAt[k], sBt[k], sB_l[k]]
        if any(np.isnan(np.concatenate(q))):
            continue
        wall_lines.append([tuple(sA_l[k]), tuple(sAt[k])])   # 좌 수직
        wall_lines.append([tuple(sB_l[k]), tuple(sBt[k])])   # 우 수직
        wall_lines.append([tuple(sAt[k]), tuple(sBt[k])])    # 윗변
    ax.add_collection(LineCollection(wall_lines, colors="#222222", linewidths=0.8))

    fig.savefig(out_path, dpi=100, facecolor="white")
    plt.close(fig)
    return dict(C=C, fwd=fwd, right=right, up=up)


def render_plan(segs, ext, cam, look, hfov, far_m, out_path):
    xmin, ymin, xmax, ymax = ext
    try:
        fm.fontManager.addfont(KRFONT)
        plt.rcParams["font.family"] = fm.FontProperties(fname=KRFONT).get_name()
    except Exception:
        pass
    from matplotlib.collections import LineCollection
    fig = plt.figure(figsize=(16, 16 * (ymax - ymin) / (xmax - xmin)))
    ax = fig.add_axes([0.06, 0.06, 0.92, 0.90])
    lines = [[(s[0], s[1]), (s[2], s[3])] for s in segs]
    ax.add_collection(LineCollection(lines, colors="#444444", linewidths=0.3))
    ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax); ax.set_aspect("equal")

    cx, cy = xmin + cam[0] * 1000, ymin + cam[1] * 1000
    lx, ly = xmin + look[0] * 1000, ymin + look[1] * 1000
    yaw = math.atan2(ly - cy, lx - cx)
    far = far_m * 1000
    a1 = yaw - math.radians(hfov / 2); a2 = yaw + math.radians(hfov / 2)
    tri = np.array([[cx, cy],
                    [cx + far * math.cos(a1), cy + far * math.sin(a1)],
                    [cx + far * math.cos(a2), cy + far * math.sin(a2)]])
    ax.add_patch(mpatches.Polygon(tri, closed=True, facecolor="#ff5a36", alpha=0.18,
                                  edgecolor="#ff5a36", lw=1.5))
    ax.plot([cx], [cy], marker="o", ms=14, color="#ff5a36", zorder=5)
    ax.annotate("CCTV", (cx, cy), textcoords="offset points", xytext=(10, 10),
                fontsize=14, color="#ff5a36", weight="bold")
    ax.plot([cx, lx], [cy, ly], "--", color="#ff5a36", lw=1.2)
    # 미터 격자
    from matplotlib.ticker import FuncFormatter, MultipleLocator
    ax.xaxis.set_major_locator(MultipleLocator(5000)); ax.yaxis.set_major_locator(MultipleLocator(5000))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{(v-xmin)/1000:.0f}"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{(v-ymin)/1000:.0f}"))
    for gx in np.arange(0, xmax - xmin + 1, 5000): ax.axvline(xmin + gx, color="#1f77ff", lw=0.5, alpha=0.25)
    for gy in np.arange(0, ymax - ymin + 1, 5000): ax.axhline(ymin + gy, color="#1f77ff", lw=0.5, alpha=0.25)
    ax.set_xlabel("X (m, 남서코너 기준)"); ax.set_ylabel("Y (m, 남서코너 기준)")
    ax.set_title(f"CCTV 배치  cam=({cam[0]},{cam[1]})m  look=({look[0]},{look[1]})m  HFOV={hfov}°")
    fig.savefig(out_path, dpi=150, facecolor="white"); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dxf", required=True)
    ap.add_argument("--out", required=True, help="출력 접두어 (예: cad/cctv_A)")
    ap.add_argument("--cam", nargs=2, type=float, required=True, metavar=("X", "Y"), help="카메라 위치 m")
    ap.add_argument("--look", nargs=2, type=float, required=True, metavar=("X", "Y"), help="바라보는 바닥점 m")
    ap.add_argument("--cam_h", type=float, default=2.7, help="설치 높이 m")
    ap.add_argument("--tilt", type=float, default=None,
                    help="하방 틸트각 deg. 주면 --look 방향으로 광축이 이 각도가 되도록 주시점 거리를 재계산(높은 천장캠용)")
    ap.add_argument("--hfov", type=float, default=90.0, help="수평 화각 deg")
    ap.add_argument("--W", type=int, default=1280)
    ap.add_argument("--H", type=int, default=720)
    ap.add_argument("--wall_thresh", type=float, default=2000.0, help="이 길이(mm) 이상 선분을 입체화")
    ap.add_argument("--wall_h", type=float, default=1200.0, help="입체화 높이 mm")
    args = ap.parse_args()

    segs, ext = load_segments(args.dxf)
    print(f"[load] {len(segs)} segments, ext(m)= "
          f"{(ext[2]-ext[0])/1000:.1f} x {(ext[3]-ext[1])/1000:.1f}")
    cam_to_look = math.hypot(args.look[0]-args.cam[0], args.look[1]-args.cam[1])
    yaw = math.atan2(args.look[1]-args.cam[1], args.look[0]-args.cam[0])
    if args.tilt is not None:
        # 광축이 args.tilt 가 되도록 주시점 거리 재계산: d = h / tan(tilt)
        d = args.cam_h / math.tan(math.radians(args.tilt))
        eff_look = [args.cam[0] + d*math.cos(yaw), args.cam[1] + d*math.sin(yaw)]
        tilt = args.tilt
    else:
        eff_look = args.look
        tilt = math.degrees(math.atan2(args.cam_h, cam_to_look))

    render_perspective(segs, ext, args.cam, eff_look, args.cam_h, args.hfov,
                        args.W, args.H, args.wall_thresh, args.wall_h, args.out + "_persp.png")
    # 평면 FOV 부채꼴은 의도한 커버 방향(원래 --look)까지 그려 배치 맥락 표시
    render_plan(segs, ext, args.cam, args.look, args.hfov, far_m=cam_to_look*1.4,
                out_path=args.out + "_plan.png")
    print(f"[save] {args.out}_persp.png  {args.out}_plan.png")
    print("\n================ 이미지 생성기용 카메라 스펙 ================")
    print(f"- 평면도 위치: ({args.cam[0]}, {args.cam[1]}) m  (남서코너 기준), 설치높이 {args.cam_h} m")
    print(f"- 주시점(바닥): ({args.look[0]}, {args.look[1]}) m, 카메라-주시점 수평거리 {cam_to_look:.1f} m")
    print(f"- 하방 틸트 ≈ {tilt:.0f}°, 수평 FOV {args.hfov}°, 출력 {args.W}x{args.H}")


if __name__ == "__main__":
    main()
