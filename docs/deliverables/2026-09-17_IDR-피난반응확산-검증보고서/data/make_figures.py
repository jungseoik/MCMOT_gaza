#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""IDR 시나리오별 파라미터 민감도 그림 5종 + CSV. `python .../make_figures.py`"""
import json, csv
from pathlib import Path
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.family"] = ["NanumGothic", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 150
plt.rcParams["savefig.bbox"] = "tight"

HERE = Path(__file__).resolve().parent
IMG = HERE.parent / "img"; IMG.mkdir(exist_ok=True)
D = json.load(open(HERE / "idr_sweep.json"))
R, BASE, LIVE, SW = D["rows"], D["base"], D["live"], D["sweep_grid"]
GDT, GV = D["grid_dt"], D["grid_v"]

TRIAL = ["01", "02", "03"]                  # IDR 각본
CTRL = ["04", "05", "06"]                   # 대조군 — 정상 보행(경로만 다름)
C = {"01": "#c0504d", "02": "#2f6fb0", "03": "#2a7d3f"}
CC = "#999999"
LAB = {"01": "S01 우회 2명 (8:2)", "02": "S02 매우 느린 속도",
       "03": "S03 2초 멈췄다 이동"}


# ---------------------------------------------------------------- 공통 계산
def series_re_ve(num, v_th=None, a_th=None):
    """구역 1초 샘플 → (t[], r_e[], v_e[], n[]). 엔진 판정식과 동일."""
    v_th = BASE["v_th"] if v_th is None else v_th
    a_th = BASE["a_th"] if a_th is None else a_th
    ts, res, ves, ns = [], [], [], []
    for s in R[num]["series"]:
        m = s["m"]
        ts.append(s["t"]); ns.append(len(m))
        sp = [x[0] for x in m if x[0] is not None]
        ves.append(sum(sp) / len(sp) if sp else np.nan)
        res.append(sum(1 for x in m if x[0] is not None and x[1] is not None
                       and x[0] >= v_th and x[1] >= a_th) / len(m) if m else 0.0)
    return np.array(ts), np.array(res), np.array(ves), np.array(ns)


def hold_runs(num, v_th=None, a_th=None, r_th=None):
    """조건 성립 on/off 와 최장 연속 유지시간(s)."""
    r_th = BASE["r_th"] if r_th is None else r_th
    v_th = BASE["v_th"] if v_th is None else v_th
    t, re_, ve, _ = series_re_ve(num, v_th, a_th)
    cond = (re_ >= r_th) & (ve >= v_th)
    best = cur = 0.0; s0 = None
    for i, c in enumerate(cond):
        if c and s0 is None:
            s0 = t[i]
        if c:
            cur = t[i] - s0; best = max(best, cur)
        else:
            s0 = None
    return t, cond, best


def delays(num, key):
    """스윕 축 값 → 개시지연(초). 미개시는 np.nan."""
    xs = SW[key]
    ys = [R[num]["sweep"][key][str(v)]["delay"] for v in xs]
    return np.array(xs, float), np.array([np.nan if y is None else y for y in ys])


FAIL_Y = -6.0          # '미개시' 전용 밴드의 y (축 아래 전용 구역)


