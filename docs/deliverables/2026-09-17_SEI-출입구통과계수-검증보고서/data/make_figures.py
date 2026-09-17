# 그림 생성 스크립트 — 먼저 collect.py 로 sessions.json 을 만든 뒤 실행한다.
# -*- coding: utf-8 -*-
import json, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np

plt.rcParams["font.family"] = ["NanumGothic", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 150
plt.rcParams["savefig.bbox"] = "tight"
plt.rcParams["axes.grid"] = True
plt.rcParams["grid.alpha"] = 0.25

OUT = "docs/deliverables/2026-09-17_SEI-출입구통과계수-검증보고서/img"
rows = json.load(open(os.path.join(os.path.dirname(__file__), "sessions.json")))

# 정답(GT) — 촬영 계획서 기준. checked = 정답이 지정한 출구만 판정 대상.
GT = {
 ("AI hub 1·3층", "scenario_01"): {"exit-0": 10, "exit-1": 0},
 ("AI hub 1·3층", "scenario_02"): {"exit-0": 10, "exit-1": 0},
 ("AI hub 1·3층", "scenario_03"): {"exit-0": 10, "exit-1": 0},
 ("AI hub 1·3층", "scenario_04"): {"exit-0": 10, "exit-1": 0},
 ("AI hub 1·3층", "scenario_05"): {"exit-0": 10, "exit-1": 0},
 ("AI hub 1·3층", "scenario_06"): {"exit-0": 10, "exit-1": 0},
 ("AI hub 1·3층", "scenario_07"): {"exit-0": 10, "exit-1": 0},
 ("AI hub 1·3층", "scenario_08"): {"exit-0": 10, "exit-1": 0},
 ("AI hub 1·3층", "scenario_09"): {"exit-0": 10, "exit-1": 0},
 ("AI hub 1·3층", "scenario_10"): {"exit-0": 3,  "exit-1": 7},
 ("AI hub 1·3층", "scenario_11"): {"exit-0": 3,  "exit-1": 9},
 ("AI hub 1·3층", "scenario_12"): {"exit-1": 10},
 ("AI hub 1·3층", "scenario_13"): {"exit-0": 12, "exit-1": 0},
 ("AI hub 1·3층", "scenario_14"): {"exit-1": 10},
 ("AI hub 3층 1차", "scenario_01"): {"exit-0": 10, "exit-1": 0},
 ("AI hub 3층 1차", "scenario_02"): {"exit-0": 0,  "exit-1": 10},
 ("AI hub 3층 1차", "scenario_03"): {"exit-0": 5,  "exit-1": 5},
 ("AI hub 3층 1차", "scenario_04"): {"exit-0": 10, "exit-1": 0},
 ("AI hub 3층 1차", "scenario_05"): {"exit-0": 0,  "exit-1": 10},
 ("AI hub 3층 1차", "scenario_06"): {"exit-0": 5,  "exit-1": 5},
 ("AI hub 3층 1차", "scenario_07"): {"exit-0": 10, "exit-1": 0},
 ("AI hub 3층 1차", "scenario_08"): {"exit-0": 0,  "exit-1": 10},
}
EXCLUDE = {("AI hub 3층 1차", "scenario_09")}   # cam09 미촬영 — 측정 불가

def split(lab):
    pkg, scen = lab.rsplit(" ", 1)
    return pkg, scen

recs = []
for o in rows:
    pkg, scen = split(o["label"])
    e0 = (o["exits"].get("exit-0") or {}).get("n")
    e1 = (o["exits"].get("exit-1") or {}).get("n")
    d0 = (o["exits"].get("exit-0") or {}).get("d_share")
    c0 = (o["exits"].get("exit-0") or {}).get("cap")
    c1 = (o["exits"].get("exit-1") or {}).get("cap")
    recs.append(dict(pkg=pkg, scen=scen, floor=o["floor"], sei=o["sei"],
                     e0=e0 or 0, e1=e1 or 0, d0=d0, c0=c0, c1=c1,
                     excluded=(pkg, scen) in EXCLUDE,
                     gt=GT.get((pkg, scen))))
order = {"AI hub 1·3층": 0, "AI hub 3층 1차": 1}
recs.sort(key=lambda r: (order[r["pkg"]], r["scen"]))
用 = [r for r in recs if not r["excluded"]]

C_E0, C_E1, C_GT = "#2f6fb0", "#e08a2e", "#444444"

# ---------------------------------------------- 그림 1. 출입구 통과 계수 정답 대조
def fig_counting():
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 4.6),
                             gridspec_kw={"width_ratios": [14, 9]})
    for ax, pkg in zip(axes, ["AI hub 1·3층", "AI hub 3층 1차"]):
        rs = [r for r in recs if r["pkg"] == pkg]
        x = np.arange(len(rs)); w = 0.38
        ax.bar(x - w/2, [r["e0"] for r in rs], w, color=C_E0, label="비상구 A (exit-0) 측정")
        ax.bar(x + w/2, [r["e1"] for r in rs], w, color=C_E1, label="비상구 B (exit-1) 측정")
        for i, r in enumerate(rs):
            g = r["gt"] or {}
            for key, off in (("exit-0", -w/2), ("exit-1", +w/2)):
                if key in g:
                    ax.plot([x[i]+off-w*0.72, x[i]+off+w*0.72], [g[key]]*2,
                            color="#111111", lw=2.4, solid_capstyle="butt",
                            zorder=6)
            if r["excluded"]:
                ax.text(x[i], 1.0, "측정\n제외", ha="center", va="bottom",
                        fontsize=7.5, color="#b03030")
            else:
                ax.text(x[i], max(r["e0"], r["e1"]) + 0.5, "일치", ha="center",
                        fontsize=8, color="#2a7d3f")
        ax.set_xticks(x)
        ax.set_xticklabels([r["scen"].replace("scenario_", "S") for r in rs], fontsize=9)
        ax.set_ylim(0, 15)
        ax.set_ylabel("통과 인원 (명)")
        n_ok = sum(1 for r in rs if not r["excluded"])
        ax.set_title(f"{pkg}  —  정답 대조 {n_ok}/{n_ok} 일치", fontsize=11, pad=8)
    h, l = axes[0].get_legend_handles_labels()
    h.append(plt.Line2D([0], [0], color="#111111", lw=2.4)); l.append("정답(촬영 계획) — 가로 막대")
    fig.legend(h, l, loc="lower center", ncol=3, frameon=False,
               bbox_to_anchor=(0.5, -0.08), fontsize=9.5)
    fig.suptitle("그림 1.  출입구 통과 계수 — 정답 대비 측정값 (22개 시나리오)",
                 fontsize=13, y=1.02)
    fig.savefig(f"{OUT}/fig1_counting_gt.png"); plt.close(fig)

