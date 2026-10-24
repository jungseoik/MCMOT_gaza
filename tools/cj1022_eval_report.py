#!/usr/bin/env python
"""CJ 실훈련(2026-10-22) **피난훈련 평가 보고서** — 발표 영상 끝에 붙는 슬라이드 클립을 만든다.

예전에는 리포트 창 맨 위에 섹션 하나를 끼워 스크롤했지만, 층·출구·건물 관점이 섞이면 한 창에서
읽히지 않는다. 그래서 보고서를 **장(슬라이드) 단위**로 나눠 한 장씩 멈춰 보여 준다.

    1. 종합 평가     — 범위·핵심 숫자·판정(잘된 점/개선 지점)·이상적 피난의 기준
    2. 피난 타임라인 — 층별 대피 개시 → 출구별 통과 시간대 → 피난층 종료 → 최종 종료
    3. 출구별 분석   — 층마다: 출구 분담(실제 vs 설계) · 출구 앞 병목 혼잡(CBS) · 유량 · 해석
    4. 건물 종합     — (여러 층일 때) 4대 지표 층별 + 건물 통합 · 결론 · 개선 제안

평가의 관점: 이상적인 피난은 **출구가 설계대로 고르게 쓰이고(SEI→100) 출구 앞 병목 혼잡이
없는(CBS→0)** 것이다. 그래서 CBS 를 층 합계로만 보지 않고 **출구별로**(출구 앞 병목 구역을
그 출구에 묶어) 보고, 쏠린 출구와 막힌 출구가 같은지 다른지로 원인을 읽는다.

숫자는 손으로 적지 않는다 — 녹화 세션(.db)을 **평가 시각에서 잘라** 지표 엔진으로 다시 돌린다
(④ 리플레이와 같은 엔진·같은 dt_hold 2.0). 피난층은 그 층의 피난층 종료 시각, 1층은 범위의 최종
종료 시각까지. 시각 사실(피난층 종료·최종 종료)은 answers.json 에서 읽는다.
보고서에는 **훈련 사실·결과·해석만** 싣는다 — 카메라·측정 방식·근사 정답 비교 같은 내부 정보는
넣지 않는다(그건 media/vsource/cj/drill-1022/docs/ 소관).

    python tools/cj1022_eval_report.py                 # 전부: 데이터 → 슬라이드 → mp4 → 리포트 클립 합본
    python tools/cj1022_eval_report.py --only 3F_all --stage html   # 슬라이드 HTML 만(브라우저로 열어 확인)

산출 (results/demo/05_CJ1022/_리포트/):
    eval/data_<id>.json      평가 데이터(재계산 결과)
    eval/<id>/slideN.html    슬라이드 원고 · slideN.png 캡처
    eval/<id>.mp4            슬라이드 클립
    <id>.mp4                 ui/<id>.mp4(우리 UI 리포트 화면) + eval/<id>.mp4 — cj1022_package.py --report-dir 가 붙인다
"""
from __future__ import annotations

import argparse
import html as _h
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESS = ROOT / "data/sites/default/sessions"
ANS = json.loads((ROOT / "media/vsource/cj/drill-1022/answers.json").read_text(encoding="utf-8"))
OUT = ROOT / "results/demo/05_CJ1022/_리포트"
CSS_TOKENS = (ROOT / "webui/static/colors_and_type.css").as_uri()

T0_WALL = 14 * 3600 + 59 * 60 + 55          # 영상 00:00 = 실제 14:59:55 (경보·훈련 시작)
SIREN = 5                                    # 사이렌 00:05 (15:00:00)
DT_HOLD = 2.0                                # 영상·리플레이와 같은 기준

# 영상 → (세션, 층, 범위 이름, 판형). 판형 = 영상 크기(패키징 결과와 같아야 이어 붙는다)
OUTPUTS = {
    "16F_floorend": ("sess-1792820885287", ["floor8"], "16층 단독 · 피난층 종료까지", "single"),
    "17F_floorend": ("sess-1792821419865", ["floor7"], "17층 단독 · 피난층 종료까지", "single"),
    "16F1F_final":  ("sess-1792820138660", ["floor8", "floor9"], "16층 → 1층 · 건물 관점 (최종 종료까지)", "wide"),
    "17F1F_final":  ("sess-1792821415890", ["floor7", "floor9"], "17층 → 1층 · 건물 관점 (최종 종료까지)", "wide"),
    "3F_all":       ("sess-1792820130375", ["floor7", "floor8", "floor9"], "건물 전체 · 17층 · 16층 · 1층", "grid"),
}
#           출력 WxH,     뷰포트(css px), 배율
FORMAT = {"single": ((1920, 1102), (1280, 735), 1.5),
          "wide":   ((3840, 1102), (1920, 551), 2.0),
          "grid":   ((3840, 2204), (1280, 735), 3.0)}
NAME = {"floor7": "17층", "floor8": "16층", "floor9": "1층"}
EXIT_NAME = {
    "floor7": {"e1": "e1 · 왼쪽 계단", "e2": "e2 · 오른쪽 계단"},
    "floor8": {"e1": "e1 · 왼쪽 계단", "e2": "e2 · 오른쪽 계단"},
    "floor9": {"exit-0": "출구1 · 서쪽", "exit-1": "출구2 · 북쪽", "exit-2": "출구3 · 남동쪽"},
}
GROUND = "floor9"
SLIDE_SEC = {"summary": 9.0, "timeline": 9.0, "floor": 11.0, "building": 11.0}
FADE = 0.45

# 등급 — webui/static/main/grade.js 와 같은 경계(운영 뷰·리플레이·리포트와 같은 기준)
SCORE_CUTS, CBS_CUTS = [90, 75, 60, 40], [0.5, 3, 10, 30]
SCORE_LABELS, CBS_LABELS = ["우수", "양호", "보통", "미흡", "불량"], ["원활", "양호", "보통", "혼잡", "심각"]