def sweep_panel(ax, key, nums, title, xlabel, ctrl=True):
    """인자 1개 스윕 → 개시 지연. 미개시는 축 하단 전용 밴드에 ✕ 로 찍는다."""
    ax.axhspan(FAIL_Y - 3, FAIL_Y + 3, color="#f2d5d2", zorder=0)
    ax.text(0.012, 0.045, "미개시 (값 없음)", transform=ax.transAxes,
            fontsize=8.5, color="#a33", va="center")
    if ctrl:
        for i, n in enumerate(CTRL):
            xs, ys = delays(n, key)
            ax.plot(xs, ys, marker=".", ms=4, lw=1.1, ls="--", color=CC,
                    alpha=0.85, label="대조군 S04~S06 (정상 보행)" if i == 0 else None)
            ax.plot(xs[np.isnan(ys)], np.full(np.isnan(ys).sum(), FAIL_Y),
                    marker="x", ls="none", ms=7, mew=1.6, color=CC, alpha=0.85)
    span = (max(SW[key]) - min(SW[key])) * 0.012
    for i, n in enumerate(nums):
        xs, ys = delays(n, key)
        ax.plot(xs, ys, marker="o", ms=5.5, lw=2.2, color=C[n], label=LAB[n])
        bad = np.isnan(ys)
        off = (i - (len(nums) - 1) / 2) * span * 2     # 겹침 방지 미세 오프셋
        ax.plot(xs[bad] + off, np.full(bad.sum(), FAIL_Y), marker="x",
                ls="none", ms=10, mew=2.4, color=C[n])
    ax.axvline(BASE[key], color="#7b4fa0", lw=1.3, ls=":")
    lo, hi = ax.get_ylim()
    ax.set_ylim(FAIL_Y - 3.5, hi + (hi - FAIL_Y) * 0.42)   # 범례 자리 확보
    ax.annotate(f"기준 {BASE[key]}", (BASE[key], FAIL_Y + 4.5),
                textcoords="offset points", xytext=(4, 0),
                fontsize=8.5, color="#7b4fa0", va="bottom")
    ax.set_title(title, fontsize=11.5)
    ax.set_xlabel(xlabel); ax.set_ylabel("피난개시 지연 (s) — 낮을수록 빠른 반응")
    ax.grid(alpha=0.3); ax.legend(fontsize=8.3, loc="upper left",
                                  framealpha=0.92, ncol=2)


# ---------------------------------------------------------------- fig1 격자
def fig1():
    fig, axes = plt.subplots(1, 3, figsize=(13.2, 3.9))
    for ax, n in zip(axes, TRIAL):
        M = np.full((len(GDT), len(GV)), np.nan)
        for i, dt in enumerate(GDT):
            for j, v in enumerate(GV):
                d = R[n]["grid"][f"{dt}|{v}"]["delay"]
                M[i, j] = np.nan if d is None else d
        im = ax.imshow(M, cmap="YlGnBu_r", vmin=0, vmax=50, aspect="auto")
        for i in range(len(GDT)):
            for j in range(len(GV)):
                ok = not np.isnan(M[i, j])
                ax.text(j, i, f"{M[i,j]:.0f}s" if ok else "미개시",
                        ha="center", va="center", fontsize=8.5,
                        color="white" if ok and M[i, j] < 28 else "#333")
        ax.set_xticks(range(len(GV))); ax.set_xticklabels(GV)
        ax.set_yticks(range(len(GDT))); ax.set_yticklabels(GDT)
        ax.set_xlabel("v_th (m/s)"); ax.set_ylabel("dt_hold (s)")
        ax.set_title(f"{LAB[n]}", fontsize=11)
        ax.add_patch(plt.Rectangle(
            (GV.index(BASE["v_th"]) - .5, GDT.index(BASE["dt_hold"]) - .5), 1, 1,
            fill=False, ec="#c0392b", lw=2.2))
    fig.suptitle("그림 1. 기준 임계 선정 — dt_hold × v_th 격자에서 각본 3건의 개시 지연"
                 f"  (a_th {BASE['a_th']} · r_th {BASE['r_th']} 고정, 빨간 테두리 = 채택 기준)",
                 fontsize=11.5, y=1.04)
    fig.tight_layout(); fig.savefig(IMG / "fig1_base_grid.png"); plt.close(fig)


