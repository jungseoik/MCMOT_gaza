"""
evac.render — 평면도/경로/연결성 렌더(17F_plan_scale.png 스타일: 미터격자·스케일바).
matplotlib Agg 백엔드(헤드리스 OK).
"""
import math

import numpy as np

KRFONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def _setup_font(plt):
    import matplotlib.font_manager as fm
    try:
        fm.fontManager.addfont(KRFONT)
        plt.rcParams["font.family"] = fm.FontProperties(fname=KRFONT).get_name()
    except Exception:
        pass


def _scale_axes(ax, np_, bounds, grid_m):
    from matplotlib.ticker import MultipleLocator, FuncFormatter
    minx, miny, maxx, maxy = bounds
    G = grid_m * 1000.0
    ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy); ax.set_aspect("equal")
    ax.xaxis.set_major_locator(MultipleLocator(G))
    ax.yaxis.set_major_locator(MultipleLocator(G))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{(v-minx)/1000:.0f}"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{(v-miny)/1000:.0f}"))
    for gx in np_.arange(0, (maxx - minx) + 1, G):
        ax.axvline(minx + gx, color="#1f77ff", lw=0.4, alpha=0.15)
    for gy in np_.arange(0, (maxy - miny) + 1, G):
        ax.axhline(miny + gy, color="#1f77ff", lw=0.4, alpha=0.15)
    ax.set_xlabel("X (m, SW=0)", fontsize=13); ax.set_ylabel("Y (m, SW=0)", fontsize=13)
    # 스케일바 10m
    bx, by = minx + 3000, miny + 2000
    ax.plot([bx, bx + 10000], [by, by], "-", color="k", lw=4, solid_capstyle="butt", zorder=9)
    ax.text(bx + 5000, by + 1200, "10 m", ha="center", fontsize=12, weight="bold", zorder=9)


def render_routes(out, obstacles, bounds, analysis, *, exits=None, ref=None,
                  threshold_mm=30000.0, grid_m=5.0, dpi=200, title_extra=""):
    """경로 결과 렌더. analysis=core.Analysis. exits=[(x,y)] 표시용."""
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    _setup_font(plt)
    minx, miny, maxx, maxy = bounds
    Wm, Hm = (maxx - minx) / 1000, (maxy - miny) / 1000
    fig = plt.figure(figsize=(20, 20 * Hm / Wm)); ax = fig.add_axes([0.05, 0.05, 0.93, 0.9])
    ax.add_collection(LineCollection(
        [[(s[0], s[1]), (s[2], s[3])] for s in obstacles], colors="#999", linewidths=0.25))

    if ref:
        for pts in ref["pass"] + ref["fail"]:
            xs, ys = zip(*pts); ax.plot(xs, ys, "--", color="#1f77ff", lw=1.0, alpha=0.55, zorder=3)
        ax.plot([], [], "--", color="#1f77ff", lw=1.2, label="매크로 원본 경로(ref)")

    if exits:
        ax.scatter([e[0] for e in exits], [e[1] for e in exits], s=110, marker="s",
                   c="#0033aa", edgecolors="white", linewidths=0.7, zorder=7, label="Exit(피난계단)")

    n_pass = n_fail = 0
    for p in analysis.paths:
        color = "#00a000" if p["is_pass"] else "#e00000"
        xs, ys = zip(*p["path_m"])
        ax.plot(xs, ys, "-", color=color, lw=2.0, zorder=5)
        ax.scatter([xs[0]], [ys[0]], s=70, facecolors="none", edgecolors=color,
                   linewidths=1.6, zorder=6)
        ax.text(xs[0] + 100, ys[0] + 25, f"{p['dist_mm']/1000:.1f}m", color=color,
                fontsize=9, weight="bold", zorder=8,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.7))
        n_pass += p["is_pass"]; n_fail += (not p["is_pass"])
    ax.plot([], [], "-", color="#00a000", lw=2, label=f"Pass ≤{threshold_mm/1000:.0f}m ({n_pass})")
    ax.plot([], [], "-", color="#e00000", lw=2, label=f"Fail >{threshold_mm/1000:.0f}m ({n_fail})")

    _scale_axes(ax, np, bounds, grid_m)
    ax.set_title(f"피난경로(다익스트라) · 전체 {Wm:.1f}×{Hm:.1f} m · "
                 f"Pass {n_pass}/Fail {n_fail}{title_extra}", fontsize=15)
    ax.legend(loc="upper right", fontsize=11, framealpha=0.9)
    fig.savefig(out, facecolor="white", dpi=dpi); plt.close(fig)
    return n_pass, n_fail


def render_connectivity(out, obstacles, bounds, conn, dpi=150):
    """연결성 진단(도면 품질): 초록=최대연결영역, 주황=고립조각, 회색=벽."""
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.colors import ListedColormap
    import matplotlib.patches as mp
    _setup_font(plt)
    minx, miny, maxx, maxy = bounds
    Wm, Hm = (maxx - minx) / 1000, (maxy - miny) / 1000
    lab, main = conn["labels"], conn["main"]
    img = np.zeros(lab.shape, np.uint8)
    img[lab > 0] = 2; img[lab == main] = 1
    fig = plt.figure(figsize=(18, 18 * Hm / Wm)); ax = fig.add_axes([0.05, 0.05, 0.92, 0.9])
    ax.imshow(img.T, origin="lower", extent=[0, Wm, 0, Hm],
              cmap=ListedColormap(["white", "#8fd18f", "#ffb84d"]), vmin=0, vmax=2,
              interpolation="nearest")
    ax.add_collection(LineCollection(
        [[((s[0]-minx)/1000, (s[1]-miny)/1000), ((s[2]-minx)/1000, (s[3]-miny)/1000)]
         for s in obstacles], colors="#555", linewidths=0.2))
    ax.set_aspect("equal"); ax.set_xlim(0, Wm); ax.set_ylim(0, Hm)
    ax.set_xlabel("X (m, SW=0)"); ax.set_ylabel("Y (m, SW=0)")
    ax.set_title(f"보행공간 연결성 — 최대영역 {100*conn['largest_frac']:.0f}% / "
                 f"고립조각 {conn['n_components']-1}개")
    ax.legend(handles=[mp.Patch(color="#8fd18f", label="최대 연결영역"),
                       mp.Patch(color="#ffb84d", label="고립 조각(방/집기)"),
                       mp.Patch(color="#555", label="벽/장애물")],
              loc="upper right", fontsize=11)
    fig.savefig(out, facecolor="white", dpi=dpi); plt.close(fig)