def grade(v, kind="score"):
    if v is None:
        return 0, "—"
    if kind == "cbs":
        r = next((i + 1 for i, c in enumerate(CBS_CUTS) if v <= c), 5)
        return r, CBS_LABELS[r - 1]
    r = next((i + 1 for i, c in enumerate(SCORE_CUTS) if v >= c), 5)
    return r, SCORE_LABELS[r - 1]


def mmss(s) -> str:
    s = int(round(s))
    return f"{s // 60:02d}:{s % 60:02d}"


def kdur(s) -> str:
    s = int(round(s))
    m, ss = divmod(s, 60)
    return (f"{m}분 {ss}초" if ss else f"{m}분") if m else f"{ss}초"


def wall(s) -> str:
    t = T0_WALL + int(round(s))
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def gt(fl):
    return next((g for g in ANS["floors"] if g["floor"] == fl), None)


# ───────────────────────────────────────────────────────── 1) 데이터 (평가 시각에서 잘라 재계산)
def cut_replay(fl: str, sid: str, cut: float) -> dict:
    sys.path.insert(0, str(ROOT))
    from system.config.schema import CameraConfig, SiteConfig
    from system.metrics import recorder
    from system.metrics.engine import MetricsEngine
    from system.metrics.replay import _apply_overrides

    db = SESS / fl / f"{sid}.db"
    meta = recorder.load_meta(str(db))
    site = SiteConfig.model_validate(meta["site_view"])
    cams = [CameraConfig.model_validate(c) for c in meta.get("cameras", [])]
    _apply_overrides(site, {"thresholds": {"dt_hold": DT_HOLD}})
    eng = MetricsEngine(site, cams)
    t0 = float(meta["alarm_ts"])
    eng.start_session(t_alarm=t0, alarm_origins=[tuple(o) for o in (meta.get("alarm_origins") or [])] or None)
    for cam, ts, tr in recorder.iter_calls(str(db)):
        if ts - t0 > cut + 1e-6:
            break
        eng.on_tracks(cam, ts, tr)
    r = eng.stop_session()
    tl = eng.session_timeline()
    tl = [p if isinstance(p, dict) else p.model_dump() for p in tl]

    # 출구별 통과 시각(경보 후 초) — 누적 카운트 타임라인의 증가분
    passes: dict[str, list[float]] = {}
    prev: dict[str, int] = {}
    for p in tl:
        for k, v in (p.get("exit_counts") or {}).items():
            if v > prev.get(k, 0):
                passes.setdefault(k, []).extend([p["ts"] - t0] * (v - prev.get(k, 0)))
            prev[k] = v

    sv = meta["site_view"]
    mid = {e["id"]: ((e["line"][0][0] + e["line"][1][0]) / 2, (e["line"][0][1] + e["line"][1][1]) / 2)
           for e in sv["exits"]}
    bn_exit = {}                                   # 출구 앞 병목 = 중심이 가장 가까운 출구
    for b in sv["bottlenecks"]:
        c = (b.get("shape") or {}).get("center") or [sum(q[0] for q in b["polygon"]) / len(b["polygon"]),
                                                     sum(q[1] for q in b["polygon"]) / len(b["polygon"])]
        bn_exit[b["id"]] = min(mid, key=lambda k: math.dist(c, mid[k]))
    bms = {b.bottleneck_id: b for b in r.bottleneck_metrics}

    exits = []
    for em in r.exit_metrics:
        ts = sorted(passes.get(em.exit_id, []))
        n = len(ts)
        bids = [b for b, e in bn_exit.items() if e == em.exit_id]
        cbs = sum(bms[b].cbs for b in bids) if bids else None
        exits.append({
            "id": em.exit_id, "name": EXIT_NAME[fl].get(em.exit_id, em.exit_id),
            "count": em.actual_count, "share": em.actual_share, "design_share": em.design_share,
            "width_m": em.width_m,
            "first": ts[0] if n else None, "last": ts[-1] if n else None,
            "p10": ts[int(n * 0.1)] if n else None, "p90": ts[min(n - 1, int(n * 0.9))] if n else None,
            "peak_min": max(sum(1 for u in ts if t <= u < t + 60) for t in ts) if n else 0,
            "cbs": cbs,
            "peak_density": max((bms[b].peak_density for b in bids), default=None),
            "over_sec": sum(bms[b].over_threshold_sec for b in bids) if bids else None,
        })
    z = (r.zone_metrics or [None])[0]
    return {
        "floor": fl, "name": NAME[fl], "cut": cut,
        "sei": r.sei, "cbs": r.cbs_total, "epfi": r.epfi_avg,
        "epfis": [p.epfi for p in r.person_metrics if p.epfi is not None],
        "idr_delay": z.response_delay_sec if z else None, "idr": z.idr if z else None,
        "exits": exits,
    }