# ---------------------------------------------------------------- fig2 r_th
def fig2():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13.4, 4.5))
    t, re_, _v, _n = series_re_ve("01")
    t5, re5, _v5, _n5 = series_re_ve("05")
    a1.fill_between(t, 0, re_, color=C["01"], alpha=0.12)
    a1.plot(t5, re5, lw=1.6, color="#2a7d3f", alpha=0.85,
            label="대조군 S05 — 전원이 권장경로 (우회 0명)")
    a1.plot(t, re_, lw=2.4, color=C["01"], label=LAB["01"])
    for y, c, lb, fx, ha in [(0.7, "#7b4fa0", "r_th 0.7 (기준) — 개시 13s", .99, "right"),
                             (0.9, "#e08a2e", "r_th 0.9 — 개시 14s (S01 한계)", .52, "left"),
                             (0.95, "#c0392b", "r_th 0.95 — S01 미개시", .99, "right")]:
        a1.axhline(y, color=c, lw=1.3, ls=":")
        a1.text(t.max() * fx, y + 0.012, lb, fontsize=8.3, color=c,
                ha=ha, va="bottom")
    a1.set_xlim(0, t.max() * 1.02)
    a1.set_xlabel("경보 이후 경과 (s)"); a1.set_ylabel("동시만족 비율 $r_e$")
    a1.set_ylim(0, 1.06); a1.grid(alpha=0.3); a1.legend(fontsize=8.5, loc="lower left")
    a1.set_title("(a) 구역 내 동시만족 비율 $r_e$ — 엔진 판정 격자(1초)", fontsize=11.5)

    sweep_panel(a2, "r_th", ["01"], "(b) r_th 만 바꿨을 때의 개시 지연",
                "r_th (동시만족 비율 문턱)")
    fig.suptitle("그림 2. S01(권장경로 8명 + 우측 우회 2명) — 비율 문턱 r_th 민감도",
                 fontsize=12.5, y=1.02)
    fig.tight_layout(); fig.savefig(IMG / "fig2_s01_ratio.png"); plt.close(fig)


# ---------------------------------------------------------------- fig3 v_th
def fig3():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13.4, 4.5))
    t1, _r1, ve1, _ = series_re_ve("01")
    t2, _r2, ve2, _ = series_re_ve("02")
    a1.plot(t1, ve1, lw=1.7, color=C["01"], alpha=0.85,
            label="대조군 S01 — 정상 보행 속도")
    a1.plot(t2, ve2, lw=2.4, color=C["02"], label=LAB["02"])
    a1.fill_between(t2, 0, ve2, color=C["02"], alpha=0.12)
    for y, c, lb in [(0.5, "#7b4fa0", "v_th 0.5 (기준) — S02 개시 22s"),
                     (0.7, "#c0392b", "v_th 0.7 — S02 미개시 / S01 13s 유지")]:
        a1.axhline(y, color=c, lw=1.3, ls=":")
        a1.text(max(t1.max(), t2.max()) * 0.99, y + 0.02, lb, fontsize=8.3,
                color=c, ha="right", va="bottom")
    a1.set_xlim(0, max(t1.max(), t2.max()) * 1.02)
    a1.set_xlabel("경보 이후 경과 (s)"); a1.set_ylabel("구역 평균속도 $v_e$ (m/s)")
    a1.set_ylim(0, None); a1.grid(alpha=0.3); a1.legend(fontsize=8.5, loc="upper right")
    a1.set_title("(a) 구역 평균속도 $v_e$ — 엔진 판정 격자(1초)", fontsize=11.5)

    sweep_panel(a2, "v_th", ["02"], "(b) v_th 만 바꿨을 때의 개시 지연",
                "v_th (속도 문턱, m/s)")
    fig.suptitle("그림 3. S02(매우 느린 속도) — 속도 문턱 v_th 민감도", fontsize=12.5, y=1.02)
    fig.tight_layout(); fig.savefig(IMG / "fig3_s02_speed.png"); plt.close(fig)


