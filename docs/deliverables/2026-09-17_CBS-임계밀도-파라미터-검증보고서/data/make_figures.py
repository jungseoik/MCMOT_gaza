# -*- coding: utf-8 -*-
"""CBS 임계밀도·가중치 민감도 — 그림 생성. cbs_sweep.py 가 만든 cbs.json 을 읽는다."""
import json, os
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.family"] = ["NanumGothic", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 150
plt.rcParams["savefig.bbox"] = "tight"
plt.rcParams["axes.grid"] = True
plt.rcParams["grid.alpha"] = 0.25

BASE = "docs/deliverables/2026-09-17_CBS-임계밀도-파라미터-검증보고서"
OUT = f"{BASE}/img"
SRC = os.environ.get("CBS_JSON", f"{BASE}/data/cbs_sweep.json")
D = json.load(open(SRC))
RHOS, ROWS = D["rhos"], D["rows"]

INTENT = {  # floor4 시나리오별 촬영 의도
 "scenario_01": ("IDR", "권장경로+우측 우회"),
 "scenario_02": ("IDR", "매우 느린 속도"),
 "scenario_03": ("IDR", "2초 멈췄다 움직이기"),
 "scenario_04": ("EPFI", "2명 완전 빙 돌아"),
 "scenario_05": ("EPFI", "2명 복귀 후 재진입"),
 "scenario_06": ("EPFI", "1명 빙빙 돌기"),
 "scenario_07": ("CBS", "2열로 들어가기"),
 "scenario_08": ("CBS", "문 근처 밀집 2회"),
 "scenario_09": ("CBS", "5명+중간 5명 합류"),
 "scenario_10": ("SEI", "7:3 분산"),
 "scenario_11": ("SEI", "7:3 + 2명 재진입"),
}
CBS_SET = [k for k, v in INTENT.items() if v[0] == "CBS"]
F4 = {r["label"].split()[-1]: r for r in ROWS if r["floor"] == "floor4"}
REC_LO, REC_HI = 1.0, 1.5     # 권장 구간

# ---------------------------------------------- 그림 1. CBS 정의
def fig_definition():
    s = F4["scenario_08"]["series"]
    t = np.array([x[0] for x in s]); rho = np.array([x[1].get("b1", 0.0) for x in s])
    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    ax.plot(t, rho, color="#222", lw=1.6, label="병목 b1 실측 밀도  ρ(t)")
    sw = F4["scenario_08"]["sweep"]
    for r, c in [(2.0, "#c0504d"), (1.5, "#2a7d3f"), (1.0, "#2f6fb0")]:
        ax.axhline(r, color=c, ls="--", lw=1.2)
        cbs_b1 = sw[str(r)]["per"]["b1"]["cbs"]          # 엔진 산출값 (좌리만 합)
        osec = sw[str(r)]["per"]["b1"]["over"]
        ax.text(t[-1]*1.02, r, f"  ρcrit {r}\n  CBS {cbs_b1:.2f} · 초과 {osec:.0f}초",
                va="center", fontsize=8.5, color=c)
    ax.fill_between(t, 1.5, rho, where=(rho > 1.5), color="#2a7d3f", alpha=0.18)
    ax.set_xlabel("경보 후 경과 시간 (초)"); ax.set_ylabel("구역 밀도 ρ (명/㎡)")
    ax.set_xlim(0, t[-1]*1.42); ax.set_ylim(0, 3.8)
    ax.legend(loc="upper left", frameon=False, fontsize=9.5)
    ax.set_title("그림 1.  CBS 의 정의 — 임계밀도 초과분의 시간 적분\n"
                 "시나리오 S08(문 근처 밀집 2회) · 병목 b1. 음영은 ρcrit=1.5 일 때의 적분 면적",
                 fontsize=11, pad=10)
    fig.savefig(f"{OUT}/fig1_cbs_definition.png"); plt.close(fig)

