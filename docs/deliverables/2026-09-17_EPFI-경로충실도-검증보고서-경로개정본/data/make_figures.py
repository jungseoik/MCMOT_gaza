#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""EPFI 검증 그림 — 파라미터별 민감도."""
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
D = json.load(open(HERE / "epfi_sweep.json"))
R, DAS, BASE_D, REID = D["rows"], D["d_allows"], D["base_d_allow"], D["reid"]
COL = {"양성": "#c0504d", "음성": "#2f6fb0", "참고": "#888888", "제외": "#cccccc",
       "1층": "#7b4fa0"}
POS = [n for n in R if R[n]["role"] == "양성"]
NEG = [n for n in R if R[n]["role"] == "음성"]
MAIN = [n for n in sorted(R) if R[n]["role"] in ("양성", "음성", "참고", "제외")]
ep = lambda dev, da: max(0.0, 1 - dev / da) * 100
REID_LAB = {
    "cos_th":       ("ReID 코사인 하한 cos_th", "낮추면 덜 닮아도 같은 사람으로 묶는다"),
    "fragment_obs": ("파편 기준 관측 fragment_obs", "이보다 관측이 적은 군집은 '사람'에서 뺀다"),
    "link_tol":     ("제약 완화 link_tol", "물리적으로 불가능한 쌍을 얼마나 봐줄지"),
    "slack_m":      ("매핑 여유 slack_m (m)", "카메라 간 좌표 오차를 얼마나 봐줄지"),
}


# ------------------------------------------------ 그림 1. 기본값 EPFI 최저값
def fig_base():
    fig, ax = plt.subplots(figsize=(12.4, 4.6))
    x = np.arange(len(MAIN))
    lows = [R[n]["base"]["min_epfi"] for n in MAIN]
    ax.bar(x, lows, .6, color=[COL[R[n]["role"]] for n in MAIN])
    for i, v in enumerate(lows):
        ax.text(i, v + 1.8, f"{v:.0f}", ha="center", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels([f"S{n}\n{R[n]['role']}" for n in MAIN], fontsize=8.5)
    ax.set_ylabel(f"그 시나리오의 EPFI 최저값  (d_allow {BASE_D:.0f}m)")
    ax.set_ylim(0, 100)
    ax.legend(handles=[Patch(color=COL[k], label=k) for k in ("양성", "음성", "참고", "제외")],
              frameon=False, fontsize=9.5, ncol=4, loc="upper left")
    ax.set_title("그림 1.  경로 이탈 각본(양성)에서 EPFI 가 내려가는가 — ReID 재구성 기준",
                 fontsize=11.5, pad=10)
    fig.savefig(OUT / "fig1_base.png"); plt.close(fig)


# ------------------------------------------------ 그림 2. d_allow 스윕
def fig_dallow():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.6, 4.5))
    for grp, c, lb in ((POS, "#c0504d", "양성 (경로 이탈 각본)"),
                       (NEG, "#2f6fb0", "음성 (경로 정상)")):
        for n in grp:
            ys = [ep(R[n]["devs"][0], da) for da in DAS]
            a1.plot(DAS, ys, color=c, alpha=.45, lw=1.2)
        ys = [np.mean([ep(R[n]["devs"][0], da) for n in grp]) for da in DAS]
        a1.plot(DAS, ys, color=c, lw=2.8, marker="o", ms=5, label=lb)
    a1.axvline(BASE_D, color="#666", ls="--", lw=1.2)
    a1.text(BASE_D + .3, 92, "기본값 12m", fontsize=9, color="#666")
    a1.set_xlabel("허용 이탈거리  d_allow (m)"); a1.set_ylabel("EPFI 최저값")
    a1.set_ylim(-3, 100); a1.legend(frameon=False, fontsize=9.5, loc="lower right")
    a1.set_title("(가) 굵은 선 = 그룹 평균, 옅은 선 = 개별 시나리오", fontsize=10.5, pad=6)

    m = [min(ep(R[n]["devs"][0], da) for n in NEG)
         - max(ep(R[n]["devs"][0], da) for n in POS) for da in DAS]
    a2.plot(DAS, m, color="#7b4fa0", lw=2.6, marker="o", ms=5)
    best = DAS[int(np.argmax(m))]
    a2.axhline(0, color="#222", lw=1.2)
    a2.axvline(BASE_D, color="#666", ls="--", lw=1.2)
    a2.annotate(f"최대 {max(m):+.0f}점\n(d_allow {best}m)", (best, max(m)),
                textcoords="offset points", xytext=(14, 6), fontsize=9.5, color="#7b4fa0")
    a2.fill_between([DAS[0], DAS[-1]], 0, max(m) * 1.2, color="#2a7d3f", alpha=.07)
    a2.set_xlabel("허용 이탈거리  d_allow (m)"); a2.set_ylabel("분리 마진 (점)")
    a2.set_title("(나) 음성 최저 − 양성 최고 · 양수면 각본대로 갈린 것", fontsize=10.5, pad=6)
    fig.suptitle("그림 2.  d_allow 를 바꾸면 EPFI 가 어떻게 움직이는가", fontsize=12.5, y=1.02)
    fig.savefig(OUT / "fig2_dallow.png"); plt.close(fig)