# ---------------------------------------------------------------- fig4 dt_hold
def fig4():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13.4, 4.5))
    order = TRIAL + CTRL
    xmax = max(series_re_ve(n)[0].max() for n in order)
    for k, n in enumerate(order):
        t, cond, best = hold_runs(n)
        col = C.get(n, CC)
        a1.broken_barh([(t[i] - 0.5, 1.0) for i in range(len(t)) if cond[i]],
                       (k - 0.30, 0.60), facecolors=col,
                       alpha=1.0 if n in TRIAL else 0.5)
        a1.text(xmax * 1.01, k, f"최장 {best:.0f}s", fontsize=8.5,
                color=col if n in TRIAL else "#666", va="center")
    a1.set_xlim(0, xmax * 1.10)
    a1.set_yticks(range(len(order)))
    a1.set_yticklabels([LAB.get(n, f"대조군 S{n}") for n in order], fontsize=9)
    a1.invert_yaxis(); a1.grid(alpha=0.3, axis="x")
    a1.set_xlabel("경보 이후 경과 (s)")
    a1.set_title(f"(a) 개시 조건($v_e≥${BASE['v_th']}, $r_e≥${BASE['r_th']}) 이 성립한 구간",
                 fontsize=11.5)

    sweep_panel(a2, "dt_hold", ["03"], "(b) dt_hold 만 바꿨을 때의 개시 지연",
                "dt_hold (연속 유지시간, s)")
    fig.suptitle("그림 4. S03(2초 멈췄다 움직였다) — 연속 유지시간 dt_hold 민감도",
                 fontsize=12.5, y=1.02)
    fig.tight_layout(); fig.savefig(IMG / "fig4_s03_hold.png"); plt.close(fig)


# ---------------------------------------------------------------- fig5 a_th
def fig5():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13.2, 4.4))
    bins = np.linspace(-1, 1, 21)
    for n in TRIAL:
        al = [x[1] for s in R[n]["series"] for x in s["m"] if x[1] is not None]
        a1.hist(al, bins=bins, histtype="step", lw=2 if n == "01" else 1.3,
                color=C[n], density=True, label=f"{LAB[n]}  (n={len(al)})")
    a1.axvline(BASE["a_th"], color="#7b4fa0", lw=1.4, ls=":")
    a1.text(BASE["a_th"], a1.get_ylim()[1], f" a_th {BASE['a_th']} (45°)",
            fontsize=8.5, color="#7b4fa0", va="top")
    a1.set_xlabel("객체별 경로 정렬도 $a_i$ (cosine)")
    a1.set_ylabel("밀도"); a1.grid(alpha=0.3); a1.legend(fontsize=8.5, loc="upper left")
    a1.set_title("(a) 구역 내 객체별 정렬도 분포", fontsize=11.5)

    sweep_panel(a2, "a_th", TRIAL, "(b) a_th 만 바꿨을 때의 개시 지연", "a_th (정렬도 문턱, cosine)")
    fig.suptitle("그림 5. 보조 인자 — 정렬도 문턱 a_th 민감도", fontsize=12, y=1.02)
    fig.tight_layout(); fig.savefig(IMG / "fig5_align.png"); plt.close(fig)


# ---------------------------------------------------------------- CSV
def csvs():
    with open(HERE / "idr_sweep.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["시나리오", "층", "각본", "겨눈 인자", "인자", "값",
                    "판정", "개시지연_s", "IDR_mps", "판정시점_r_e"])
        for n, r in sorted(R.items()):
            for k, vv in r["sweep"].items():
                for v, d in vv.items():
                    w.writerow([f"S{n}", r["floor"], r["intent"],
                                D["target"].get(n, ""), k, v, d["status"],
                                "" if d["delay"] is None else d["delay"],
                                "" if d["idr"] is None else round(d["idr"], 3),
                                "" if d["ratio"] is None else round(d["ratio"], 3)])
    with open(HERE / "idr_grid.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["시나리오", "dt_hold", "v_th", "판정", "개시지연_s"])
        for n, r in sorted(R.items()):
            for key, d in r["grid"].items():
                dt, v = key.split("|")
                w.writerow([f"S{n}", dt, v, d["status"],
                            "" if d["delay"] is None else d["delay"]])


if __name__ == "__main__":
    fig1(); fig2(); fig3(); fig4(); fig5(); csvs()
    print("→ img/fig1_base_grid.png fig2_s01_ratio.png fig3_s02_speed.png "
          "fig4_s03_hold.png fig5_align.png + idr_sweep.csv idr_grid.csv")
