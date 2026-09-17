#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""IDR 검증 그림 — 파라미터 1개씩 바꿔가며 개시 지연이 어떻게 변하는가."""
import json, os
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.family"] = ["NanumGothic", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 150
plt.rcParams["savefig.bbox"] = "tight"
plt.rcParams["axes.grid"] = True
plt.rcParams["grid.alpha"] = 0.25

HERE = Path(__file__).parent
OUT = HERE.parent / "img"; OUT.mkdir(exist_ok=True)
D = json.load(open(HERE / "idr_sweep.json"))
R, BASE, SW = D["rows"], D["base"], D["sweep"]

IDR3 = ["01", "02", "03"]                      # IDR 을 겨냥한 각본
C3 = {"01": "#2a7d3f", "02": "#e0a030", "03": "#c0504d"}
L3 = {"01": "S01 정상 진행", "02": "S02 매우 느린 속도", "03": "S03 2초 멈췄다 이동"}
PARAM = {
    "v_th":    ("속도 임계 v_th (m/s)", "이 속도 이상이어야 '피난 이동 중'"),
    "a_th":    ("방향 정렬도 임계 a_th (cos)", "경로 방향과 이 정도는 맞아야"),
    "r_th":    ("동시만족 비율 r_th", "구역 인원 중 이 비율이 조건을 채워야"),
    "dt_hold": ("연속 유지시간 dt_hold (s)", "조건이 이 시간 이상 끊기지 않아야"),
}


def delays(num, key):
    xs = SW[key]
    return xs, [R[num]["sweep"][key][str(v)]["delay"] for v in xs]


# ---------------------------------------------- 그림 1. 기본값에서의 전 14건
def fig_base():
    nums = sorted(R)
    fig, ax = plt.subplots(figsize=(12.4, 4.4))
    x = np.arange(len(nums))
    d = [R[n]["base"]["delay"] or 0 for n in nums]
    c = ["#2f6fb0" if R[n]["base"]["status"] == "started" else "#dddddd" for n in nums]
    ax.bar(x, d, .6, color=c)
    for i, n in enumerate(nums):
        b = R[n]["base"]
        if b["status"] == "started":
            ax.text(i, d[i] + 1.2, f"{d[i]:.0f}s", ha="center", fontsize=8.5)
            ax.text(i, 2, f"{b['idr']:.2f}", ha="center", fontsize=8, color="white")
        else:
            ax.text(i, 2, "미개시", ha="center", fontsize=8, color="#999", rotation=90)
    ax.set_xticks(x)
    ax.set_xticklabels([f"S{n}\n{R[n]['kind']}" for n in nums], fontsize=8.5)
    ax.set_ylabel("피난 개시 지연 (초)")
    ax.set_title("그림 1.  현재 기본값에서의 전 14개 시나리오\n"
                 f"v_th {BASE['v_th']} · a_th {BASE['a_th']} · r_th {BASE['r_th']} · dt_hold {BASE['dt_hold']}",
                 fontsize=11.5, pad=10)
    fig.savefig(OUT / "fig1_base_all.png"); plt.close(fig)


# ---------------------------------------------- 그림 2. 인자별 스윕 (IDR 각본 3건)
def fig_sweep():
    fig, axes = plt.subplots(2, 2, figsize=(12.6, 8.2))
    for ax, key in zip(axes.ravel(), ["v_th", "a_th", "r_th", "dt_hold"]):
        for n in IDR3:
            xs, ys = delays(n, key)
            ok = [(x, y) for x, y in zip(xs, ys) if y is not None]
            ng = [x for x, y in zip(xs, ys) if y is None]
            if ok:
                ax.plot([p[0] for p in ok], [p[1] for p in ok],
                        marker="o", ms=5, lw=2, color=C3[n], label=L3[n])
            for i, x in enumerate(ng):        # 미개시는 아래쪽에 ✕
                ax.scatter([x], [-2.5 - IDR3.index(n) * 2.2], marker="x", s=45, color=C3[n])
        ax.axvline(BASE[key], color="#666", ls="--", lw=1.2)
        ax.text(BASE[key], ax.get_ylim()[1] * .96, " 기본값", fontsize=8.5, color="#666",
                va="top")
        ax.set_xlabel(PARAM[key][0]); ax.set_ylabel("개시 지연 (초)")
        ax.set_ylim(-9, 62)
        ax.set_title(PARAM[key][1], fontsize=10.5, pad=6)
    axes[0][0].legend(frameon=False, fontsize=9, loc="upper left")
    fig.suptitle("그림 2.  인자를 하나씩 바꿨을 때 개시 지연 — 아래쪽 ✕ 는 미개시\n"
                 "나머지 인자는 기본값 고정", fontsize=12.5, y=1.0)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(OUT / "fig2_param_sweep.png"); plt.close(fig)


