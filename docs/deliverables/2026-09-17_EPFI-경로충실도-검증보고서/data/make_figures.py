#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""EPFI 보고서 그림 — collect.py 가 만든 epfi_data.json 을 읽는다."""
import json, os
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np

plt.rcParams["font.family"] = ["NanumGothic", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 150
plt.rcParams["savefig.bbox"] = "tight"
plt.rcParams["axes.grid"] = True
plt.rcParams["grid.alpha"] = 0.25

HERE = Path(__file__).parent
OUT = HERE.parent / "img"
OUT.mkdir(exist_ok=True)
D = json.load(open(HERE / "epfi_data.json"))
ROWS, D_ALLOW = D["rows"], D["d_allow"]
PKGS = ["1·3층", "3층1차"]
PKG_LABEL = {"1·3층": "AI hub 1·3층 (drill-1f3f)", "3층1차": "AI hub 3층 1차 (rehearsal)"}
ROLE_COL = {"양성": "#c0504d", "음성": "#2f6fb0", "기준": "#888888"}
S = lambda r: r["scen"].replace("scenario_", "S")


def rows_of(pkg):
    return [r for r in ROWS if r["pkg"] == pkg]


# ------------------------------------------------ 그림 1. 배정 단위가 만드는 차이
def fig_assignment():
    fig, axes = plt.subplots(1, 2, figsize=(12.6, 4.6), sharey=True)
    for ax, pkg in zip(axes, PKGS):
        rs = sorted(rows_of(pkg), key=lambda r: r["scen"])
        x = np.arange(len(rs)); w = 0.38
        tr = [max(p["dev_track"] for p in r["people"]) for r in rs]
        pe = [max(p["dev_person"] for p in r["people"]) for r in rs]
        ax.bar(x - w/2, tr, w, color="#bbbbbb", label="현행 — 트랙렛마다 경로 재배정")
        ax.bar(x + w/2, pe, w, color="#c0504d", label="사람당 경로 1개로 재계산")
        for i, (a, b) in enumerate(zip(tr, pe)):
            ax.text(x[i] + w/2, b + 0.25, f"{b:.1f}", ha="center", fontsize=8.5)
        ax.set_xticks(x); ax.set_xticklabels([S(r) for r in rs], fontsize=9.5)
        ax.set_title(PKG_LABEL[pkg], fontsize=11, pad=8)
    axes[0].set_ylabel("그 시나리오의 최대 이탈거리 (m)")
    axes[0].legend(frameon=False, fontsize=9.5, loc="upper left")
    fig.suptitle("부록 그림 A.  경로 배정 단위가 이탈거리를 가린다 (수정 전 구현)",
                 fontsize=12.5, y=1.02)
    fig.savefig(OUT / "figA_assignment_gap.png"); plt.close(fig)


# ------------------------------------------------ 그림 2. 양성·음성 대조
def fig_control():
    fig, axes = plt.subplots(1, 2, figsize=(12.6, 4.8), sharey=True)
    for ax, pkg in zip(axes, PKGS):
        rs = sorted(rows_of(pkg), key=lambda r: r["scen"])
        for i, r in enumerate(rs):
            ds = sorted((p["dev_person"] for p in r["people"]), reverse=True)
            ax.scatter([i] * len(ds), ds, s=26, color=ROLE_COL[r["role"]],
                       alpha=.75, zorder=3)
            if r["k"]:
                ax.scatter([i] * r["k"], ds[:r["k"]], s=120, facecolors="none",
                           edgecolors="#111", linewidths=1.3, zorder=4)
        ax.set_xticks(range(len(rs)))
        ax.set_xticklabels([f"{S(r)}\n{r['role']}" + (f" k={r['k']}" if r["k"] else "")
                            for r in rs], fontsize=9)
        ax.axhline(D_ALLOW, color="#2a7d3f", ls="--", lw=1.2)
        ax.set_title(PKG_LABEL[pkg], fontsize=11, pad=8)
    axes[0].set_ylabel("사람별 평균 이탈거리 (m)")
    axes[0].text(-0.35, D_ALLOW + 0.3, f"d_allow {D_ALLOW}m", fontsize=9, color="#2a7d3f")
    axes[0].legend(handles=[Patch(color=v, label=k) for k, v in ROLE_COL.items()]
                   + [plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="none",
                                 markeredgecolor="#111", markersize=10,
                                 label="의도한 상위 k명")],
                   frameon=False, fontsize=9, loc="upper left", ncol=2)
    fig.suptitle("그림 2.  사람별 이탈거리(절대값) — 패키지마다 기준선이 다르다",
                 fontsize=12.5, y=1.02)
    fig.savefig(OUT / "fig2_control_abs.png"); plt.close(fig)


