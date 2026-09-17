#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""경로 개정본 IDR·EPFI 검증 그림."""
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
OUT = HERE.parent / "img"; OUT.mkdir(exist_ok=True)
D = json.load(open(HERE / "data.json"))
R = {r["num"]: r for r in D["rows"]}
DA = D["d_allow"]

# EPFI 역할 — 경로 이탈 각본이 양성, 경로가 정상인 각본이 음성
POS = ["04", "05", "06"]
NEG = ["02", "03", "07", "08", "09"]
REF = ["01"]
EXCL = ["10", "11"]          # 두 출구 분산 — 경로 배정이 한 곳으로 몰려 판정 불가
F1 = ["12", "13", "14"]
COL = {"양성": "#c0504d", "음성": "#2f6fb0", "참고": "#888888", "제외": "#bbbbbb"}


def role(n):
    return ("양성" if n in POS else "음성" if n in NEG
            else "참고" if n in REF else "제외")


# ---------------------------------------------- 그림 1. IDR 각본 3건
def fig_idr3():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.4, 4.4),
                                 gridspec_kw={"width_ratios": [1, 1.25]})
    nums = ["01", "02", "03"]
    lab = ["S01\n정상 진행", "S02\n매우 느린 속도", "S03\n2초 멈췄다 이동"]
    delay = [R[n]["idr"]["delay"] for n in nums]
    idr = [R[n]["idr"]["idr"] for n in nums]
    x = np.arange(3)
    a1.bar(x, delay, .55, color=["#2a7d3f", "#e0a030", "#c0504d"])
    for i, (d, v) in enumerate(zip(delay, idr)):
        a1.text(i, d + .7, f"{d:.0f}초", ha="center", fontsize=11, fontweight="bold")
        a1.text(i, d / 2, f"IDR {v:.2f}", ha="center", fontsize=9.5, color="white")
    a1.set_xticks(x); a1.set_xticklabels(lab, fontsize=9.5)
    a1.set_ylabel("피난 개시 지연 (초)"); a1.set_ylim(0, max(delay) * 1.25)
    a1.set_title("각본 의도대로 벌어진다", fontsize=11, pad=8)

    # dt_hold 스윕 (본문 §4 표와 같은 값)
    sweep = {1.0: [13, 22, 32], 2.0: [13, 22, None], 3.0: [13, 22, None], 5.0: [None] * 3}
    for i, n in enumerate(nums):
        ys = [sweep[d][i] for d in sorted(sweep)]
        a2.plot([d for d in sorted(sweep)], [y if y else np.nan for y in ys],
                marker="o", ms=6, lw=2, label=lab[i].replace("\n", " "),
                color=["#2a7d3f", "#e0a030", "#c0504d"][i])
        for d, y in zip(sorted(sweep), ys):
            if y is None:      # 미개시 — 세 계열이 겹치지 않게 살짝 띄운다
                a2.scatter([d], [1.0 + i * 1.6], marker="x", s=55,
                           color=["#2a7d3f", "#e0a030", "#c0504d"][i])
    a2.axvline(1.0, color="#2a7d3f", ls="--", lw=1.2)
    a2.text(1.08, 8.5, "권장 1.0s", fontsize=9, color="#2a7d3f")
    a2.set_xlabel("연속 유지시간  dt_hold (초)"); a2.set_ylabel("개시 지연 (초)")
    a2.set_ylim(-2, 38); a2.legend(frameon=False, fontsize=9, loc="upper right")
    a2.set_title("dt_hold 스윕 — ✕는 미개시 (v_th 0.5 · a_th 0.707)", fontsize=11, pad=8)
    fig.suptitle("그림 1.  IDR — 속도·정지 각본이 개시 지연으로 나타나는가",
                 fontsize=12.5, y=1.02)
    fig.savefig(OUT / "fig1_idr_scenarios.png"); plt.close(fig)


# ---------------------------------------------- 그림 2. IDR 전 14건
def fig_idr_all():
    nums = sorted(R)
    fig, ax = plt.subplots(figsize=(12.4, 4.4))
    x = np.arange(len(nums))
    d = [R[n]["idr"]["delay"] if R[n]["idr"]["delay"] is not None else 0 for n in nums]
    c = ["#2f6fb0" if R[n]["idr"]["status"] == "started" else "#ddd" for n in nums]
    ax.bar(x, d, .6, color=c)
    for i, n in enumerate(nums):
        if R[n]["idr"]["status"] == "started":
            ax.text(i, d[i] + 1, f"{d[i]:.0f}s", ha="center", fontsize=8.5)
            ax.text(i, 1.5, f"{R[n]['idr']['idr']:.2f}", ha="center", fontsize=8, color="white")
        else:
            ax.text(i, 1.5, "미개시", ha="center", fontsize=8, color="#888", rotation=90)
    ax.set_xticks(x)
    ax.set_xticklabels([f"S{n}\n{R[n]['kind']}" for n in nums], fontsize=8.5)
    ax.set_ylabel("피난 개시 지연 (초)")
    ax.set_title("그림 2.  전 14개 시나리오 IDR — 파랑=개시 판정, 회색=미개시\n"
                 "a_th 0.707 · v_th 0.5 · r_th 0.7 · dt_hold 1.0", fontsize=11.5, pad=10)
    fig.savefig(OUT / "fig2_idr_all.png"); plt.close(fig)