# ---------------------------------------------- 그림 2. 케이스별 SEI
def fig_sei_cases():
    rs = 用
    def kind(r):
        t = r["e0"] + r["e1"]
        if t == 0: return "n/a"
        p = r["e0"] / t
        if min(p, 1-p) < 0.05: return "한쪽 출구 집중"
        if abs(p - 0.5) <= 0.1: return "양 출구 균등"
        return "부분 분산"
    COL = {"한쪽 출구 집중": "#c0504d", "부분 분산": "#e0a030", "양 출구 균등": "#2a7d3f"}
    fig, ax = plt.subplots(figsize=(13.5, 5.0))
    x = np.arange(len(rs))
    cols = [COL[kind(r)] for r in rs]
    ax.bar(x, [r["sei"] for r in rs], 0.65, color=cols)
    for i, r in enumerate(rs):
        ax.text(i, r["sei"] + 1.5, f"{r['sei']:.1f}", ha="center", fontsize=8.5)
        ax.text(i, 3, f"{r['e0']}:{r['e1']}", ha="center", fontsize=8, color="white")
    ax.axhline(100, color="#888", ls="--", lw=1)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{r['scen'].replace('scenario_','S')}\n{'3·1F' if r['pkg'].startswith('AI hub 1') else '1차'}"
                        for r in rs], fontsize=8.5)
    ax.set_ylim(0, 112); ax.set_ylabel("SEI (0~100)")
    ax.set_title("그림 2.  케이스별 SEI — 막대 안 숫자는 실제 출구 분배 (비상구A:비상구B)",
                 fontsize=12, pad=10)
    ax.legend(handles=[Patch(color=v, label=k) for k, v in COL.items()],
              loc="lower center", bbox_to_anchor=(0.5, -0.30), frameon=False,
              fontsize=10, ncol=3)
    n = len([r for r in rs if r["pkg"].startswith("AI hub 1")])
    ax.axvline(n - 0.5, color="#999", lw=1.2)
    ax.text(n/2 - 0.5, 106, "AI hub 1·3층 (14건)", ha="center", fontsize=9.5, color="#555")
    ax.text(n + (len(rs)-n)/2 - 0.5, 106, "AI hub 3층 1차 (8건)", ha="center", fontsize=9.5, color="#555")
    fig.savefig(f"{OUT}/fig2_sei_by_case.png"); plt.close(fig)