# ---------------------------------------------- 그림 2. ρcrit 스윕
def fig_sweep():
    fig, ax = plt.subplots(figsize=(9.5, 5.4))
    ax.axvspan(REC_LO, REC_HI, color="#2a7d3f", alpha=0.08, zorder=0)
    COL = {"scenario_07": "#e08a2e", "scenario_08": "#c0504d", "scenario_09": "#7b4fa0"}
    for k in sorted(F4):
        y = [F4[k]["sweep"][str(r)]["total"] for r in RHOS]
        if k in CBS_SET:
            ax.plot(RHOS, y, color=COL[k], lw=2.4, marker="o", ms=4.5, zorder=3,
                    label=f"{k.replace('scenario_','S')} — CBS 의도 · {INTENT[k][1]}")
        else:
            ax.plot(RHOS, y, color="#bbbbbb", lw=1.0, zorder=1)
    ax.plot([], [], color="#bbbbbb", lw=1.0, label="그 외 8개 시나리오 (IDR·EPFI·SEI 의도)")
    ax.axvline(2.0, color="#888", ls=":", lw=1.2)
    ax.text(2.06, 6.5, "종전 기본값 2.0", fontsize=9, color="#666")
    ax.text((REC_LO+REC_HI)/2, 28.5, "권장 구간", ha="center", fontsize=9.5, color="#2a7d3f")
    ax.set_xlabel("임계밀도  ρcrit (명/㎡)"); ax.set_ylabel("CBS (명/㎡·초)")
    ax.set_xlim(0.1, 3.6); ax.set_ylim(-0.8, 31)
    ax.legend(frameon=False, fontsize=9, loc="upper right")
    ax.set_title("그림 2.  임계밀도에 따른 CBS 변화 — AI hub 3층 11개 시나리오",
                 fontsize=12, pad=10)
    fig.savefig(f"{OUT}/fig2_rho_sweep.png"); plt.close(fig)

# ---------------------------------------------- 그림 3. 변별력
def fig_discrimination():
    det_i, det_all, ratio = [], [], []
    for r in RHOS:
        vi = [F4[k]["sweep"][str(r)]["total"] for k in CBS_SET]
        vn = [F4[k]["sweep"][str(r)]["total"] for k in F4 if k not in CBS_SET]
        det_i.append(sum(1 for v in vi if v > 0))
        det_all.append(sum(1 for v in vi+vn if v > 0))
        mi, mn = sum(vi)/len(vi), sum(vn)/len(vn)
        ratio.append(mi/mn if mn > 0 else np.nan)
    fig, ax = plt.subplots(figsize=(9.5, 4.8))
    ax.axvspan(REC_LO, REC_HI, color="#2a7d3f", alpha=0.08, zorder=0)
    ax.plot(RHOS, det_i, color="#c0504d", lw=2.2, marker="o", ms=5,
            label="CBS 의도 시나리오 검출 수 (최대 3)")
    ax.plot(RHOS, det_all, color="#999", lw=1.6, marker="s", ms=4,
            label="전체 검출 시나리오 수 (최대 11)")
    ax.set_xlabel("임계밀도  ρcrit (명/㎡)"); ax.set_ylabel("검출 시나리오 수")
    ax.set_ylim(-0.4, 12); ax.set_xlim(0.1, 3.6)
    ax2 = ax.twinx(); ax2.grid(False)
    ax2.plot(RHOS, ratio, color="#2f6fb0", lw=2.0, ls="--", marker="^", ms=5,
             label="대비 = 의도 평균 ÷ 비의도 평균")
    ax2.set_ylabel("대비 (배)", color="#2f6fb0")
    ax2.tick_params(axis="y", colors="#2f6fb0"); ax2.set_ylim(0, 30)
    best = RHOS[int(np.nanargmax(np.where(np.array(det_i) == 3, ratio, np.nan)))]
    ax2.annotate(f"대비 최대 {max(v for v, di in zip(ratio, det_i) if di == 3):.1f}배\n"
                 f"(의도 3/3 유지)", (best, max(v for v, di in zip(ratio, det_i) if di == 3)),
                 textcoords="offset points", xytext=(-95, -10), fontsize=9, color="#2f6fb0")
    h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1+h2, l1+l2, loc="upper center", frameon=False, fontsize=9,
              bbox_to_anchor=(0.5, -0.17), ncol=3)
    ax.set_title("그림 3.  임계밀도에 따른 변별력\n"
                 "낮으면 전건이 걸려 구분이 안 되고, 높으면 의도한 혼잡조차 놓친다",
                 fontsize=11.5, pad=10)
    fig.savefig(f"{OUT}/fig3_discrimination.png"); plt.close(fig)

