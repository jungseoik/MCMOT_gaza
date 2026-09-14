"""핸드오버 리본 — 두 글로벌ID 방식의 차이를 한눈에.

시간축(x) 위에 카메라 5개를 줄(lane)로 깔고, 각 프레임 관측을 배정된 글로벌ID 색으로
칠한다. 같은 색이 여러 카메라 줄을 가로지르면 = 그 사람을 카메라 넘어 계속 따라간 것.
  - zip(조율자): 색이 카메라 줄을 가로질러 이어짐(적극 병합)
  - 우리(경비원): 카메라마다 색이 바뀜(보수·조각)
두 방식이 같은 캡처(같은 트랙)에서 나오므로 정확히 apples-to-apples.

  python tools/gid_compare/ribbon.py --scenario scenario_01 --zip-src <zip>/src
출력: results/gid_compare/<scenario>/ribbon.png
"""
from __future__ import annotations

import argparse
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
from matplotlib.patches import Patch     # noqa: E402
from matplotlib import font_manager      # noqa: E402
import colorsys                          # noqa: E402

# 한글 폰트 (없으면 조용히 기본값)
for _fp in ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",):
    try:
        font_manager.fontManager.addfont(_fp)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=_fp).get_name()
    except Exception:
        pass
plt.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.gid_compare.render import run_ours, run_zip   # noqa: E402


GREY = (0.82, 0.82, 0.82)
_TAB = plt.get_cmap("tab10").colors + plt.get_cmap("Set2").colors  # 18색 뚜렷


def _stats(labels):
    """정체성별 등장 카메라 집합·관측수."""
    cams = defaultdict(set)
    nobs = defaultdict(int)
    for (_, cam, _), v in labels.items():
        if v:
            cams[v].add(cam)
            nobs[v] += 1
    return cams, nobs


def _highlight_colors(labels, topk=10):
    """카메라를 많이 가로지르는 상위 정체성(≥2카메라)만 뚜렷한 색, 나머지는 회색."""
    cams, nobs = _stats(labels)
    multi = [g for g in cams if len(cams[g]) >= 2]
    multi.sort(key=lambda g: (len(cams[g]), nobs[g]), reverse=True)
    color = {}
    for i, g in enumerate(multi[:topk]):
        color[g] = _TAB[i % len(_TAB)]
    return color, cams


def draw_panel(ax, labels, cap, order, title, fps):
    lane_h = 0.82
    step_w = 1.0 / fps
    color, cams = _highlight_colors(labels)
    for li, cam in enumerate(order):
        y = len(order) - 1 - li
        ax.axhspan(y - lane_h / 2, y + lane_h / 2, color=(0.965, 0.965, 0.965), zorder=0)
        run = None
        for snap in cap["observations"]:
            step = snap["step"]
            labs = [labels.get((step, cam, t["local_id"]))
                    for t in snap["cams"].get(cam, {}).get("tracks", [])]
            labs = [l for l in labs if l]
            for lab in labs or [None]:
                if lab is None:
                    continue
                ax.add_patch(plt.Rectangle(
                    (step * step_w, y - lane_h / 2), step_w, lane_h,
                    color=color.get(lab, GREY), ec=None, zorder=2))
            cur = next((l for l in labs if l in color), None)
            if cur and cur != run:                      # 스포트라이트 id 만 텍스트
                ax.text(step * step_w + 0.05, y, cur, fontsize=7, va="center",
                        ha="left", color=(0.1, 0.1, 0.1), zorder=3, fontweight="bold")
            run = cur
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels(list(reversed(order)), fontsize=10)
    ax.set_ylim(-0.6, len(order) - 0.4)
    ax.set_xlim(0, len(cap["observations"]) / fps)
    n_id = len(cams)
    n2 = sum(len(c) >= 2 for c in cams.values())
    n3 = sum(len(c) >= 3 for c in cams.values())
    ax.set_title(f"{title}   —   정체성 {n_id}개  ·  카메라 넘긴 사람: 2+대 {n2}명 / 3+대 {n3}명",
                 fontsize=12.5, fontweight="bold", loc="left")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="scenario_01")
    ap.add_argument("--zip-src", required=True)
    ap.add_argument("--min-conf", type=float, default=0.5)
    ap.add_argument("--checkpoint-frames", type=int, default=7)
    ap.add_argument("--min-frames", type=int, default=2)
    args = ap.parse_args()

    out_dir = ROOT / "results/gid_compare" / args.scenario
    cap = pickle.load(open(out_dir / "capture.pkl", "rb"))
    order = cap["order"]                       # route 순 (cam14→…→cam8)
    fps = float(cap["fps"])

    ours_labels, _ = run_ours(cap, args.min_conf)
    zip_labels, _ = run_zip(cap, Path(args.zip_src), args.checkpoint_frames, args.min_frames)

    fig, axes = plt.subplots(2, 1, figsize=(15, 8), sharex=True)
    draw_panel(axes[0], ours_labels, cap, order,
               "내 구현 (깐깐한 경비원 — 물리게이트·보수)", fps)
    draw_panel(axes[1], zip_labels, cap, order,
               "zip (색안경 벗기는 조율자 — percam_norm·적극병합)", fps)
    fig.suptitle(
        "핸드오버 리본 — 색칠된 막대가 카메라 줄(위→아래)을 '대각선 계단'으로 관통하면 "
        "= 한 사람을 카메라 넘어 계속 이은 것\n"
        f"({args.scenario}: 사람이 cam14→11→10→9→8 순 통과 · 두 방식 동일 캡처·동일 임베딩 · "
        "색=카메라 많이 가로지른 상위 정체성, 회색=단일카메라/미할당)",
        fontsize=12, y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    out = out_dir / "ribbon.png"
    fig.savefig(out, dpi=130)
    print(f"[ribbon] -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