def build_data(oid: str) -> dict:
    sid, floors, scope, fmt = OUTPUTS[oid]
    ups = [f for f in floors if f != GROUND]
    final = max(gt(f)["final_end_sec"] for f in ups)
    if floors == ups:                              # 피난층 단독 — 그 층 종료까지만
        final_scope = None
        end_scope = gt(ups[0])["floor_end_sec"]
    else:
        final_scope = final
        end_scope = final
    out = {"id": oid, "session": sid, "scope": scope, "format": fmt, "floors": [],
           "end_scope": end_scope, "final_scope": final_scope, "dt_hold": DT_HOLD}
    for fl in floors:
        g = gt(fl)
        cut = g["floor_end_sec"] if g else end_scope
        d = cut_replay(fl, sid, cut)
        if g:
            d.update(role="upper", floor_end=g["floor_end_sec"], final_end=g["final_end_sec"])
        else:
            d.update(role="ground")
        out["floors"].append(d)
        print(f"  {oid} {NAME[fl]} ~{mmss(cut)}  SEI {d['sei']:.1f} CBS {d['cbs']:.2f} "
              f"EPFI {d['epfi'] or 0:.1f} 개시 {d['idr_delay']}", flush=True)
    # 건물 통합 — 서버 건물 롤업(_aggregate_floors)과 같은 정의: SEI 통합분포·CBS 합·EPFI 전원평균
    # design_share 는 층 안 비율이라 층끼리 섞을 수 없다 → 설계용량(폭 × 같은 q_design) 기준으로 다시 모은다
    caps = []
    for d in out["floors"]:
        meta_caps = {e["id"]: (e["width_m"] or 0) for e in d["exits"]}
        caps += [(e["count"], meta_caps[e["id"]]) for e in d["exits"]]
    se, sc = sum(a for a, _ in caps), sum(c for _, c in caps)
    out["building"] = {
        "sei": (1 - 0.5 * sum(abs(a / se - c / sc) for a, c in caps)) * 100 if se and sc else None,
        "cbs": sum(d["cbs"] for d in out["floors"]),
        "epfi": (sum(sum(d["epfis"]) for d in out["floors"]) / max(1, sum(len(d["epfis"]) for d in out["floors"]))),
    }
    return out


# ───────────────────────────────────────────────────────── 2) 해석 문장
def floor_reading(d: dict) -> tuple[list[str], list[str]]:
    """층 하나의 출구 관점 해석 → (관찰, 개선 제안)."""
    ex = [e for e in d["exits"] if e["count"]]
    obs, rec = [], []
    if not ex:
        return ["출구 통과 인원이 없다."], []
    tot = sum(e["count"] for e in d["exits"])
    over = max(d["exits"], key=lambda e: e["share"] - e["design_share"])
    under = min(d["exits"], key=lambda e: e["share"] - e["design_share"])
    dev = (over["share"] - over["design_share"]) * 100
    worst = max(d["exits"], key=lambda e: e["cbs"] or 0)
    g_sei = grade(d["sei"])[1]
    if dev < 10:
        obs.append(f"출구 분담이 설계와 거의 같다 — SEI {d['sei']:.0f}({g_sei}). 출구가 고르게 쓰였다.")
    else:
        obs.append(f"<b>{over['name']}</b>에 {over['share'] * 100:.0f}%가 몰렸다(설계 {over['design_share'] * 100:.0f}%, "
                   f"+{dev:.0f}%p) — SEI {d['sei']:.0f}({g_sei}). "
                   f"<b>{under['name']}</b>의 분담은 {under['share'] * 100:.0f}%에 그쳤다.")
    if (worst["cbs"] or 0) > CBS_CUTS[0]:
        gw = grade(worst["cbs"], "cbs")[1]
        if worst is over and dev >= 10:
            obs.append(f"혼잡도 쏠린 그 출구에서 났다 — {worst['name']} 앞 CBS {worst['cbs']:.1f}({gw}), "
                       f"임계밀도 초과 {worst['over_sec']:.0f}초. <b>쏠림이 병목 혼잡으로 이어졌다.</b>")
            rec.append(f"{d['name']}: {under['name']} 방향으로 유도(안내 방송·유도원)하면 "
                       f"{worst['name']} 앞 혼잡을 덜 수 있다.")
        else:
            span = (worst["p90"] - worst["p10"]) if worst["p10"] is not None else None
            obs.append(f"분담은 {'고르지만' if dev < 10 else '쏠린 출구와 다른'} <b>{worst['name']}</b> 앞이 막혔다 — "
                       f"CBS {worst['cbs']:.1f}({gw}), 임계밀도 초과 {worst['over_sec']:.0f}초"
                       + (f". 이용 인원 80%가 {kdur(span)} 안에 몰려 통과(최대 {worst['peak_min']}명/분)" if span else "")
                       + " — <b>시간 집중이 원인</b>.")
            rec.append(f"{d['name']}: {worst['name']} 진입 시점 분산(시차 피난·유도원 배치)할 것.")
    else:
        obs.append("출구 앞 병목에서 임계밀도를 넘는 혼잡이 거의 없었다.")
    clean = [e for e in d["exits"] if (e["cbs"] or 0) <= CBS_CUTS[0] and e["count"]]
    if clean and (worst["cbs"] or 0) > CBS_CUTS[1]:
        c = clean[0]
        obs.append(f"{c['name']}에서는 {c['count']}명이 지나가는 동안 혼잡이 없었다 — 여유 용량이 남았다.")
    if d["role"] == "upper":
        obs.append(f"{tot}명이 비상구로 층을 빠져나갔다.")
    return obs, rec


# ───────────────────────────────────────────────────────── 3) 슬라이드 HTML
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;background:var(--bg-base);color:var(--text-1);
  font-family:Pretendard,'Noto Sans KR',sans-serif;-webkit-font-smoothing:antialiased}