# ---------------------------------------------- 그림 4. 병목별 실측 밀도 분포
def fig_peaks():
    FL = {"floor4": "3층", "floor5": "1층", "floor6": "3층 1차"}
    COLF = {"floor4": "#cfe0f0", "floor5": "#f7dcc6", "floor6": "#d6ead8"}
    groups, data, cols = [], [], []
    for fl in ("floor4", "floor5", "floor6"):
        rs = [r for r in ROWS if r["floor"] == fl]
        for b in sorted(rs[0]["peaks"]):
            groups.append(f"{FL[fl]}\n{b}")
            data.append([r["peaks"].get(b, 0.0) for r in rs])
            cols.append(COLF[fl])
    fig, ax = plt.subplots(figsize=(10.5, 4.8))
    bp = ax.boxplot(data, patch_artist=True, widths=0.55,
                    medianprops=dict(color="#222"))
    for p, c in zip(bp["boxes"], cols):
        p.set_facecolor(c)
    for i, vals in enumerate(data, start=1):
        ax.scatter([i]*len(vals), vals, s=14, color="#444", alpha=0.55, zorder=3)
    ax.axhspan(REC_LO, REC_HI, color="#2a7d3f", alpha=0.10, zorder=0)
    ax.axhline(2.0, color="#c0504d", ls=":", lw=1.4)
    ax.text(len(data)+0.6, 2.06, "종전 2.0", va="bottom", ha="center", fontsize=9, color="#c0504d")
    ax.text(len(data)+0.6, (REC_LO+REC_HI)/2, "권장\n1.0~1.5", va="center",
            ha="center", fontsize=9, color="#2a7d3f")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor=COLF[f], edgecolor="#555", label=FL[f])
                       for f in ("floor4", "floor5", "floor6")],
              loc="upper left", frameon=False, fontsize=9, ncol=3)
    ax.set_xticklabels(groups, fontsize=8.5)
    ax.set_ylabel("시나리오별 최대 밀도 ρ_peak (명/㎡)")
    ax.set_xlim(0.4, len(data)+1.3)
    ax.set_title("그림 4.  병목별 실측 최대 밀도 분포 — 임계밀도 선택의 근거\n"
                 "1층 병목은 최대 0.58 로, ρcrit 2.0 에서는 어떤 시나리오도 검출되지 않는다",
                 fontsize=11.5, pad=10)
    fig.savefig(f"{OUT}/fig4_peak_density.png"); plt.close(fig)

# ---------------------------------------------- 그림 5. 가중치 선형성
def fig_weight():
    W = [0.5, 1.0, 1.5, 2.0, 3.0]
    CBS = [5.5409, 11.0818, 16.6227, 22.1636, 33.2454]     # 실측 (ρcrit 1.0 · b1)
    OVER = [9.0]*5
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.plot([0, 3.2], [0, 3.2*11.0818], color="#999", ls="--", lw=1,
            label="원점을 지나는 직선 (기울기 = w=1 실측값)")
    ax.scatter(W, CBS, s=70, color="#2f6fb0", zorder=3, edgecolor="white", lw=0.8,
               label="실측 CBS")
    ax2 = ax.twinx(); ax2.grid(False)
    ax2.plot(W, OVER, color="#c0504d", lw=1.8, marker="s", ms=5,
             label="초과 지속시간 (불변)")
    ax2.set_ylabel("초과 지속시간 (초)", color="#c0504d"); ax2.set_ylim(0, 14)
    ax2.tick_params(axis="y", colors="#c0504d")
    ax.set_xlabel("병목 가중치  weight"); ax.set_ylabel("CBS (명/㎡·초)")
    ax.set_xlim(0, 3.2); ax.set_ylim(0, 36)
    h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1+h2, l1+l2, loc="upper left", frameon=False, fontsize=8.5)
    ax.set_title("그림 5.  가중치는 순수 배율 — 검출 여부를 바꾸지 않는다\n"
                 "시나리오 S08 · 병목 b1 · ρcrit 1.0", fontsize=11, pad=10)
    fig.savefig(f"{OUT}/fig5_weight_linearity.png"); plt.close(fig)

os.makedirs(OUT, exist_ok=True)
fig_definition(); fig_sweep(); fig_discrimination(); fig_peaks(); fig_weight()

# ---------------------------------------------- CSV
import csv
with open(f"{BASE}/data/cbs_by_rho.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["패키지", "시나리오", "층", "촬영의도", "최대밀도"] + [f"CBS@{r}" for r in RHOS])
    for r in ROWS:
        scen = r["label"].split()[-1]
        it = INTENT.get(scen, ("", ""))[0] if r["floor"] == "floor4" else ""
        w.writerow([r["label"].rsplit(" ", 1)[0], scen, r["floor"], it,
                    f"{max(r['peaks'].values()):.3f}"]
                   + [f"{r['sweep'][str(x)]['total']:.4f}" for x in RHOS])
print("완료:", sorted(os.listdir(OUT)))