# ---------------------------------------------- 그림 3. EPFI 최저값
def fig_epfi():
    nums = [n for n in sorted(R) if n not in F1]
    fig, ax = plt.subplots(figsize=(12.4, 4.6))
    x = np.arange(len(nums))
    lows = [min((p["epfi"] for p in R[n]["people"]), default=0) for n in nums]
    cols = [COL[role(n)] for n in nums]
    ax.bar(x, lows, .6, color=cols)
    for i, (n, v) in enumerate(zip(nums, lows)):
        ax.text(i, v + 1.5, f"{v:.0f}", ha="center", fontsize=9)
    ax.axhline(30, color="#2a7d3f", ls="--", lw=1.2)
    ax.text(len(nums) - .4, 31.5, "30점", fontsize=9, color="#2a7d3f", ha="right")
    ax.set_xticks(x)
    ax.set_xticklabels([f"S{n}\n{role(n)}" for n in nums], fontsize=8.5)
    ax.set_ylabel(f"그 시나리오의 EPFI 최저값 (d_allow {DA:.0f}m)")
    ax.set_ylim(0, 100)
    ax.legend(handles=[Patch(color=COL[k], label=k) for k in ("양성", "음성", "참고", "제외")],
              frameon=False, fontsize=9.5, ncol=4, loc="upper left")
    ax.set_title("그림 3.  EPFI — 경로 이탈 각본(양성)에서 값이 내려가는가 · ReID 재구성 기준",
                 fontsize=11.5, pad=10)
    fig.savefig(OUT / "fig3_epfi_min.png"); plt.close(fig)


# ---------------------------------------------- 그림 4. d_allow 스윕 분리 마진
DALLOWS = [4, 6, 8, 10, 12, 14, 16, 20, 25]


def fig_margin():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.4, 4.4))
    ep = lambda dev, da: max(0.0, 1 - dev / da) * 100
    for grp, cs, lb in ((POS, "#c0504d", "양성(경로 이탈)"), (NEG, "#2f6fb0", "음성(경로 정상)")):
        for da_i, da in enumerate(DALLOWS):
            pass
        ys = [np.mean([min(ep(p["dev"], da) for p in R[n]["people"]) for n in grp])
              for da in DALLOWS]
        a1.plot(DALLOWS, ys, marker="o", ms=5, lw=2.2, color=cs, label=lb)
    a1.axvline(DA, color="#666", ls=":", lw=1)
    a1.set_xlabel("허용 이탈거리  d_allow (m)"); a1.set_ylabel("EPFI 최저값 평균")
    a1.legend(frameon=False, fontsize=9.5); a1.set_title("(가) 양성·음성 평균", fontsize=11, pad=8)

    m = [min(min(ep(p["dev"], da) for p in R[n]["people"]) for n in NEG)
         - max(min(ep(p["dev"], da) for p in R[n]["people"]) for n in POS) for da in DALLOWS]
    a2.plot(DALLOWS, m, marker="o", ms=5, lw=2.4, color="#7b4fa0")
    a2.axhline(0, color="#222", lw=1.2)
    a2.axvline(DA, color="#666", ls=":", lw=1)
    a2.annotate(f"{m[DALLOWS.index(DA)]:+.0f}점", (DA, m[DALLOWS.index(DA)]),
                textcoords="offset points", xytext=(8, 6), fontsize=10, color="#7b4fa0")
    a2.fill_between([DALLOWS[0], DALLOWS[-1]], 0, max(m) * 1.15, color="#2a7d3f", alpha=.07)
    a2.set_xlabel("허용 이탈거리  d_allow (m)"); a2.set_ylabel("분리 마진 (점)")
    a2.set_title("(나) 음성 최저 − 양성 최고 — 양수면 각본대로", fontsize=11, pad=8)
    fig.suptitle("그림 4.  d_allow 에 따른 양성·음성 분리", fontsize=12.5, y=1.02)
    fig.savefig(OUT / "fig4_margin.png"); plt.close(fig)


# ---------------------------------------------- CSV
def csv_out():
    import csv
    with open(HERE / "by_scenario.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["시나리오", "지표", "각본", "정답 e0", "정답 e1", "측정 e0", "측정 e1",
                    "카운팅", "SEI", "CBS", "IDR 상태", "IDR 지연s", "IDR",
                    "EPFI 역할", f"EPFI 최저(d={DA:.0f}m)", "재구성 인원", "대피 중앙s"])
        for n in sorted(R):
            r = R[n]
            g, m = r["gt_exit"], r["exit"]
            ok = all(gi is None or gi == mi for gi, mi in zip(g, m))
            lows = [p["epfi"] for p in r["people"]]
            ev = [p["evac"] for p in r["people"] if p.get("evac") is not None]
            w.writerow([f"S{n}", r["kind"], r["intent"],
                        g[0] if g[0] is not None else "", g[1] if g[1] is not None else "",
                        m[0], m[1], "일치" if ok else "불일치",
                        f"{r['sei']:.1f}", f"{r['cbs']:.2f}",
                        r["idr"]["status"],
                        f"{r['idr']['delay']:.0f}" if r["idr"]["delay"] is not None else "",
                        f"{r['idr']['idr']:.2f}" if r["idr"]["idr"] is not None else "",
                        role(n) if n not in F1 else "1층",
                        f"{min(lows):.1f}" if lows else "",
                        r["n_persons"],
                        f"{np.median(ev):.1f}" if ev else ""])


fig_idr3(); fig_idr_all(); fig_epfi(); fig_margin(); csv_out()
print("완료:", sorted(os.listdir(OUT)))