# ------------------------------------------------ 그림 3. 의도 인원 대조
def fig_topk():
    fig, ax = plt.subplots(figsize=(11.5, 4.6))
    labels, intended, detected, colors = [], [], [], []
    for pkg in PKGS:
        for r in sorted(rows_of(pkg), key=lambda q: q["scen"]):
            if r["role"] == "음성":
                continue
            ds = sorted((p["dev_person"] for p in r["people"]), reverse=True)
            base = float(np.median(ds))
            n_out = sum(1 for d in ds if d >= max(2 * base, D_ALLOW))   # 뚜렷한 이탈자
            labels.append(f"{pkg}\n{S(r)}"); intended.append(r["k"])
            detected.append(n_out); colors.append(ROLE_COL[r["role"]])
    x = np.arange(len(labels)); w = 0.38
    ax.bar(x - w/2, intended, w, color="#888", label="각본상 이탈 인원")
    ax.bar(x + w/2, detected, w, color="#c0504d", label="측정된 뚜렷한 이탈자")
    for i, (a, b) in enumerate(zip(intended, detected)):
        ax.text(x[i] - w/2, a + .08, str(a), ha="center", fontsize=9)
        ax.text(x[i] + w/2, b + .08, str(b), ha="center", fontsize=9)
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("인원 (명)"); ax.set_ylim(0, max(detected + intended) + 1.2)
    ax.legend(frameon=False, fontsize=9.5, loc="upper left")
    ax.set_title("그림 4.  각본상 이탈 인원 vs 측정된 이탈자 수\n"
                 "판정선: 중앙값의 2배 또는 d_allow 이상", fontsize=11.5, pad=10)
    fig.savefig(OUT / "fig4_intended_vs_detected.png"); plt.close(fig)


# ------------------------------------------------ 그림 4. EPFI 분포
def fig_dist():
    fig, axes = plt.subplots(1, 2, figsize=(12.6, 4.4), sharey=True)
    for ax, pkg in zip(axes, PKGS):
        rs = sorted(rows_of(pkg), key=lambda r: r["scen"])
        data = [[p["epfi_person"] for p in r["people"]] for r in rs]
        bp = ax.boxplot(data, patch_artist=True, widths=.55,
                        medianprops=dict(color="#222"))
        for b, r in zip(bp["boxes"], rs):
            b.set_facecolor(ROLE_COL[r["role"]]); b.set_alpha(.35)
        for i, (vals, r) in enumerate(zip(data, rs), start=1):
            ax.scatter([i] * len(vals), vals, s=16, color=ROLE_COL[r["role"]],
                       alpha=.8, zorder=3)
        ax.set_xticklabels([f"{S(r)}\n{r['role']}" for r in rs], fontsize=9)
        ax.set_title(PKG_LABEL[pkg], fontsize=11, pad=8)
    axes[0].set_ylabel(f"사람별 EPFI (d_allow {D_ALLOW}m)")
    fig.suptitle("그림 5.  사람별 EPFI 분포 — 0 점에 몰려 하한이 포화한다",
                 fontsize=12.5, y=1.02)
    fig.savefig(OUT / "fig5_epfi_distribution.png"); plt.close(fig)