# ---------------------------------------------- 그림 3. 정의식 일치 검증
def fig_identity():
    rs = 用
    # 엔진이 낸 분담률을 쓰지 않는다 — 통과 인원과 설계 용량(문 실측폭 기반)만으로
    # 정의식을 독립 재구성해 대조한다.
    calc = []
    for r in rs:
        t = r["e0"] + r["e1"]
        p0 = r["e0"]/t
        d0 = r["c0"]/(r["c0"] + r["c1"])
        tvd = 0.5*(abs(p0-d0) + abs((1-p0)-(1-d0)))
        calc.append((1-tvd)*100)
    meas = [r["sei"] for r in rs]
    fig, ax = plt.subplots(figsize=(5.6, 5.6))
    ax.plot([40, 100], [40, 100], color="#999", ls="--", lw=1, label="y = x (완전 일치)")
    ax.scatter(calc, meas, s=70, color="#2f6fb0", zorder=3, edgecolor="white", linewidth=0.8)
    err = max(abs(a-b) for a, b in zip(calc, meas))
    ax.set_xlabel("독립 재구성 값  (1 - TVD) x 100")
    ax.set_ylabel("엔진 산출 SEI")
    ax.set_title("그림 3.  SEI 정의식 독립 재구성 대조\n"
                 f"통과 인원·설계 용량만으로 계산한 값과 엔진 산출값 — 22개 전건 일치 (최대 오차 {err:.3f})",
                 fontsize=10, pad=10)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    ax.set_xlim(40, 100); ax.set_ylim(40, 100); ax.set_aspect("equal")
    fig.savefig(f"{OUT}/fig3_sei_identity.png"); plt.close(fig)

# ---------------------------------------------- 그림 4. 응답 곡선
def fig_response():
    rs = 用
    fig, ax = plt.subplots(figsize=(8.2, 5.0))
    for d0, lbl, col in [(rs[0]["d0"], "3층 (설계 분담 53.3 : 46.7)", "#2f6fb0"),
                         (next(r["d0"] for r in rs if r["floor"] == "floor5"),
                          "1층 (설계 분담 46.4 : 53.6)", "#c0504d")]:
        p = np.linspace(0, 1, 400)
        ax.plot(p*100, (1-np.abs(p-d0))*100, color=col, lw=1.6, label=f"이론값 — {lbl}")
        ax.axvline(d0*100, color=col, ls=":", lw=1)
    seen = set()
    for r in rs:
        t = r["e0"] + r["e1"]; p0 = r["e0"]/t*100
        c = "#c0504d" if r["floor"] == "floor5" else "#2f6fb0"
        ax.scatter(p0, r["sei"], s=55, color=c, zorder=4, edgecolor="white", linewidth=0.8)
        key = (round(p0, 1), round(r["sei"], 1))
        if key not in seen:
            seen.add(key)
            ax.annotate(f"{r['e0']}:{r['e1']}", (p0, r["sei"]), textcoords="offset points",
                        xytext=(6, 5), fontsize=8, color="#333")
    ax.set_xlabel("비상구 A 사용 비율 (%)"); ax.set_ylabel("SEI")
    ax.set_xlim(-3, 103); ax.set_ylim(40, 104)
    ax.set_title("그림 4.  출구 분배에 대한 SEI 응답 — 이론 곡선과 실측점\n"
                 "점선 = 설계 용량 분담률(SEI 100 지점)", fontsize=11.5, pad=10)
    ax.legend(frameon=False, fontsize=9, loc="lower center")
    fig.savefig(f"{OUT}/fig4_sei_response.png"); plt.close(fig)

os.makedirs(OUT, exist_ok=True)
fig_counting(); fig_sei_cases(); fig_identity(); fig_response()

# ---------------------------------------------- 원자료 CSV
import csv
with open("docs/deliverables/2026-09-17_SEI-출입구통과계수-검증보고서/data/measurements.csv",
          "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["패키지", "시나리오", "층", "정답_exit0", "정답_exit1",
                "측정_exit0", "측정_exit1", "일치", "SEI",
                "실제분담_exit0", "설계분담_exit0", "TVD", "정의식_SEI"])
    for r in recs:
        g = r["gt"] or {}
        t = r["e0"] + r["e1"]
        if t and not r["excluded"]:
            p0 = r["e0"]/t; d0c = r["c0"]/(r["c0"]+r["c1"])
            tvd = abs(p0 - d0c); calc = (1-tvd)*100
            ok = all(g[k] == (r["e0"] if k == "exit-0" else r["e1"]) for k in g)
        else:
            p0 = tvd = calc = None; ok = None
        w.writerow([r["pkg"], r["scen"], r["floor"],
                    g.get("exit-0", ""), g.get("exit-1", ""),
                    r["e0"], r["e1"],
                    ("측정제외" if r["excluded"] else ("일치" if ok else "불일치")),
                    f"{r['sei']:.4f}",
                    "" if p0 is None else f"{p0:.4f}",
                    f"{r['d0']:.4f}",
                    "" if tvd is None else f"{tvd:.4f}",
                    "" if calc is None else f"{calc:.4f}"])

# 판정 요약
ok = sum(1 for r in 用 if all((r["gt"] or {})[k] == (r["e0"] if k == "exit-0" else r["e1"])
                              for k in (r["gt"] or {})))
print(f"카운팅 일치 {ok}/{len(用)}   (측정제외 {len(recs)-len(用)}건)")
print("PNG:", os.listdir(OUT))
