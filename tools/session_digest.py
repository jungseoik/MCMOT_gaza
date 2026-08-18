#!/usr/bin/env python3
"""피난훈련 평가 세션 → 해석용 다이제스트 + 4대 지표 시각화.

세션 결과 JSON(`data/sites/<site>/sessions/[<floor>/]<id>.json`)은 최대 수백 KB
(person_series가 사람 수 × 시계열)라 그대로 읽어 해석하기엔 무겁고, raw 배열에서
"몇 초 시점에 급등" 같은 사실을 눈으로 찾는 것은 부정확하다.

이 도구는 **계산만** 한다 — 파생 사실을 뽑아 수 KB JSON으로 요약하고 차트를
렌더한다. **해석·서술은 에이전트가** `.claude/skills/evac-report` 절차로 수행한다.
(요구사항 §8 결정성·역추적성: 같은 입력 → 같은 다이제스트)

사용:
  python tools/session_digest.py --list
  python tools/session_digest.py <session_id> [--site default] [--floor default]
  python tools/session_digest.py <session_id> --charts docs/reports/훈련평가/img
  python tools/session_digest.py <session_id> --json-only        # 차트 없이

주의 (요구사항이 못 박은 것):
  - D-8: 종합점수·A/B/C 등급은 범위 밖 → 이 도구는 합산 점수를 만들지 않는다.
  - null ≠ 0: SEI null=insufficient_data, zone idr null=not_started(미개시).
    다이제스트는 이를 별도 카운트로 명시한다.
  - §9: 임계값은 고객 미확정 → 적용값을 그대로 실어 보내 해석에 단서를 남긴다.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# dataviz 검증 팔레트 (light surface) — references/palette.md
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
       "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
STATUS = {"good": "#0ca30c", "warning": "#fab219",
          "serious": "#ec835a", "critical": "#d03b3b"}
RISK_COLOR = {"low": STATUS["good"], "mid": STATUS["warning"],
              "high": STATUS["critical"]}


# ────────────────────────────────────────────────────────────── 파일 탐색
def sessions_dir(site: str, floor: str) -> Path:
    base = REPO_ROOT / "data" / "sites" / site / "sessions"
    return base if floor in ("", "default") else base / floor


def find_session(session_id: str, site: str) -> tuple[Path, str]:
    """세션 파일을 층 상관없이 찾아 (경로, floor_id) 반환."""
    base = REPO_ROOT / "data" / "sites" / site / "sessions"
    direct = base / f"{session_id}.json"
    if direct.is_file():
        return direct, "default"
    for sub in sorted(p for p in base.glob("*") if p.is_dir()):
        cand = sub / f"{session_id}.json"
        if cand.is_file():
            return cand, sub.name
    raise SystemExit(f"세션을 찾을 수 없습니다: {session_id} (site={site})")


def list_sessions(site: str) -> list[dict]:
    base = REPO_ROOT / "data" / "sites" / site / "sessions"
    out = []
    for p in sorted(base.rglob("*.json"), key=lambda q: q.stat().st_mtime,
                    reverse=True):
        floor = "default" if p.parent == base else p.parent.name
        try:
            r = json.loads(p.read_text(encoding="utf-8")).get("result", {})
        except (OSError, ValueError):
            continue
        dur = ((r.get("ended_at") or 0) - (r.get("alarm_ts") or 0)) or None
        out.append({
            "session_id": r.get("session_id", p.stem), "floor_id": floor,
            "duration_sec": round(dur, 1) if dur else None,
            "track_count": (r.get("quality") or {}).get("track_count"),
            "sei": r.get("sei"), "epfi_avg": r.get("epfi_avg"),
            "cbs_total": r.get("cbs_total"),
            "zones_not_started": sum(1 for z in r.get("zone_metrics", [])
                                     if z.get("status") != "started"),
            "has_record": p.with_suffix(".db").is_file(),
            "path": str(p.relative_to(REPO_ROOT)),
        })
    return out


# ────────────────────────────────────────────────────────── 파생 사실 계산
def _q(vals: list[float]) -> dict:
    """분포 요약 — 개수·최소·사분위·중앙값·최대."""
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    return {"n": len(s), "min": round(s[0], 2),
            "p25": round(s[len(s) // 4], 2), "median": round(st.median(s), 2),
            "p75": round(s[(3 * len(s)) // 4], 2), "max": round(s[-1], 2),
            "mean": round(st.fmean(s), 2)}


def _surges(timeline: list[dict], key: str, t0: float) -> list[dict]:
    """bottleneck_density에서 상승 구간(연속 증가 후 정점)을 뽑는다.
    '몇 초 시점에 무엇이 급등했나'를 에이전트가 눈으로 찾지 않게 하려는 목적."""
    out = []
    for bn in {k for t in timeline for k in (t.get(key) or {})}:
        series = [(t["ts"] - t0, (t.get(key) or {}).get(bn, 0.0))
                  for t in timeline]
        peak_t, peak_v = max(series, key=lambda p: p[1])
        if peak_v <= 0:
            out.append({"id": bn, "peak_density": 0.0, "peak_at_sec": None,
                        "nonzero_sec": 0.0})
            continue
        nz = [t for t, v in series if v > 0]
        out.append({"id": bn, "peak_density": round(peak_v, 3),
                    "peak_at_sec": round(peak_t, 1),
                    "first_nonzero_sec": round(min(nz), 1),
                    "last_nonzero_sec": round(max(nz), 1),
                    "nonzero_sec": round(len(nz) * _dt(series), 1)})
    return sorted(out, key=lambda d: -d["peak_density"])


def _dt(series: list[tuple[float, float]]) -> float:
    if len(series) < 2:
        return 1.0
    return round(st.median([b[0] - a[0] for a, b in zip(series, series[1:])]), 3)


def build_digest(payload: dict, site: str, floor: str, path: Path,
                 thresholds: dict | None) -> dict:
    r = payload["result"]
    tl = payload.get("timeline") or []
    ps = payload.get("person_series") or {}
    t0 = r.get("alarm_ts") or (tl[0]["ts"] if tl else 0.0)
    dur = (r.get("ended_at") or (tl[-1]["ts"] if tl else t0)) - t0

    zones = r.get("zone_metrics", [])
    persons = r.get("person_metrics", [])
    bns = r.get("bottleneck_metrics", [])
    exits = r.get("exit_metrics", [])
    qual = r.get("quality", {})

    started = [z for z in zones if z.get("status") == "started"]
    not_started = [z for z in zones if z.get("status") != "started"]
    epfi_vals = [p["epfi"] for p in persons if p.get("epfi") is not None]
    epfi_null = [p["global_track_id"] for p in persons if p.get("epfi") is None]

    # 출구 쏠림 — 실제 vs 설계 분담 편차
    exit_dev = sorted(
        ({"exit_id": e["exit_id"], "actual_count": e.get("actual_count", 0),
          "design_capacity": e.get("design_capacity"),
          "actual_share": e.get("actual_share"),
          "design_share": e.get("design_share"),
          "delta": (None if e.get("actual_share") is None
                    or e.get("design_share") is None
                    else round(e["actual_share"] - e["design_share"], 4))}
         for e in exits),
        key=lambda d: -abs(d["delta"] or 0))
    unused = [e["exit_id"] for e in exits
              if e.get("actual_count", 0) == 0 and (e.get("design_share") or 0) > 0]

    # 데이터 신뢰도 판정 — 해석 착수 가능 여부의 게이트
    unmapped = qual.get("unmapped_point_ratio")
    ntr = qual.get("track_count") or len(persons)
    flags = []
    if unmapped is not None and unmapped >= 0.30:
        flags.append(f"맵 투영 실패 비율 {unmapped:.1%} — 좌표 기반 지표 신뢰 곤란")
    elif unmapped is not None and unmapped >= 0.15:
        flags.append(f"맵 투영 실패 비율 {unmapped:.1%} — 주의")
    if ntr < 10:
        flags.append(f"관측 트랙 {ntr}개 — 표본 부족")
    if dur < 30:
        flags.append(f"세션 {dur:.0f}초 — 평가 구간이 짧음")
    reliability = ("insufficient" if any("신뢰 곤란" in f or "표본 부족" in f
                                         for f in flags)
                   else "caution" if flags else "ok")

    return {
        "session": {
            "session_id": r.get("session_id"), "site_id": site,
            "floor_id": floor, "source": str(path.relative_to(REPO_ROOT)),
            "record_db": (str(path.with_suffix(".db").relative_to(REPO_ROOT))
                          if path.with_suffix(".db").is_file() else None),
            "alarm_ts": r.get("alarm_ts"), "ended_at": r.get("ended_at"),
            "duration_sec": round(dur, 1),
            "alarm_origins": r.get("alarm_origins") or (
                [r["alarm_origin"]] if r.get("alarm_origin") else []),
            "calibration_version": r.get("calibration_version"),
            "config_version": r.get("config_version"),
            "generated_at": r.get("generated_at"),
            "timeline_points": len(tl),
        },
        # 요구사항 §9 — 임계값은 고객 미확정. 해석에 단서를 남기려 그대로 싣는다.
        "thresholds_applied": thresholds or {},
        "reliability": {
            "verdict": reliability, "flags": flags,
            "unmapped_point_ratio": unmapped,
            "track_count": ntr,
            "cameras_observed": qual.get("cameras_observed", []),
            "warnings": qual.get("warnings", []),
        },
        "idr": {                                  # 구역별 — 요약값 없음(정의상)
            "zone_count": len(zones),
            "started": len(started), "not_started": len(not_started),
            "not_started_ids": [z["zone_id"] for z in not_started],
            "response_delay_sec": _q([z["response_delay_sec"] for z in started
                                      if z.get("response_delay_sec") is not None]),
            "idr_mps": _q([z["idr"] for z in started if z.get("idr") is not None]),
            "zones": [{
                "zone_id": z["zone_id"], "status": z.get("status"),
                "response_delay_sec": z.get("response_delay_sec"),
                "graph_distance_m": (round(z["graph_distance"], 2)
                                     if z.get("graph_distance") is not None else None),
                "idr_mps": (round(z["idr"], 2) if z.get("idr") is not None else None),
                "idr_per_origin": z.get("idr_per_origin", []),
                "participant_ratio": z.get("participant_ratio"),
            } for z in zones],
        },
        "epfi": {
            "avg": r.get("epfi_avg"),
            "distribution": _q(epfi_vals),
            "unassigned_count": len(epfi_null),
            "route_counts": _count_by(persons, "assigned_route_id"),
            "worst": [{
                "global_track_id": p["global_track_id"],
                "epfi": round(p["epfi"], 2), "route": p.get("assigned_route_id"),
                "mean_deviation_m": (round(p["mean_deviation_m"], 2)
                                     if p.get("mean_deviation_m") is not None else None),
                "max_deviation_m": (round(p["max_deviation_m"], 2)
                                    if p.get("max_deviation_m") is not None else None),
                "duration_sec": round(p.get("duration_sec") or 0, 1),
            } for p in sorted((p for p in persons if p.get("epfi") is not None),
                              key=lambda q: q["epfi"])[:8]],
            "deviation_m": _q([p["mean_deviation_m"] for p in persons
                               if p.get("mean_deviation_m") is not None]),
            "series_count": len(ps),
        },
        "cbs": {
            "total": r.get("cbs_total"),
            "bottlenecks": [{
                "bottleneck_id": b["bottleneck_id"],
                "cbs": round(b.get("cbs") or 0, 4),
                "peak_density": b.get("peak_density"),
                "over_threshold_sec": b.get("over_threshold_sec"),
                "risk_level": b.get("risk_level"),
            } for b in sorted(bns, key=lambda d: -(d.get("cbs") or 0))],
            "timeline_surges": _surges(tl, "bottleneck_density", t0),
        },
        "sei": {
            "value": r.get("sei"),
            "insufficient_data": r.get("sei") is None,
            "total_passages": sum(e.get("actual_count", 0) for e in exits),
            "exits": exit_dev,
            "unused_exits": unused,
            "max_abs_delta": (abs(exit_dev[0]["delta"])
                              if exit_dev and exit_dev[0]["delta"] is not None else None),
        },
        "timeline": {                             # 시점 서사용 축약 (최대 40포인트)
            "t_sec": [round(t["ts"] - t0, 1) for t in _thin(tl, 40)],
            "sei": [t.get("sei") for t in _thin(tl, 40)],
            "epfi_avg": [t.get("epfi_avg") for t in _thin(tl, 40)],
            "cbs_total": [round(t.get("cbs_total") or 0, 4) for t in _thin(tl, 40)],
            "zones_started": [t.get("zones_started") for t in _thin(tl, 40)],
            "exit_counts_final": (tl[-1].get("exit_counts") if tl else {}),
        },
    }


def _count_by(rows: list[dict], key: str) -> dict:
    out: dict = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return out


def _thin(rows: list, n: int) -> list:
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


# ────────────────────────────────────────────────────────────────── 차트
def _style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    ko = next((f.name for f in fm.fontManager.ttflist
               if f.name in ("NanumGothic", "NanumBarunGothic",
                             "Noto Sans CJK KR", "Malgun Gothic")), None)
    plt.rcParams.update({
        "font.family": ko or "DejaVu Sans", "axes.unicode_minus": False,
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE, "text.color": INK,
        "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
        "axes.edgecolor": BASELINE, "grid.color": GRID, "grid.linewidth": 0.8,
        "axes.titlesize": 12, "axes.titleweight": "bold", "font.size": 10,
        "axes.spines.top": False, "axes.spines.right": False,
        "legend.frameon": False,
    })
    return plt


def _save(plt, fig, out: Path, name: str) -> str:
    out.mkdir(parents=True, exist_ok=True)
    p = out / name
    fig.tight_layout()
    fig.savefig(p, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(p.relative_to(REPO_ROOT))


def render_charts(dg: dict, out_dir: Path, prefix: str) -> dict:
    plt = _style()
    made: dict = {}
    sid = prefix

    # ① IDR — 구역별. 지연(s)과 IDR(m/s)은 단위가 달라 이중축 대신 small multiples.
    z = dg["idr"]["zones"]
    if z:
        fig, axes = plt.subplots(1, 2, figsize=(9, 0.7 * len(z) + 2.2))
        ids = [q["zone_id"] for q in z]
        y = range(len(z))
        for ax, key, title, unit in (
                (axes[0], "response_delay_sec", "피난 개시 지연", "초"),
                (axes[1], "idr_mps", "IDR (확산 속도)", "m/s")):
            vals = [q[key] if q[key] is not None else 0 for q in z]
            miss = [q[key] is None for q in z]
            ax.barh(list(y), vals, height=0.55,
                    color=[GRID if m else CAT[0] for m, in zip(miss)])
            ax.set_yticks(list(y), ids)
            ax.invert_yaxis()
            ax.set_title(title, color=INK, loc="left")
            ax.set_xlabel(unit)
            ax.xaxis.grid(True); ax.set_axisbelow(True)
            for i, (v, m) in enumerate(zip(vals, miss)):
                ax.text(v, i, "  미개시" if m else f"  {v:.1f}",
                        va="center", fontsize=9,
                        color=STATUS["critical"] if m else INK2)
            ax.margins(x=0.22)
        fig.suptitle("IDR — 구역별 피난 반응 (회색 = 미개시, 값 없음)",
                     color=INK, fontweight="bold", x=0.01, ha="left")
        made["idr"] = _save(plt, fig, out_dir, f"{sid}_idr.png")

    # ② EPFI — 사람별 점수 분포 (단일 계열 → 범례 없음)
    d = dg["epfi"]["distribution"]
    if d.get("n"):
        fig, ax = plt.subplots(figsize=(7.5, 3.4))
        vals = [w["epfi"] for w in dg["epfi"]["worst"]]
        ax.hist(_epfi_all(dg), bins=20, color=CAT[0], edgecolor=SURFACE,
                linewidth=1.2)
        for label, x, col in (("중앙값", d["median"], INK2),
                              ("평균", d["mean"], CAT[1])):
            ax.axvline(x, color=col, linewidth=2, linestyle="--")
            ax.text(x, ax.get_ylim()[1] * 0.96, f" {label} {x:.1f}",
                    color=col, fontsize=9, va="top")
        ax.set_title(f"EPFI — 경로 충실도 분포 (n={d['n']}, 높을수록 좋음)",
                     color=INK, loc="left")
        ax.set_xlabel("EPFI 점수 (0~100)"); ax.set_ylabel("인원")
        ax.yaxis.grid(True); ax.set_axisbelow(True)
        made["epfi"] = _save(plt, fig, out_dir, f"{sid}_epfi.png")

    # ③ CBS — 병목별 밀도 시계열 + 임계선. 초과 구간 음영.
    surges = dg["cbs"]["timeline_surges"]
    if surges:
        fig, ax = plt.subplots(figsize=(8.5, 3.6))
        t = dg["_raw_timeline"]["t"]
        rho = dg["thresholds_applied"].get("rho_crit")
        for i, bn in enumerate(dg["_raw_timeline"]["bn_order"]):
            ser = dg["_raw_timeline"]["bn"][bn]
            col = CAT[i % len(CAT)]
            ax.plot(t, ser, linewidth=2, color=col, label=bn)
            if rho:      # 임계 초과분만 음영 — CBS 적분에 기여하는 면적
                ax.fill_between(t, rho, ser, where=[v > rho for v in ser],
                                color=col, alpha=0.18, linewidth=0)
        if rho:
            ax.axhline(rho, color=STATUS["critical"], linestyle="--", linewidth=1.5)
            ax.annotate(f"ρcrit {rho}", (1.0, rho), xycoords=("axes fraction", "data"),
                        textcoords="offset points", xytext=(-4, 4), ha="right",
                        fontsize=9, color=STATUS["critical"])
        ax.set_title("CBS — 병목별 밀도 추이 (음영 = 임계 초과분, 낮을수록 좋음)",
                     color=INK, loc="left")
        ax.set_xlabel("경보 이후 경과 (초)"); ax.set_ylabel("밀도 (명/㎡)")
        # 범례는 축 밖으로 — 스파이크가 잦아 축 안에 두면 데이터를 가린다
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22),
                  ncols=min(4, len(surges)))
        ax.yaxis.grid(True); ax.set_axisbelow(True)
        ax.margins(x=0.02)
        made["cbs"] = _save(plt, fig, out_dir, f"{sid}_cbs.png")

    # ④ SEI — 출구별 실제 vs 설계 분담 (2계열 → 범례)
    ex = dg["sei"]["exits"]
    if ex:
        fig, ax = plt.subplots(figsize=(7.5, 0.9 * len(ex) + 2.2))
        ids = [e["exit_id"] for e in ex]
        y = range(len(ex))
        h = 0.34
        act = [(e["actual_share"] or 0) * 100 for e in ex]
        des = [(e["design_share"] or 0) * 100 for e in ex]
        ax.barh([i + h / 2 + 0.01 for i in y], act, height=h, color=CAT[0],
                label="실제 통과 분담")
        ax.barh([i - h / 2 - 0.01 for i in y], des, height=h, color=CAT[1],
                label="설계 분담")
        for i, e in enumerate(ex):
            ax.text(act[i], i + h / 2, f"  {act[i]:.0f}% ({e['actual_count']}명)",
                    va="center", fontsize=9, color=INK2)
            ax.text(des[i], i - h / 2, f"  {des[i]:.0f}%", va="center",
                    fontsize=9, color=INK2)
        # Δ는 축 라벨에 합친다 — 축 왼쪽에 따로 쓰면 tick 라벨과 겹친다
        labels = [e["exit_id"] + (f"\nΔ{e['delta']*100:+.0f}%p"
                                  if e["delta"] is not None and abs(e["delta"]) >= 0.10
                                  else "") for e in ex]
        ax.set_yticks(list(y), labels); ax.invert_yaxis()
        for tick, e in zip(ax.get_yticklabels(), ex):
            if e["delta"] is not None and abs(e["delta"]) >= 0.10:
                tick.set_color(STATUS["critical"])
                tick.set_fontweight("bold")
        sv = dg["sei"]["value"]
        ax.set_title("SEI — 비상구 활용 분포"
                     + (f" (SEI {sv:.1f} / 100)" if sv is not None
                        else " (insufficient_data)"),
                     color=INK, loc="left")
        ax.set_xlabel("분담 비율 (%)")
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22), ncols=2)
        ax.xaxis.grid(True); ax.set_axisbelow(True)
        ax.margins(x=0.26)
        made["sei"] = _save(plt, fig, out_dir, f"{sid}_sei.png")

    # ⑤ 타임라인 — 스케일이 다른 3지표를 이중축 대신 공유 x축 small multiples로
    raw = dg["_raw_timeline"]
    if raw["t"]:
        fig, axes = plt.subplots(3, 1, figsize=(8.5, 6.4), sharex=True)
        panels = [
            (axes[0], raw["sei"], "SEI (0~100, 높을수록 좋음)", CAT[0], (0, 100)),
            (axes[1], raw["epfi"], "EPFI 평균 (0~100, 높을수록 좋음)", CAT[2], (0, 100)),
            (axes[2], raw["cbs"], "CBS 누적 (낮을수록 좋음)", CAT[1], None),
        ]
        for ax, ser, title, col, ylim in panels:
            xs = [x for x, v in zip(raw["t"], ser) if v is not None]
            ys = [v for v in ser if v is not None]
            ax.plot(xs, ys, linewidth=2, color=col)
            if ylim:
                ax.set_ylim(*ylim)
            ax.set_title(title, color=INK, loc="left", fontsize=11)
            ax.yaxis.grid(True); ax.set_axisbelow(True)
            gap = [x for x, v in zip(raw["t"], ser) if v is None]
            if gap:
                ax.axvspan(min(gap), max(gap), color=GRID, alpha=0.6, zorder=0)
                lo, hi = ax.get_ylim()
                ax.text(max(gap), lo + (hi - lo) * 0.06, " 산출 불가",
                        fontsize=8, color=MUTED, va="bottom")
        axes[-1].set_xlabel("경보 이후 경과 (초)")
        fig.suptitle("세션 타임라인 — 지표 추이", color=INK, fontweight="bold",
                     x=0.01, ha="left")
        made["timeline"] = _save(plt, fig, out_dir, f"{sid}_timeline.png")

    return made


def _epfi_all(dg: dict) -> list[float]:
    return dg["_raw_epfi"]


# ────────────────────────────────────────────────────────────────── main
def main() -> int:
    ap = argparse.ArgumentParser(description="세션 → 해석용 다이제스트 + 차트")
    ap.add_argument("session_id", nargs="?", help="세션 id (예: sess-1786977645508)")
    ap.add_argument("--site", default="default")
    ap.add_argument("--list", action="store_true", help="세션 목록만 출력")
    ap.add_argument("--charts", default="docs/reports/훈련평가/img",
                    help="차트 출력 디렉토리 (레포 상대경로)")
    ap.add_argument("--json-only", action="store_true", help="차트 렌더 생략")
    ap.add_argument("--out", help="다이제스트 JSON 저장 경로 (미지정 시 stdout)")
    args = ap.parse_args()

    if args.list or not args.session_id:
        rows = list_sessions(args.site)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    path, floor = find_session(args.session_id, args.site)
    payload = json.loads(path.read_text(encoding="utf-8"))

    # 적용 임계값 — site.json에서 (요구사항 §8 역추적성)
    site_json = REPO_ROOT / "data" / "sites" / args.site / "site.json"
    thresholds = {}
    if site_json.is_file():
        sc = json.loads(site_json.read_text(encoding="utf-8"))
        thresholds = dict(sc.get("thresholds") or {})
        for b in sc.get("bottlenecks") or []:      # 병목별 임계밀도·가중치
            if b.get("rho_crit") is not None:
                thresholds.setdefault("rho_crit", b["rho_crit"])
            thresholds.setdefault(f"w_{b['id']}", b.get("weight"))

    dg = build_digest(payload, args.site, floor, path, thresholds)

    # 차트용 원자료 (다이제스트 JSON에는 싣지 않는다 — 컨텍스트 절약)
    tl = payload.get("timeline") or []
    t0 = payload["result"].get("alarm_ts") or (tl[0]["ts"] if tl else 0)
    bn_ids = sorted({k for t in tl for k in (t.get("bottleneck_density") or {})})
    dg["_raw_timeline"] = {
        "t": [round(t["ts"] - t0, 1) for t in tl],
        "sei": [t.get("sei") for t in tl],
        "epfi": [t.get("epfi_avg") for t in tl],
        "cbs": [t.get("cbs_total") or 0 for t in tl],
        "bn_order": bn_ids,
        "bn": {b: [(t.get("bottleneck_density") or {}).get(b, 0.0) for t in tl]
               for b in bn_ids},
    }
    dg["_raw_epfi"] = [p["epfi"] for p in payload["result"].get("person_metrics", [])
                       if p.get("epfi") is not None]

    if not args.json_only:
        try:
            dg["charts"] = render_charts(dg, REPO_ROOT / args.charts,
                                         dg["session"]["session_id"])
        except Exception as e:                     # 차트 실패가 다이제스트를 막지 않게
            dg["charts"] = {}
            dg["chart_error"] = f"{type(e).__name__}: {e}"

    for k in ("_raw_timeline", "_raw_epfi"):
        dg.pop(k, None)

    text = json.dumps(dg, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"다이제스트 저장: {args.out}  ({len(text)/1024:.1f} KB)")
        for k, v in (dg.get("charts") or {}).items():
            print(f"  차트 {k}: {v}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