# ------------------------------------------------ 그림 1. 이탈 배수 (본문 핵심)
def fig_ratio():
    """이탈 배수 = 그 사람의 이탈 ÷ 그 시나리오 중앙값.

    절대 이탈거리는 패키지·매핑에 따라 기준선이 달라(1·3층 중앙 2.2m vs
    3층1차 6.9m) 그대로 비교할 수 없다. 중앙값으로 정규화하면 "이 시나리오에서
    누가 유독 벗어났나"만 남는다 — 각본이 묻는 바로 그 질문이다.
    """
    fig, axes = plt.subplots(1, 2, figsize=(12.6, 4.8), sharey=True)
    for ax, pkg in zip(axes, PKGS):
        rs = sorted(rows_of(pkg), key=lambda r: r["scen"])
        for i, r in enumerate(rs):
            ds = sorted((p["dev_person"] for p in r["people"]), reverse=True)
            med = float(np.median(ds))
            rt = [d / med for d in ds]
            ax.scatter([i] * len(rt), rt, s=26, color=ROLE_COL[r["role"]],
                       alpha=.75, zorder=3)
            if r["k"]:
                ax.scatter([i] * r["k"], rt[:r["k"]], s=120, facecolors="none",
                           edgecolors="#111", linewidths=1.3, zorder=4)
            ax.text(i, rt[0] + .18, f"{rt[0]:.1f}×", ha="center", fontsize=8.5,
                    color=ROLE_COL[r["role"]])
        ax.axhline(3.0, color="#2a7d3f", ls="--", lw=1.2)
        ax.set_xticks(range(len(rs)))
        ax.set_xticklabels([f"{S(r)}\n{r['role']}" + (f" k={r['k']}" if r["k"] else "")
                            for r in rs], fontsize=9)
        ax.set_title(PKG_LABEL[pkg], fontsize=11, pad=8)
    axes[0].set_ylabel("이탈 배수 (그 사람 ÷ 시나리오 중앙값)")
    axes[0].text(-0.4, 3.12, "3배", fontsize=9, color="#2a7d3f")
    axes[0].legend(handles=[Patch(color=v, label=k) for k, v in ROLE_COL.items()]
                   + [plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="none",
                                 markeredgecolor="#111", markersize=10,
                                 label="각본상 이탈 k명")],
                   frameon=False, fontsize=9, loc="upper right", ncol=2)
    fig.suptitle("그림 1.  이탈 배수로 본 양성·음성 대조 — 재구성(사람) 기준",
                 fontsize=12.5, y=1.02)
    fig.savefig(OUT / "fig1_deviation_ratio.png"); plt.close(fig)


# ------------------------------------------------ 그림 3. 대피 소요시간
def fig_evac():
    fig, axes = plt.subplots(1, 2, figsize=(12.6, 4.4), sharey=True)
    for ax, pkg in zip(axes, PKGS):
        rs = sorted(rows_of(pkg), key=lambda r: r["scen"])
        data, lab = [], []
        for r in rs:
            ev = [p["evac_sec"] for p in r["people"] if p.get("evac_sec") is not None]
            data.append(ev or [0]); lab.append(f"{S(r)}\n{r['role']} ({len(ev)}명)")
        bp = ax.boxplot(data, patch_artist=True, widths=.55,
                        medianprops=dict(color="#222"))
        for b, r in zip(bp["boxes"], rs):
            b.set_facecolor(ROLE_COL[r["role"]]); b.set_alpha(.35)
        for i, (vals, r) in enumerate(zip(data, rs), start=1):
            ax.scatter([i] * len(vals), vals, s=16, color=ROLE_COL[r["role"]],
                       alpha=.8, zorder=3)
        ax.set_xticklabels(lab, fontsize=8.5)
        ax.set_title(PKG_LABEL[pkg], fontsize=11, pad=8)
    axes[0].set_ylabel("대피 소요시간 (초) — 첫 관측 → 출구 통과")
    fig.suptitle("그림 3.  개인별 대피 소요시간 — 같은 재구성에서 함께 나오는 값",
                 fontsize=12.5, y=1.02)
    fig.savefig(OUT / "fig3_evac_time.png"); plt.close(fig)


# ------------------------------------------------ CSV
def csv_out():
    import csv
    with open(HERE / "epfi_by_person.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["패키지", "시나리오", "역할", "각본_이탈인원", "person_id",
                    "관측", "카메라수", "트랙렛수", "지속s",
                    "이탈_트랙렛배정_m", "이탈_사람배정_m",
                    f"EPFI_트랙렛(d={D_ALLOW})", f"EPFI_사람(d={D_ALLOW})", "배정경로"])
        for r in ROWS:
            for p in r["people"]:
                w.writerow([r["pkg"], r["scen"], r["role"], r["k"], p["pid"],
                            p["obs"], p["cams"], p["n_tracklets"], f"{p['dur']:.1f}",
                            f"{p['dev_track']:.3f}", f"{p['dev_person']:.3f}",
                            f"{p['epfi_track']:.1f}", f"{p['epfi_person']:.1f}",
                            p["route"]])


fig_ratio(); fig_control(); fig_evac(); fig_topk(); fig_dist()
fig_assignment()      # 부록 — 수정 전 구현 기록
csv_out()
print("완료:", sorted(os.listdir(OUT)))