# ---------------------------------------------- 그림 3. 각본 순서가 서는 구간
def fig_order():
    """S01 < S02 < S03 순서가 성립하는 설정 구간을 찾는다."""
    fig, axes = plt.subplots(1, 4, figsize=(13.2, 3.6), sharey=True)
    for ax, key in zip(axes, ["v_th", "a_th", "r_th", "dt_hold"]):
        xs = SW[key]
        ok = []
        for v in xs:
            ds = [R[n]["sweep"][key][str(v)]["delay"] for n in IDR3]
            good = (all(d is not None for d in ds) and ds[0] < ds[1] < ds[2])
            some = sum(1 for d in ds if d is not None)
            ok.append((2 if good else (1 if some == 3 else 0), some))
        ax.bar(range(len(xs)), [o[1] for o in ok], .6,
               color=["#2a7d3f" if o[0] == 2 else "#e0a030" if o[0] == 1 else "#ccc" for o in ok])
        ax.set_xticks(range(len(xs)))
        ax.set_xticklabels([str(v) for v in xs], fontsize=8.5, rotation=45)
        ax.axvline(xs.index(BASE[key]), color="#666", ls="--", lw=1.2)
        ax.set_xlabel(PARAM[key][0], fontsize=9.5)
        ax.set_ylim(0, 3.4)
    axes[0].set_ylabel("개시된 각본 수 (최대 3)")
    from matplotlib.patches import Patch
    axes[0].legend(handles=[Patch(color="#2a7d3f", label="3건 개시 + 순서 성립"),
                            Patch(color="#e0a030", label="3건 개시, 순서 안 맞음"),
                            Patch(color="#ccc", label="일부 미개시")],
                   frameon=False, fontsize=8, loc="upper left")
    fig.suptitle("그림 3.  S01 < S02 < S03 순서가 서는 설정 구간 (점선 = 기본값)",
                 fontsize=12, y=1.04)
    fig.savefig(OUT / "fig3_order_window.png"); plt.close(fig)


# ---------------------------------------------- CSV
def csv_out():
    import csv
    with open(HERE / "idr_sweep.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["시나리오", "지표", "각본", "바꾼 인자", "값",
                    "상태", "개시 지연s", "IDR m/s", "참여율", "거리 D(m)"])
        for n in sorted(R):
            r = R[n]
            for tag, th in [("(기본값)", r["base"])]:
                w.writerow([f"S{n}", r["kind"], r["intent"], tag, "",
                            th["status"], th["delay"] or "", f"{th['idr']:.3f}" if th["idr"] else "",
                            f"{th['ratio']:.2f}" if th["ratio"] is not None else "",
                            f"{th['dist']:.2f}" if th["dist"] else ""])
            for k, vals in SW.items():
                for v in vals:
                    th = r["sweep"][k][str(v)]
                    w.writerow([f"S{n}", r["kind"], r["intent"], k, v,
                                th["status"], th["delay"] or "",
                                f"{th['idr']:.3f}" if th["idr"] else "",
                                f"{th['ratio']:.2f}" if th["ratio"] is not None else "",
                                f"{th['dist']:.2f}" if th["dist"] else ""])


fig_base(); fig_sweep(); fig_order(); csv_out()
print("완료:", sorted(os.listdir(OUT)))