.page{position:absolute;inset:0;padding:26px 34px 22px;display:flex;flex-direction:column;gap:14px}
.top{display:flex;align-items:baseline;gap:14px;border-bottom:1px solid var(--stroke-2);padding-bottom:10px}
.top .k{color:var(--pia-cyan);font-weight:800;font-size:15px;letter-spacing:.04em}
.top h1{font-size:25px;font-weight:800}
.top .sub{color:var(--text-3);font-size:14px;margin-left:auto;white-space:nowrap}
.num{font-variant-numeric:tabular-nums}
.cards{display:flex;gap:12px}
.card{flex:1;background:var(--bg-2);border:1px solid var(--stroke-2);border-radius:10px;padding:12px 14px}
.card .l{color:var(--text-3);font-size:13px}
.card .v{font-size:27px;font-weight:800;margin-top:4px}
.card .s{color:var(--text-3);font-size:12.5px;margin-top:3px}
.verdict{border-radius:10px;padding:14px 18px;background:var(--bg-1);border:1px solid var(--stroke-2);border-left-width:5px}
.verdict.g-good{border-left-color:#3FB950}.verdict.g-mid{border-left-color:#F5A623}.verdict.g-bad{border-left-color:var(--danger)}
.verdict h2{font-size:19px;font-weight:700;margin-bottom:8px}
.verdict li{font-size:15.5px;line-height:1.55;list-style:none;padding-left:20px;position:relative;color:var(--text-2)}
.verdict li:before{position:absolute;left:0;font-weight:800}
.verdict li.good:before{content:"✓";color:#3FB950}.verdict li.warn:before{content:"!";color:#F5A623;left:5px}
.ideal{font-size:14px;color:var(--text-2);background:var(--bg-1);border:1px dashed var(--stroke-3);border-radius:10px;padding:11px 16px;line-height:1.6}
.ideal b{color:var(--pia-cyan)}
.pill{display:inline-block;font-size:12px;font-weight:700;padding:1px 8px;border-radius:999px;margin-left:6px;vertical-align:2px}
.r1{background:#1f6f3a;color:#c8f7d3}.r2{background:#2a5d2f;color:#d6f5d0}.r3{background:#6b5310;color:#ffe9a8}
.r4{background:#7a2e14;color:#ffd0bd}.r5{background:#7d1b1b;color:#ffc9c9}.r0{background:var(--bg-4);color:var(--text-3)}
table{border-collapse:collapse;width:100%;font-size:14.5px}
th{color:var(--text-3);font-weight:600;font-size:12.5px;text-align:right;padding:6px 8px;border-bottom:1px solid var(--stroke-2)}
td{padding:7px 8px;border-bottom:1px solid var(--stroke-1);text-align:right;font-variant-numeric:tabular-nums}
th:first-child,td:first-child{text-align:left}
td.n{font-weight:700;color:var(--text-1)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:22px;flex:1;min-height:0}
.col{display:flex;flex-direction:column;gap:12px;min-width:0}
.sh{font-size:17px;font-weight:800}.sh .m{font-weight:400;color:var(--text-3);font-size:13.5px;margin-left:8px}
.bars{display:flex;flex-direction:column;gap:9px}
.bar{display:grid;grid-template-columns:118px 1fr 116px;align-items:center;gap:10px;font-size:14px}
.bar .t{height:20px;background:var(--bg-3);border-radius:4px;position:relative;overflow:visible}
.bar .f{position:absolute;left:0;top:0;bottom:0;border-radius:4px}
.bar .d{position:absolute;top:-4px;bottom:-4px;width:3px;background:#fff;border-radius:2px}
.bar .x{color:var(--text-2);font-variant-numeric:tabular-nums;text-align:right}
.legend{font-size:12px;color:var(--text-3)}
.legend i{display:inline-block;width:3px;height:12px;background:#fff;vertical-align:-1px;margin:0 4px 0 10px}
.read{background:var(--bg-1);border:1px solid var(--stroke-2);border-radius:10px;padding:12px 16px}
.read li{list-style:none;font-size:14.5px;line-height:1.55;color:var(--text-2);padding-left:16px;position:relative;margin-bottom:3px}
.read li:before{content:"•";position:absolute;left:2px;color:var(--pia-cyan)}
.read li.rec:before{content:"→";color:#F5A623;left:0}
.read b{color:var(--text-1)}
.tl{position:relative;flex:1;min-height:0}
.foot{color:var(--text-3);font-size:12px;margin-top:auto}
"""


def pill(v, kind="score"):
    r, lab = grade(v, kind)
    return f'<span class="pill r{r}">{lab}</span>'


def page(kicker: str, title: str, sub: str, body: str, foot: str = "") -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{CSS_TOKENS}">'
            f'<style>{CSS}</style></head><body><div class="page">'
            f'<div class="top"><span class="k">{kicker}</span><h1>{title}</h1><span class="sub">{sub}</span></div>'
            f'{body}' + (f'<div class="foot">{foot}</div>' if foot else "") + '</div></body></html>')


def slide_summary(D: dict) -> str:
    fl = D["floors"]
    ups = [d for d in fl if d["role"] == "upper"]
    ground = next((d for d in fl if d["role"] == "ground"), None)
    multi = len(fl) > 1
    # 핵심 숫자 카드
    cards = []
    if D["final_scope"] is None:
        u = ups[0]
        cards.append(("피난층 종료(층 비움)", mmss(u["floor_end"]), f"{wall(u['floor_end'])} · 경보 후 {kdur(u['floor_end'])}"))
    else:
        cards.append(("최종 종료(1층 밖 탈출)", mmss(D["final_scope"]),
                      " · ".join(f"{u['name']} {mmss(u['final_end'])}" for u in ups)))
        cards.append(("피난층 종료(층 비움)", " · ".join(f"{u['name']} {mmss(u['floor_end'])}" for u in ups), "그 층 마지막 인원이 비상구 통과"))
    starts = [(u["name"], u["idr_delay"]) for u in ups if u["idr_delay"] is not None]
    if starts:
        first = min(starts, key=lambda s: s[1])
        cards.append(("대피 개시", f"{int(first[1])}초", " · ".join(f"{n} {int(s)}초" for n, s in starts) + " (경보 후)"))
    cards.append(("비상구 탈출", f"{sum(sum(e['count'] for e in u['exits']) for u in ups)}명",
                  " · ".join(f"{u['name']} {sum(e['count'] for e in u['exits'])}" for u in ups) + " (피난층 종료까지)"))
    if ground:
        cards.append(("1층 출구 통과", f"{sum(e['count'] for e in ground['exits'])}명", "1층 재실자·다른 층 인원 포함"))
    card_html = "".join(f'<div class="card"><div class="l">{l}</div><div class="v num">{v}</div><div class="s">{s}</div></div>'
                        for l, v, s in cards)
    # 판정
    good, warn = [], []
    for n, s in starts:
        (good if s <= 30 else warn).append(
            f"{n}: 경보 후 <b>{int(s)}초</b>에 대피 개시" + ("" if s <= 30 else " — 초기 반응이 늦다"))
    for d in fl:
        r = grade(d["sei"])[0]
        over = max(d["exits"], key=lambda e: e["share"] - e["design_share"])
        if r <= 2:
            good.append(f"{d['name']}: 출구가 고르게 쓰임 (SEI {d['sei']:.0f})")
        else:
            warn.append(f"{d['name']}: {over['name']}에 {over['share'] * 100:.0f}% 쏠림 (SEI {d['sei']:.0f}, 설계 {over['design_share'] * 100:.0f}%)")
        worst = max(d["exits"], key=lambda e: e["cbs"] or 0)
        rc = grade(worst["cbs"] or 0, "cbs")[0]
        if rc <= 2:
            good.append(f"{d['name']}: 출구 앞 병목 혼잡 낮음 (최대 CBS {worst['cbs'] or 0:.1f})")
        else:
            warn.append(f"{d['name']}: {worst['name']} 앞 병목 혼잡 (CBS {worst['cbs']:.1f}, 임계 초과 {worst['over_sec']:.0f}초)")
    for u in ups:
        good.append(f"{u['name']}: {kdur(u['floor_end'])}에 층 비움 완료"
                    + (f" · {kdur(u['final_end'])}에 1층 밖 탈출" if D["final_scope"] else ""))
    head = ("전반적으로 <b>우수</b>합니다.", "g-good") if not warn else \
        (("전반적으로 <b>양호</b>하나, 개선 지점이 있습니다.", "g-mid") if len(warn) <= len(good)
         else ("<b>개선이 필요</b>합니다.", "g-bad"))
    li = "".join(f'<li class="good">{t}</li>' for t in good) + "".join(f'<li class="warn">{t}</li>' for t in warn)
    verdict = f'<div class="verdict {head[1]}"><h2>이번 훈련({D["scope"].split(" · ")[0]})은 {head[0]}</h2><ul>{li}</ul></div>'
    ideal = ('<div class="ideal">평가 기준 — 이상적인 피난은 <b>① 경보 직후 빠르게 움직이기 시작하고(IDR)</b> '
             '<b>② 출구가 설계대로 고르게 쓰이고(SEI → 100)</b> <b>③ 출구 앞 병목에 사람이 쌓이지 않고(CBS → 0)</b> '
             '<b>④ 권장 경로를 따라 이동하는(EPFI → 100)</b> 것이다. 출구가 고르게 쓰일수록 한 출구 앞에 사람이 몰리지 않아 '
             '병목 혼잡이 줄어든다 — 그래서 이 보고서는 SEI 와 CBS 를 <b>출구별로</b> 함께 본다.</div>')
    return page("피난훈련 평가 보고서", "종합 평가",
                f"CJ제일제당센터 · 2026-10-22 · {D['scope']}",
                f'<div class="cards">{card_html}</div>{verdict}{ideal}',
                f"경보(훈련 시작) 14:59:55 = 00:00 · 사이렌 00:05 · 시각은 경보 후 경과(분:초)")


def slide_timeline(D: dict, vw: int) -> str:
    fl = D["floors"]
    T = D["final_scope"] or D["end_scope"]
    T_axis = math.ceil((T + 20) / 60) * 60
    W = vw - 68
    lab_w = 190
    X = lambda s: lab_w + (W - lab_w - 10) * s / T_axis
    rows, y = [], 34
    COLS = ["#30DCFB", "#FF9F43", "#A78BFA"]
    for d in fl:
        end = d.get("floor_end") or T
        rows.append(f'<div style="position:absolute;left:0;top:{y}px;width:{lab_w - 14}px;font-weight:800;font-size:17px">{d["name"]}'
                    f'<div style="font-size:12px;color:var(--text-3);font-weight:400">'
                    f'{"피난층" if d["role"] == "upper" else "피난(탈출)층"} · 00:00~{mmss(end)}</div></div>')
        rows.append(f'<div style="position:absolute;left:{X(0)}px;top:{y + 9}px;width:{X(end) - X(0)}px;height:6px;'
                    f'background:var(--bg-4);border-radius:3px"></div>')
        if d.get("idr_delay") is not None and d["role"] == "upper":
            s = d["idr_delay"]
            rows.append(f'<div style="position:absolute;left:{X(s) - 7}px;top:{y + 5}px;width:14px;height:14px;border-radius:50%;'
                        f'background:#3FB950;border:2px solid #121212"></div>'
                        f'<div style="position:absolute;left:{X(s) + 10}px;top:{y - 14}px;font-size:12.5px;color:#7ee39a">대피 개시 {mmss(s)}</div>')
        if d["role"] == "upper":
            for t, txt, col in [(d["floor_end"], f"피난층 종료 {mmss(d['floor_end'])}", "#F5A623")]:
                rows.append(f'<div style="position:absolute;left:{X(t) - 1}px;top:{y - 2}px;width:3px;height:26px;background:{col}"></div>'
                            f'<div style="position:absolute;left:{X(t) + 6}px;top:{y - 14}px;font-size:12.5px;color:{col};white-space:nowrap">{txt}</div>')
        if d["role"] == "upper" and D["final_scope"] and d["final_end"] < T - 5:   # 이 층 인원의 최종 종료
            t = d["final_end"]
            rows.append(f'<div style="position:absolute;left:{X(t) - 1}px;top:{y - 2}px;width:3px;height:26px;background:#ff8a85"></div>'
                        f'<div style="position:absolute;left:{X(t) + 6}px;top:{y + 12}px;font-size:12.5px;color:#ff8a85;white-space:nowrap">'
                        f'1층 밖 탈출 {mmss(t)}</div>'
                        f'<div style="position:absolute;left:{X(d["floor_end"])}px;top:{y + 9}px;width:{X(t) - X(d["floor_end"])}px;'
                        f'height:0;border-top:2px dashed #ff8a85;opacity:.6"></div>')
        y += 34
        for k, e in enumerate(d["exits"]):
            col = COLS[k % 3]
            if e["first"] is None:
                rows.append(f'<div style="position:absolute;left:{lab_w - 4}px;top:{y}px;font-size:12.5px;color:var(--text-3)">'
                            f'{e["name"]} — 통과 없음</div>')
            else:
                rows.append(
                    f'<div style="position:absolute;left:{X(e["first"])}px;top:{y + 8}px;width:{max(2, X(e["last"]) - X(e["first"]))}px;height:2px;background:{col};opacity:.55"></div>'
                    f'<div style="position:absolute;left:{X(e["p10"])}px;top:{y + 3}px;width:{max(4, X(e["p90"]) - X(e["p10"]))}px;height:12px;background:{col};border-radius:3px"></div>'
                    + (f'<div style="position:absolute;left:{X(e["last"]) + 8}px;top:{y}px;font-size:12.5px;color:var(--text-2);white-space:nowrap">'
                       if X(e["last"]) < W - 290 else    # 오른쪽 끝에 닿으면 막대 왼쪽에 붙인다
                       f'<div style="position:absolute;right:{W - X(e["first"]) + 8}px;top:{y}px;font-size:12.5px;color:var(--text-2);white-space:nowrap">')
                    + f'{e["name"]} <b class="num" style="color:#fff">{e["count"]}명</b> · {mmss(e["first"])}~{mmss(e["last"])}</div>')
            y += 24
        y += 18
    ticks = "".join(
        f'<div style="position:absolute;left:{X(t)}px;top:0;bottom:22px;width:1px;background:var(--stroke-1)"></div>'
        f'<div style="position:absolute;left:{X(t) - 16}px;bottom:0;font-size:12px;color:var(--text-3)" class="num">{mmss(t)}</div>'
        for t in range(0, T_axis + 1, 60))
    final = ""
    if D["final_scope"]:
        final = (f'<div style="position:absolute;left:{X(T) - 1}px;top:0;bottom:22px;width:3px;background:var(--danger)"></div>'
                 f'<div style="position:absolute;left:{X(T) - 150}px;top:2px;font-size:13px;color:#ff8a85;font-weight:700;text-align:right;width:140px">'
                 f'최종 종료 {mmss(T)}</div>')
    body = (f'<div class="legend">굵은 막대 = 출구 이용 인원 80%가 지나간 시간대 · 가는 선 = 첫 통과~마지막 통과'
            f'<span style="margin-left:18px;color:#7ee39a">● 대피 개시</span>'
            f'<span style="margin-left:14px;color:#F5A623">┃ 피난층 종료</span>'
            + ('<span style="margin-left:14px;color:#ff8a85">┃ 최종 종료</span>' if final else "") + '</div>'
            f'<div class="tl">{ticks}{"".join(rows)}{final}</div>')
    return page("피난훈련 평가 보고서", "피난 타임라인", f"{D['scope']} · 경보 후 경과(분:초)", body)


def floor_panel(d: dict, D: dict) -> str:
    end = d.get("floor_end") if d["role"] == "upper" else (D["final_scope"] or D["end_scope"])
    tot = sum(e["count"] for e in d["exits"])
    COLS = ["#30DCFB", "#FF9F43", "#A78BFA"]
    bars = "".join(
        f'<div class="bar"><span>{e["name"]}</span><div class="t">'
        f'<div class="f" style="width:{e["share"] * 100:.1f}%;background:{COLS[k % 3]}"></div>'
        f'<div class="d" style="left:calc({e["design_share"] * 100:.1f}% - 1px)"></div></div>'
        f'<span class="x"><b style="color:#fff">{e["share"] * 100:.0f}%</b> / 설계 {e["design_share"] * 100:.0f}%</span></div>'
        for k, e in enumerate(d["exits"]))
    trs = ""
    for e in d["exits"]:
        dv = (e["share"] - e["design_share"]) * 100
        trs += (f'<tr><td>{e["name"]}</td><td class="n">{e["count"]}명</td>'
                f'<td style="color:{"#ff9a6b" if dv >= 10 else ("#8fb7ff" if dv <= -10 else "inherit")}">{dv:+.0f}%p</td>'
                f'<td>{(mmss(e["p10"]) + "~" + mmss(e["p90"])) if e["p10"] is not None else "—"}</td>'
                f'<td>{e["peak_min"]}</td>'
                f'<td class="n">{(e["cbs"] or 0):.1f}{pill(e["cbs"] or 0, "cbs")}</td>'
                f'<td>{(e["over_sec"] or 0):.0f}초</td>'
                f'<td>{(e["peak_density"] or 0):.2f}</td></tr>')
    tbl = ('<table><thead><tr><th>출구</th><th>인원</th><th>분담 편차</th><th>80% 통과 시간대</th>'
           '<th>최대 명/분</th><th>출구 앞 CBS</th><th>임계 초과</th><th>최대 밀도 명/㎡</th></tr></thead>'
           f'<tbody>{trs}</tbody></table>')
    obs, rec = floor_reading(d)
    read = "".join(f"<li>{t}</li>" for t in obs) + "".join(f'<li class="rec">{t}</li>' for t in rec)
    return (f'<div class="col"><div class="sh">{d["name"]} — 출구 {len(d["exits"])}곳 · {tot}명'
            f'<span class="m">00:00 ~ {mmss(end)} · SEI {d["sei"]:.0f}{pill(d["sei"])} · CBS {d["cbs"]:.1f}{pill(d["cbs"], "cbs")}</span></div>'
            f'<div class="bars">{bars}</div>'
            f'<div class="legend">막대 = 실제 분담 · <i></i>흰 눈금 = 설계 분담(출구 폭 비례)</div>'
            f'{tbl}<div class="read"><ul>{read}</ul></div></div>')


def slide_floor(D: dict, ds: list[dict]) -> str:
    body = (f'<div class="grid2">{"".join(floor_panel(d, D) for d in ds)}</div>' if len(ds) > 1
            else floor_panel(ds[0], D))
    names = " · ".join(d["name"] for d in ds)
    return page("피난훈련 평가 보고서", f"출구별 분석 — {names}", D["scope"], body,
                "CBS = 출구 앞 병목 구역에서 임계밀도(0.8명/㎡)를 넘긴 혼잡의 시간 누적 · 0 이 이상적 · "
                "분담 편차 = 실제 분담 − 설계 분담")


def slide_building(D: dict) -> str:
    fl = D["floors"]
    B = D["building"]
    ups = [d for d in fl if d["role"] == "upper"]
    ground = next((d for d in fl if d["role"] == "ground"), None)
    cols = "".join(f"<th>{d['name']}</th>" for d in fl) + "<th>건물 통합</th>"
    starts = [(d["name"], d["idr_delay"]) for d in ups if d["idr_delay"] is not None]
    first = min(starts, key=lambda s: s[1]) if starts else None

    def cell_start(d):
        if d["role"] == "upper":
            return f"{int(d['idr_delay'])}초 ({mmss(d['idr_delay'])})" if d["idr_delay"] is not None else "미개시"
        return (f"{mmss(d['idr_delay'])} 흐름 형성" if d["idr_delay"] is not None else "—")
    rows = [
        ("대피 개시 (IDR)", [cell_start(d) for d in fl],
         f"{first[0]} {int(first[1])}초" if first else "—"),
        ("출구 균등 사용 (SEI)", [f"{d['sei']:.0f}{pill(d['sei'])}" for d in fl], f"{B['sei']:.0f}{pill(B['sei'])}"),
        ("병목 혼잡 (CBS)", [f"{d['cbs']:.1f}{pill(d['cbs'], 'cbs')}" for d in fl], f"{B['cbs']:.1f}{pill(B['cbs'], 'cbs')}"),
        ("경로 충실도 (EPFI)", [f"{d['epfi']:.0f}{pill(d['epfi'])}" if d["epfi"] is not None else "—" for d in fl],
         f"{B['epfi']:.0f}{pill(B['epfi'])}"),
        ("출구 통과 인원", [f"{sum(e['count'] for e in d['exits'])}명" for d in fl], ""),
        ("피난층 종료 / 최종 종료",
         [f"{mmss(d['floor_end'])} / {mmss(d['final_end'])}" if d["role"] == "upper" else f"~ {mmss(D['final_scope'])}" for d in fl],
         f"최종 {mmss(D['final_scope'])}"),
    ]
    trs = "".join(f"<tr><td>{k}</td>" + "".join(f'<td class="n">{v}</td>' for v in vs) + f'<td class="n">{b}</td></tr>'
                  for k, vs, b in rows)
    tbl = f'<table><thead><tr><th>지표</th>{cols}</tr></thead><tbody>{trs}</tbody></table>'
    # 결론
    con, rec = [], []
    if len(starts) >= 2:
        a, b = sorted(starts, key=lambda s: s[1])
        con.append(f"대피 개시는 {a[0]}이 경보 후 {int(a[1])}초로 가장 빨랐고, {b[0]}은 {int(b[1])}초로 "
                   f"<b>{int(b[1] - a[1])}초 늦었다</b>.")
        rec.append(f"{b[0]}: 경보 인지·초기 지휘(자위소방대 첫 안내) 시점 점검.")
    elif first:
        con.append(f"건물 대피는 {first[0]}이 경보 후 <b>{int(first[1])}초</b>에 시작했다"
                   + (f"; 1층 출구 쪽 흐름은 {mmss(ground['idr_delay'])}에 형성 — "
                      f"피난층 인원이 1층에 닿은 뒤다." if ground and ground["idr_delay"] is not None else "."))
    for u in ups:
        con.append(f"{u['name']}: {kdur(u['floor_end'])}에 층을 비우고, 그 인원이 {kdur(u['final_end'])}에 1층 밖으로 나갔다"
                   f" (계단·1층 이동 {kdur(u['final_end'] - u['floor_end'])}).")
    best = max(fl, key=lambda d: (d["sei"] or 0) - 3 * (d["cbs"] or 0))
    worst = min(fl, key=lambda d: (d["sei"] or 0) - 3 * (d["cbs"] or 0))
    con.append(f"이상적 피난(출구 균등·무병목)에 가장 가까운 층은 <b>{best['name']}</b>(SEI {best['sei']:.0f}·CBS {best['cbs']:.1f}), "
               f"가장 먼 층은 <b>{worst['name']}</b>(SEI {worst['sei']:.0f}·CBS {worst['cbs']:.1f}).")
    if ground:
        o = max(ground["exits"], key=lambda e: e["share"] - e["design_share"])
        u = min(ground["exits"], key=lambda e: e["share"] - e["design_share"])
        con.append(f"1층은 {o['name']}으로 {o['share'] * 100:.0f}%(설계 {o['design_share'] * 100:.0f}%)가 나갔고 "
                   f"{u['name']}의 분담은 {u['share'] * 100:.0f}%(설계 {u['design_share'] * 100:.0f}%)에 그쳤다"
                   + (f" — {o['name']} 앞 CBS {o['cbs']:.1f}." if (o["cbs"] or 0) > CBS_CUTS[0] else "."))
        rec.append(f"1층: {u['name']} 이용 유도 — 더 넓은 {u['name']}에 여유가 남는 동안 {o['name']} 앞에 사람이 쌓였다."
                   if u["width_m"] and o["width_m"] and u["width_m"] > o["width_m"] else
                   f"1층: {u['name']} 이용 유도로 {o['name']} 부담 분산.")
    for d in ups:
        rec += floor_reading(d)[1]
    read = "".join(f"<li>{t}</li>" for t in con) + "".join(f'<li class="rec">{t}</li>' for t in rec)
    body = (f'{tbl}<div class="read"><div class="sh" style="margin-bottom:6px">건물 관점 결론</div><ul>{read}</ul></div>')
    return page("피난훈련 평가 보고서", "건물 종합 평가", D["scope"], body,
                "건물 통합: SEI = 전 출구 통과 분포 vs 설계 분포(출구 폭 비례) · CBS = 층 합계 · EPFI = 전원 평균 · "
                "IDR = 가장 먼저 움직인 층 기준")


def slides(D: dict) -> list[tuple[str, str]]:
    fmt = D["format"]
    vw = FORMAT[fmt][1][0]
    out = [("summary", slide_summary(D)), ("timeline", slide_timeline(D, vw))]
    fl = D["floors"]
    if fmt == "wide" and len(fl) == 2:
        out.append(("floor", slide_floor(D, fl)))
    else:
        out += [("floor", slide_floor(D, [d])) for d in fl]
    if len(fl) > 1:
        out.append(("building", slide_building(D)))
    return out


# ───────────────────────────────────────────────────────── 4) 캡처·인코딩
def capture(oid: str, pages: list[tuple[str, str]], fmt: str) -> list[Path]:
    from playwright.sync_api import sync_playwright
    (W, H), (vw, vh), sc = FORMAT[fmt]
    d = OUT / "eval" / oid
    d.mkdir(parents=True, exist_ok=True)
    pngs = []
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True)
        pg = br.new_page(viewport={"width": vw, "height": vh}, device_scale_factor=sc)
        for k, (_kind, h) in enumerate(pages, 1):
            hp = d / f"slide{k}.html"
            hp.write_text(h, encoding="utf-8")
            pg.goto(hp.as_uri())
            pg.wait_for_load_state("networkidle")
            pg.evaluate("document.fonts.ready")
            over = pg.evaluate("document.querySelector('.page').scrollHeight - innerHeight")
            if over > 2:
                print(f"  [warn] {oid} slide{k}: 내용이 화면보다 {over}px 김")
            p = d / f"slide{k}.png"
            pg.screenshot(path=str(p))
            pngs.append(p)
        br.close()
    return pngs


def encode(oid: str, pngs: list[Path], kinds: list[str], fmt: str) -> Path:
    (W, H), _, _ = FORMAT[fmt]
    dst = OUT / "eval" / f"{oid}.mp4"
    args = ["ffmpeg", "-y", "-loglevel", "error"]
    fc, durs = [], []
    for k, (p, kind) in enumerate(zip(pngs, kinds)):
        dur = SLIDE_SEC[kind]
        durs.append(dur)
        args += ["-loop", "1", "-framerate", "24", "-t", f"{dur}", "-i", str(p)]
        fc.append(f"[{k}:v]scale={W}:{H}:flags=lanczos,setsar=1,format=yuv420p,"
                  f"fade=t=in:st=0:d={FADE},fade=t=out:st={dur - FADE}:d={FADE}[s{k}]")
    fc.append("".join(f"[s{k}]" for k in range(len(pngs))) + f"concat=n={len(pngs)}:v=1:a=0[v]")
    args += ["-filter_complex", ";".join(fc), "-map", "[v]", "-r", "24", "-c:v", "libx264",
             "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)]
    subprocess.run(args, check=True)
    print(f"  → {dst.relative_to(ROOT)} ({sum(durs):.0f}s)")
    return dst


def combine(oid: str, fmt: str) -> Path:
    """ui/<id>.mp4(우리 UI 리포트 화면) 뒤에 평가 보고서 슬라이드를 붙인다 → 패키징이 쓰는 <id>.mp4."""
    (W, H), _, _ = FORMAT[fmt]
    ui, ev, dst = OUT / "ui" / f"{oid}.mp4", OUT / "eval" / f"{oid}.mp4", OUT / f"{oid}.mp4"
    ins = [p for p in (ui, ev) if p.exists()]
    args = ["ffmpeg", "-y", "-loglevel", "error"]
    for p in ins:
        args += ["-i", str(p)]
    fc = [f"[{k}:v]fps=24,scale={W}:{H}:force_original_aspect_ratio=decrease,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x121212,setsar=1,format=yuv420p[s{k}]" for k in range(len(ins))]
    fc.append("".join(f"[s{k}]" for k in range(len(ins))) + f"concat=n={len(ins)}:v=1:a=0[v]")
    args += ["-filter_complex", ";".join(fc), "-map", "[v]", "-c:v", "libx264", "-preset", "medium",
             "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)]
    subprocess.run(args, check=True)
    print(f"  → {dst.relative_to(ROOT)} (UI 리포트 + 평가 보고서)")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", action="append", choices=list(OUTPUTS))
    ap.add_argument("--stage", default="all", choices=["data", "html", "video", "all"],
                    help="data: 재계산만 · html: +슬라이드 캡처 · video/all: +mp4·합본")
    ap.add_argument("--reuse-data", action="store_true", help="eval/data_<id>.json 있으면 재계산 생략")
    a = ap.parse_args()
    (OUT / "eval").mkdir(parents=True, exist_ok=True)
    for oid in a.only or list(OUTPUTS):
        dp = OUT / "eval" / f"data_{oid}.json"
        if a.reuse_data and dp.exists():
            D = json.loads(dp.read_text(encoding="utf-8"))
        else:
            D = build_data(oid)
            dp.write_text(json.dumps(D, ensure_ascii=False, indent=1), encoding="utf-8")
        if a.stage == "data":
            continue
        pages = slides(D)
        pngs = capture(oid, pages, D["format"])
        print(f"  {oid}: 슬라이드 {len(pngs)}장")
        if a.stage == "html":
            continue
        encode(oid, pngs, [k for k, _ in pages], D["format"])
        combine(oid, D["format"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