# ------------------------------------------------ 그림 3. ReID 인자 스윕
def fig_reid():
    fig, axes = plt.subplots(2, 2, figsize=(12.6, 8.0))
    for ax, key in zip(axes.ravel(), ["cos_th", "fragment_obs", "link_tol", "slack_m"]):
        xs = REID[key]
        for grp, c, lb in ((POS, "#c0504d", "양성"), (NEG, "#2f6fb0", "음성")):
            ys = [np.mean([R[n]["reid"][key][str(v)]["min_epfi"]
                           for n in grp if R[n]["reid"][key][str(v)]]) for v in xs]
            ax.plot(xs, ys, color=c, lw=2.4, marker="o", ms=5, label=lb)
        ax2 = ax.twinx(); ax2.grid(False)
        ns = [np.mean([R[n]["reid"][key][str(v)]["n"]
                       for n in MAIN if R[n]["reid"][key][str(v)]]) for v in xs]
        ax2.plot(xs, ns, color="#2a7d3f", lw=1.4, ls=":", marker="s", ms=4)
        ax2.set_ylabel("재구성 인원(평균)", color="#2a7d3f", fontsize=9)
        ax2.tick_params(axis="y", colors="#2a7d3f", labelsize=8)
        ax.set_xlabel(REID_LAB[key][0]); ax.set_ylabel("EPFI 최저값 평균")
        ax.set_ylim(0, 100)
        ax.set_title(REID_LAB[key][1], fontsize=10.5, pad=6)
    axes[0][0].legend(frameon=False, fontsize=9, loc="center left")
    fig.suptitle("그림 3.  ReID 재구성 인자를 바꾸면 EPFI 가 흔들리는가\n"
                 "빨강=양성 · 파랑=음성 (왼쪽 축) · 초록 점선=재구성 인원 (오른쪽 축)",
                 fontsize=12.5, y=1.0)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(OUT / "fig3_reid.png"); plt.close(fig)


# ------------------------------------------------ CSV
def csv_out():
    import csv
    with open(HERE / "epfi_sweep.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["시나리오", "역할", "각본", "바꾼 인자", "값", "재구성 인원",
                    f"EPFI 최저(d={BASE_D:.0f}m)", "EPFI 중앙", "최대 이탈m", "중앙 이탈m",
                    "대피 중앙s", "배정 경로"])
        for n in sorted(R):
            r = R[n]
            def row(tag, val, s):
                if not s: return
                w.writerow([f"S{n}", r["role"], r["intent"], tag, val, s["n"],
                            s["min_epfi"], s["med_epfi"], s["max_dev"], s["med_dev"],
                            s["med_evac"] if s["med_evac"] is not None else "",
                            " ".join(s["routes"])])
            row("(기본값)", "", r["base"])
            for k, vals in REID.items():
                for v in vals:
                    row(k, v, r["reid"][k][str(v)])
    # d_allow 별 최저값도 따로
    with open(HERE / "epfi_by_dallow.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["시나리오", "역할"] + [f"EPFI최저@{d}m" for d in DAS])
        for n in sorted(R):
            w.writerow([f"S{n}", R[n]["role"]]
                       + [f"{ep(R[n]['devs'][0], d):.1f}" for d in DAS])


fig_base(); fig_dallow(); fig_reid(); csv_out()
print("완료:", sorted(os.listdir(OUT)))
