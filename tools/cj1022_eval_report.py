#!/usr/bin/env python
"""CJ 실훈련(2026-10-22) **피난훈련 평가 보고서** — 발표 영상 끝에 붙는 슬라이드 클립을 만든다.

예전에는 리포트 창 맨 위에 섹션 하나를 끼워 스크롤했지만, 층·출구·건물 관점이 섞이면 한 창에서
읽히지 않는다. 그래서 보고서를 **장(슬라이드) 단위**로 나눠 한 장씩 멈춰 보여 준다.

    1. 종합 평가     — 핵심 시각·인원 + 4대 지표 점수·등급(층별, 여러 층이면 건물 통합) + 등급 기준표
    2. 평가 의견     — 판정(잘된 점/개선 지점)·평가 기준
    3. 피난 타임라인 — 층별 대피 개시 → 출구별 통과 시간대 → 피난층 종료 → 최종 종료
    4. 비상구별 분석 — 층마다: 출구 분담(실제 vs 설계) · 출구 앞 병목 혼잡(CBS) · 유량 · 해석
    5. 건물 종합     — (여러 층일 때) 개시·인원·종료 시각 + 건물 종합 의견·개선 제안

문장은 공공기관 보고 어투(~하였습니다)로 쓴다. 근거 없는 합성 점수로 층 순위를 매기지 않는다.
IDR 등급은 UI(grade.js idrScore)와 같은 '개시 구역 비율' 기준이고, 개시 시각을 함께 적는다.

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
SLIDE_SEC = {"summary": 12.0, "opinion": 11.0, "timeline": 9.0, "floor": 12.0, "building": 12.0}
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
# 보고서 문장은 공공기관 보고 어투(~하였습니다)로 쓴다. 출구 이름은 문장 안에서
# '왼쪽 계단(e1)' 꼴로 부르고, 조사는 괄호 앞 낱말(계단·출구)의 받침으로 고른다.
SENT_NAME = {
    "floor7": {"e1": "왼쪽 계단(e1)", "e2": "오른쪽 계단(e2)"},
    "floor8": {"e1": "왼쪽 계단(e1)", "e2": "오른쪽 계단(e2)"},
    "floor9": {"exit-0": "서쪽 출구(출구1)", "exit-1": "북쪽 출구(출구2)", "exit-2": "남동쪽 출구(출구3)"},
}
METRIC_INFO = [   # (키, 이름, 한 줄 뜻)
    ("idr", "IDR 대피 개시", "경보 후 각 구역이 피난을 개시하였는지를 개시 구역 비율로 나타냅니다."),
    ("sei", "SEI 비상구 활용 효율", "비상구가 설계 기준대로 고르게 이용되었는지 나타내며, 100에 가까울수록 양호합니다."),
    ("cbs", "CBS 병목 혼잡 누적", "비상구 앞 구역에서 임계밀도를 초과한 혼잡의 시간 누적값으로, 0에 가까울수록 양호합니다."),
    ("epfi", "EPFI 경로 충실도", "재실자가 권장 피난경로를 얼마나 따라 이동하였는지 나타내며, 100에 가까울수록 양호합니다."),
]


def josa(word: str, pair: str) -> str:
    """pair = '을/를' 처럼 (받침 있음/없음). 괄호가 있으면 괄호 앞 글자로 판단."""
    base = word.split("(")[0].rstrip()
    ch = base[-1] if base else ""
    has = "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0
    a, b = pair.split("/")
    if pair == "으로/로" and has and (ord(ch) - 0xAC00) % 28 == 8:   # ㄹ 받침 → 로
        return word + "로"
    return word + (a if has else b)


def sn(d: dict, e: dict) -> str:
    return SENT_NAME[d["floor"]].get(e["id"], e["name"])


def idr_score(d: dict) -> float:
    """UI(grade.js idrScore)와 같은 기준 — 개시 구역 비율(구역 1개 = 개시 100 / 미개시 0)."""
    return 100.0 if d.get("idr_delay") is not None else 0.0


def floor_reading(d: dict, prefix: str = "") -> tuple[list[str], list[str]]:
    """층 하나의 비상구별 해석 → (관찰, 개선 제안)."""
    obs, rec = [], []
    if not any(e["count"] for e in d["exits"]):
        return ["비상구 통과 인원이 확인되지 않았습니다."], []
    tot = sum(e["count"] for e in d["exits"])
    over = max(d["exits"], key=lambda e: e["share"] - e["design_share"])
    under = min(d["exits"], key=lambda e: e["share"] - e["design_share"])
    dev = (over["share"] - over["design_share"]) * 100
    worst = max(d["exits"], key=lambda e: e["cbs"] or 0)
    g_sei = grade(d["sei"])[1]
    if dev < 10:
        obs.append(f"비상구별 이용 분담이 설계 기준과 유사하여 비상구가 고르게 이용되었습니다"
                   f"(SEI {d['sei']:.0f}, {g_sei}).")
    else:
        obs.append(f"피난 인원의 {over['share'] * 100:.0f}%가 {josa(sn(d, over), '을/를')} 이용하여 "
                   f"설계 분담({over['design_share'] * 100:.0f}%)보다 {dev:.0f}%p 높았으며, "
                   f"{josa(sn(d, under), '은/는')} {under['share'] * 100:.0f}%를 처리하였습니다(SEI {d['sei']:.0f}, {g_sei}).")
    if (worst["cbs"] or 0) > CBS_CUTS[0]:
        gw = grade(worst["cbs"], "cbs")[1]
        tail = f"(CBS {worst['cbs']:.1f}, {gw}, 임계밀도 초과 {worst['over_sec']:.0f}초)"
        if worst is over and dev >= 10:
            obs.append(f"혼잡 또한 이용이 편중된 {sn(d, worst)} 앞에서 발생하였습니다{tail}. "
                       "특정 비상구로의 편중이 혼잡으로 이어진 것으로 판단됩니다.")
            rec.append(f"(개선) {prefix}안내방송 및 유도원 배치를 통해 {sn(d, under)} 이용을 유도하면 "
                       f"{sn(d, worst)} 앞 혼잡을 줄일 수 있을 것으로 판단됩니다.")
        else:
            few = worst["share"] < worst["design_share"]
            obs.append(f"혼잡은 {'이용 인원이 상대적으로 적은 ' if few else ''}{sn(d, worst)} 앞에서 발생하였습니다{tail}.")
            if worst["p10"] is not None:
                obs.append(f"이는 {sn(d, worst)} 이용 인원의 80%가 {kdur(worst['p90'] - worst['p10'])} 동안 "
                           f"집중적으로 통과(최대 분당 {worst['peak_min']}명)하였기 때문으로 판단됩니다.")
            rec.append(f"(개선) {prefix}시차 피난 또는 유도원 배치를 통해 {sn(d, worst)} 진입 시점을 분산할 필요가 있습니다.")
    else:
        obs.append("비상구 앞에서 임계밀도를 초과하는 혼잡은 거의 발생하지 않았습니다.")
    clean = [e for e in d["exits"] if (e["cbs"] or 0) <= CBS_CUTS[0] and e["count"]]
    if clean and (worst["cbs"] or 0) > CBS_CUTS[1]:
        c = clean[0]
        obs.append(f"{josa(sn(d, c), '은/는')} {c['count']}명이 통과하는 동안 혼잡이 발생하지 않아 수용 여력이 있었습니다.")
    if d["role"] == "upper":
        obs.append(f"피난층 종료 시점까지 총 {tot}명이 비상구를 통해 피난하였습니다.")
    return obs, rec


# ───────────────────────────────────────────────────────── 3) 슬라이드 HTML
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;background:var(--bg-base);color:var(--text-1);
  font-family:Pretendard,'Noto Sans KR',sans-serif;-webkit-font-smoothing:antialiased}
.page{position:absolute;inset:0;padding:24px 34px 20px;display:flex;flex-direction:column;gap:13px}
.top{display:flex;align-items:baseline;gap:14px;border-bottom:1px solid var(--stroke-2);padding-bottom:10px}
.top .k{color:var(--pia-cyan);font-weight:800;font-size:15px;letter-spacing:.04em}
.top h1{font-size:25px;font-weight:800}
.top .sub{color:var(--text-3);font-size:14px;margin-left:auto;white-space:nowrap}
.num{font-variant-numeric:tabular-nums}
.cards{display:flex;gap:12px}
.card{flex:1;background:var(--bg-2);border:1px solid var(--stroke-2);border-radius:10px;padding:10px 14px}
.card .l{color:var(--text-3);font-size:13px}
.card .v{font-size:25px;font-weight:800;margin-top:3px}
.card .s{color:var(--text-3);font-size:12.5px;margin-top:2px}
.verdict{border-radius:10px;padding:14px 18px;background:var(--bg-1);border:1px solid var(--stroke-2);border-left-width:5px}
.verdict.g-good{border-left-color:#3FB950}.verdict.g-mid{border-left-color:#F5A623}.verdict.g-bad{border-left-color:var(--danger)}
.verdict h2{font-size:19px;font-weight:700;margin-bottom:8px}
.verdict li{font-size:15.5px;line-height:1.55;list-style:none;padding-left:20px;position:relative;color:var(--text-2)}
.verdict li:before{position:absolute;left:0;font-weight:800}
.verdict li.good:before{content:"✓";color:#3FB950}.verdict li.warn:before{content:"!";color:#F5A623;left:5px}
.ideal{font-size:14px;color:var(--text-2);background:var(--bg-1);border:1px dashed var(--stroke-3);border-radius:10px;padding:11px 16px;line-height:1.6}
.ideal b{color:var(--pia-cyan)}
.pill{display:inline-block;font-size:12px;font-weight:700;padding:1px 8px;border-radius:999px;margin-left:6px;vertical-align:2px;white-space:nowrap}
.pill i{font-style:normal;opacity:.7;margin-left:3px;font-weight:600}
.r1{background:#1f6f3a;color:#c8f7d3}.r2{background:#2a5d2f;color:#d6f5d0}.r3{background:#6b5310;color:#ffe9a8}
.r4{background:#7a2e14;color:#ffd0bd}.r5{background:#7d1b1b;color:#ffc9c9}.r0{background:var(--bg-4);color:var(--text-3)}
table{border-collapse:collapse;width:100%;font-size:14.5px}
th{color:var(--text-3);font-weight:600;font-size:12.5px;text-align:right;padding:6px 8px;border-bottom:1px solid var(--stroke-2)}
td{padding:7px 8px;border-bottom:1px solid var(--stroke-1);text-align:right;font-variant-numeric:tabular-nums}
th:first-child,td:first-child{text-align:left}
td.n{font-weight:700;color:var(--text-1)}
.mt td{padding:8px 8px}
.mt td:first-child b{font-size:15px}
.mt td:first-child div{color:var(--text-3);font-size:12px;font-weight:400;margin-top:2px;max-width:470px}
.mt td .v{font-size:19px;font-weight:800}
.mt td .w{display:block;color:var(--text-3);font-size:11.5px;font-weight:400}
.lg{font-size:12.5px}
.lg th,.lg td{text-align:center;padding:4px 6px;font-size:12.5px}
.lg td:first-child,.lg th:first-child{text-align:left;color:var(--text-3)}
.lgnote{font-size:11.5px;color:var(--text-3);margin-top:4px}
.mrow{display:flex;flex-direction:column;gap:10px}
@media (min-width:1500px){.mrow{flex-direction:row;align-items:flex-start;gap:22px}.mrow>div:first-child{flex:3}.mrow>div:last-child{flex:2}}
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
.read li.rec{color:#ffd9a0}.read li.rec:before{color:#F5A623}
.read b{color:var(--text-1)}
.tl{position:relative;flex:1;min-height:0}
.foot{color:var(--text-3);font-size:12px;margin-top:auto}
"""


def pill(v, kind="score"):
    r, lab = grade(v, kind)
    return f'<span class="pill r{r}">{lab}' + (f'<i>{r}/5</i>' if r else "") + '</span>'


def page(kicker: str, title: str, sub: str, body: str, foot: str = "") -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{CSS_TOKENS}">'
            f'<style>{CSS}</style></head><body><div class="page">'
            f'<div class="top"><span class="k">{kicker}</span><h1>{title}</h1><span class="sub">{sub}</span></div>'
            f'{body}' + (f'<div class="foot">{foot}</div>' if foot else "") + '</div></body></html>')


def scope_short(D: dict) -> str:
    return D["scope"].split(" · ")[0]


def metric_table(D: dict) -> str:
    """4대지표 점수·등급 표 — 층별 (+ 여러 층이면 건물 통합). 등급은 grade.js 와 같은 경계."""
    fl, B = D["floors"], D["building"]
    multi = len(fl) > 1
    cols = "".join(f"<th>{d['name']}</th>" for d in fl) + ("<th>건물 통합</th>" if multi else "")

    def cell(key, d):
        if key == "idr":
            s = idr_score(d)
            w = (f"개시 {mmss(d['idr_delay'])}" if d.get("idr_delay") is not None else "미개시")
            return f'<span class="v">{int(s / 100)}/1</span>{pill(s)}<span class="w">{w} (경보 후)</span>'
        v = d[key]
        if v is None:
            return "—"
        return f'<span class="v">{v:.1f}</span>{pill(v, "cbs")}' if key == "cbs" else f'<span class="v">{v:.0f}</span>{pill(v)}'

    def bcell(key):
        if key == "idr":
            st = sum(1 for d in fl if d.get("idr_delay") is not None)
            return f'<span class="v">{st}/{len(fl)}</span>{pill(st / len(fl) * 100)}<span class="w">개시 구역 비율</span>'
        v = B[key]
        return f'<span class="v">{v:.1f}</span>{pill(v, "cbs")}' if key == "cbs" else f'<span class="v">{v:.0f}</span>{pill(v)}'
    trs = "".join(f'<tr><td><b>{nm}</b><div>{desc}</div></td>' + "".join(f"<td>{cell(k, d)}</td>" for d in fl)
                  + (f"<td>{bcell(k)}</td>" if multi else "") + "</tr>" for k, nm, desc in METRIC_INFO)
    return f'<table class="mt"><thead><tr><th>4대 지표</th>{cols}</tr></thead><tbody>{trs}</tbody></table>'


def grade_legend() -> str:
    pills = lambda labs: "".join(f'<td><span class="pill r{i + 1}" style="margin:0">{l}</span></td>' for i, l in enumerate(labs))
    return ('<div><table class="lg"><thead><tr><th>등급 기준</th>'
            + "".join(f"<th>{i}단계</th>" for i in range(1, 6)) + '</tr></thead><tbody>'
            f'<tr><td>SEI·EPFI·IDR</td>{pills(SCORE_LABELS)}</tr>'
            '<tr><td>점수 구간</td><td>90 이상</td><td>75~90</td><td>60~75</td><td>40~60</td><td>40 미만</td></tr>'
            '<tr><td>의미</td><td>기준 충분히 충족</td><td>대체로 충족</td><td>일부 개선 필요</td><td>개선 필요</td><td>전반적 개선 필요</td></tr>'
            f'<tr><td>CBS</td>{pills(CBS_LABELS)}</tr>'
            '<tr><td>누적 구간</td><td>0.5 이하</td><td>0.5~3</td><td>3~10</td><td>10~30</td><td>30 초과</td></tr>'
            '<tr><td>의미</td><td>혼잡 거의 없음</td><td>일시적 경미한 혼잡</td><td>단시간 혼잡</td><td>지속적 혼잡</td><td>장시간 심한 정체</td></tr>'
            '</tbody></table><div class="lgnote">※ 등급 구간은 참고 기준이며, 고객 확정 시 조정될 수 있습니다. '
            'IDR은 개시 구역 비율로 등급을 매기며, 개시 시각을 함께 표기합니다.</div></div>')


def slide_summary(D: dict) -> str:
    fl = D["floors"]
    ups = [d for d in fl if d["role"] == "upper"]
    ground = next((d for d in fl if d["role"] == "ground"), None)
    cards = []
    if D["final_scope"] is None:
        u = ups[0]
        cards.append(("피난층 종료", mmss(u["floor_end"]), f"{wall(u['floor_end'])} · 해당 층 최종 인원의 비상구 통과 시각"))
    else:
        cards.append(("최종 종료(1층 외부 탈출)", mmss(D["final_scope"]),
                      " · ".join(f"{u['name']} {mmss(u['final_end'])}" for u in ups)))
        cards.append(("피난층 종료", " · ".join(f"{u['name']} {mmss(u['floor_end'])}" for u in ups),
                      "해당 층 최종 인원의 비상구 통과 시각"))
    cards.append(("비상구 피난 인원", f"{sum(sum(e['count'] for e in u['exits']) for u in ups)}명",
                  " · ".join(f"{u['name']} {sum(e['count'] for e in u['exits'])}명" for u in ups) + " (피난층 종료 시점까지)"))
    if ground:
        cards.append(("1층 출구 통과", f"{sum(e['count'] for e in ground['exits'])}명", "1층 재실자 및 타 층 피난 인원 포함"))
    card_html = "".join(f'<div class="card"><div class="l">{l}</div><div class="v num"'
                        + (' style="font-size:19px;padding-top:4px"' if len(v) > 12 else "")
                        + f'>{v}</div><div class="s">{s}</div></div>' for l, v, s in cards)
    body = (f'<div class="cards">{card_html}</div>'
            f'<div class="mrow"><div>{metric_table(D)}</div>{grade_legend()}</div>')
    return page("피난훈련 평가 보고서", "종합 평가", f"CJ제일제당센터 · 2026-10-22 · {D['scope']}", body,
                "경보(훈련 시작) 14:59:55 = 00:00 · 사이렌 00:05 · 시각은 경보 후 경과 시간(분:초)")


def slide_opinion(D: dict) -> str:
    fl = D["floors"]
    ups = [d for d in fl if d["role"] == "upper"]
    good, warn = [], []
    for u in ups:
        if u["idr_delay"] is not None:
            good.append(f"{josa(u['name'], '은/는')} 경보 후 {int(u['idr_delay'])}초에 대피를 개시하였습니다.")
        else:
            warn.append(f"{u['name']}에서는 대피 개시가 확인되지 않았습니다.")
    for d in fl:
        over = max(d["exits"], key=lambda e: e["share"] - e["design_share"])
        if grade(d["sei"])[0] <= 2:
            good.append(f"{josa(d['name'], '은/는')} 비상구가 비교적 고르게 이용되었습니다(SEI {d['sei']:.0f}).")
        else:
            warn.append(f"{josa(d['name'], '은/는')} 피난 인원의 {over['share'] * 100:.0f}%가 {josa(sn(d, over), '을/를')} 이용하여 "
                        f"설계 분담({over['design_share'] * 100:.0f}%)보다 편중되었습니다(SEI {d['sei']:.0f}).")
        worst = max(d["exits"], key=lambda e: e["cbs"] or 0)
        if grade(worst["cbs"] or 0, "cbs")[0] <= 2:
            good.append(f"{josa(d['name'], '은/는')} 비상구 앞 혼잡이 낮았습니다(최대 CBS {worst['cbs'] or 0:.1f}).")
        else:
            warn.append(f"{d['name']} {sn(d, worst)} 앞에서 혼잡이 발생하였습니다"
                        f"(CBS {worst['cbs']:.1f}, 임계밀도 초과 {worst['over_sec']:.0f}초).")
        if d["epfi"] is not None:
            if grade(d["epfi"])[0] <= 2:
                good.append(f"{josa(d['name'], '은/는')} 권장 피난경로를 대체로 준수하였습니다(EPFI {d['epfi']:.0f}).")
            else:
                warn.append(f"{d['name']}에서는 일부 인원이 권장 피난경로를 벗어나 이동하였습니다(EPFI {d['epfi']:.0f}).")
    for u in ups:
        good.append(f"{josa(u['name'], '은/는')} 경보 후 {kdur(u['floor_end'])}에 피난을 완료하였"
                    + (f"으며, {kdur(u['final_end'])}에 1층 외부 탈출을 완료하였습니다." if D["final_scope"] else "습니다."))
    sc = scope_short(D)
    head = (f"{sc} 피난은 전반적으로 우수하게 수행되었습니다.", "g-good") if not warn else \
        ((f"{sc} 피난은 전반적으로 양호하였으나, 일부 개선이 필요한 사항이 확인되었습니다.", "g-mid")
         if len(warn) <= len(good) else (f"{sc} 피난은 개선이 필요한 것으로 평가되었습니다.", "g-bad"))
    li = "".join(f'<li class="good">{t}</li>' for t in good) + "".join(f'<li class="warn">{t}</li>' for t in warn)
    verdict = f'<div class="verdict {head[1]}"><h2>{head[0]}</h2><ul>{li}</ul></div>'
    ideal = ('<div class="ideal">평가 기준: 바람직한 피난은 <b>① 경보 직후 신속한 대피 개시(IDR)</b>, '
             '<b>② 설계 기준에 따른 비상구 균등 이용(SEI 100에 근접)</b>, <b>③ 비상구 앞 혼잡 최소화(CBS 0에 근접)</b>, '
             '<b>④ 권장 피난경로 준수(EPFI 100에 근접)</b>로 판단합니다. '
             '비상구가 균등하게 이용될수록 특정 비상구 앞 혼잡이 줄어듭니다.</div>')
    return page("피난훈련 평가 보고서", "평가 의견", f"CJ제일제당센터 · 2026-10-22 · {D['scope']}", verdict + ideal)

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
                    f'{"피난층" if d["role"] == "upper" else "피난층(지상 출구)"} · 00:00~{mmss(end)}</div></div>')
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
                        f'1층 외부 탈출 {mmss(t)}</div>'
                        f'<div style="position:absolute;left:{X(d["floor_end"])}px;top:{y + 9}px;width:{X(t) - X(d["floor_end"])}px;'
                        f'height:0;border-top:2px dashed #ff8a85;opacity:.6"></div>')
        y += 34
        for k, e in enumerate(d["exits"]):
            col = COLS[k % 3]
            if e["first"] is None:
                rows.append(f'<div style="position:absolute;left:{lab_w - 4}px;top:{y}px;font-size:12.5px;color:var(--text-3)">'
                            f'{e["name"]} 통과 없음</div>')
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
    body = (f'<div class="legend">굵은 막대: 이용 인원의 80%가 통과한 시간대 · 가는 선: 최초~최종 통과 시간'
            f'<span style="margin-left:18px;color:#7ee39a">● 대피 개시</span>'
            f'<span style="margin-left:14px;color:#F5A623">┃ 피난층 종료</span>'
            + ('<span style="margin-left:14px;color:#ff8a85">┃ 최종 종료</span>' if final else "") + '</div>'
            f'<div class="tl">{ticks}{"".join(rows)}{final}</div>')
    return page("피난훈련 평가 보고서", "피난 타임라인", f"{D['scope']} · 경보 후 경과 시간(분:초)", body)


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
    tbl = ('<table><thead><tr><th>비상구</th><th>인원</th><th>분담 편차</th><th>80% 통과 시간대</th>'
           '<th>최대 명/분</th><th>비상구 앞 CBS</th><th>임계 초과</th><th>최대 밀도 명/㎡</th></tr></thead>'
           f'<tbody>{trs}</tbody></table>')
    obs, rec = floor_reading(d)
    read = "".join(f"<li>{t}</li>" for t in obs) + "".join(f'<li class="rec">{t}</li>' for t in rec)
    return (f'<div class="col"><div class="sh">{d["name"]} · 비상구 {len(d["exits"])}개소 · {tot}명'
            f'<span class="m">00:00 ~ {mmss(end)} · SEI {d["sei"]:.0f}{pill(d["sei"])} · CBS {d["cbs"]:.1f}{pill(d["cbs"], "cbs")}</span></div>'
            f'<div class="bars">{bars}</div>'
            f'<div class="legend">막대: 실제 분담 · <i></i>흰 눈금: 설계 분담(출구 폭 비례)</div>'
            f'{tbl}<div class="read"><ul>{read}</ul></div></div>')


def slide_floor(D: dict, ds: list[dict]) -> str:
    body = (f'<div class="grid2">{"".join(floor_panel(d, D) for d in ds)}</div>' if len(ds) > 1
            else floor_panel(ds[0], D))
    names = " · ".join(d["name"] for d in ds)
    return page("피난훈련 평가 보고서", f"비상구별 분석 ({names})", D["scope"], body,
                "CBS: 비상구 앞 구역에서 임계밀도(0.8명/㎡)를 초과한 혼잡의 시간 누적값(0에 가까울수록 양호) · "
                "분담 편차: 실제 분담 − 설계 분담")


def slide_building(D: dict) -> str:
    fl = D["floors"]
    ups = [d for d in fl if d["role"] == "upper"]
    ground = next((d for d in fl if d["role"] == "ground"), None)
    cols = "".join(f"<th>{d['name']}</th>" for d in fl)

    def cell_start(d):
        if d["idr_delay"] is None:
            return "미개시"
        return (f"{int(d['idr_delay'])}초 ({mmss(d['idr_delay'])})" if d["role"] == "upper"
                else f"{mmss(d['idr_delay'])} 피난 흐름 형성")
    rows = [
        ("대피 개시 (경보 후)", [cell_start(d) for d in fl]),
        ("비상구·출구 통과 인원", [f"{sum(e['count'] for e in d['exits'])}명" for d in fl]),
        ("피난층 종료 / 최종 종료",
         [f"{mmss(d['floor_end'])} / {mmss(d['final_end'])}" if d["role"] == "upper" else f"~ {mmss(D['final_scope'])}" for d in fl]),
    ]
    trs = "".join(f"<tr><td>{k}</td>" + "".join(f'<td class="n">{v}</td>' for v in vs) + "</tr>" for k, vs in rows)
    tbl = f'<table><thead><tr><th>구분</th>{cols}</tr></thead><tbody>{trs}</tbody></table>'
    con, rec = [], []
    starts = [(d["name"], d["idr_delay"]) for d in ups if d["idr_delay"] is not None]
    if len(starts) >= 2:
        a, b = sorted(starts, key=lambda s: s[1])
        con.append(f"대피 개시는 {josa(a[0], '이/가')} 경보 후 {int(a[1])}초로 가장 빨랐으며, "
                   f"{josa(b[0], '은/는')} {int(b[1])}초로 {int(b[1] - a[1])}초 늦게 개시되었습니다.")
        if ground and ground["idr_delay"] is not None:
            con.append(f"1층 출구 방향 피난 흐름은 경보 후 {mmss(ground['idr_delay'])}에 형성되었습니다.")
    elif starts:
        a = starts[0]
        con.append(f"건물 대피는 경보 후 {int(a[1])}초에 {a[0]}에서 개시되었으며"
                   + (f", 1층 출구 방향 피난 흐름은 {mmss(ground['idr_delay'])}에 형성되었습니다."
                      if ground and ground["idr_delay"] is not None else "니다."))
    for u in ups:
        con.append(f"{josa(u['name'], '은/는')} 경보 후 {kdur(u['floor_end'])}에 피난을 완료하였으며, "
                   f"{kdur(u['final_end'])}에 1층 외부 탈출을 완료하였습니다.")
    if ground:
        o = max(ground["exits"], key=lambda e: e["share"] - e["design_share"])
        u = min(ground["exits"], key=lambda e: e["share"] - e["design_share"])
        dv = (o["share"] - o["design_share"]) * 100
        con.append(f"1층은 피난 인원의 {o['share'] * 100:.0f}%가 {josa(sn(ground, o), '을/를')} 이용하여 "
                   f"설계 분담({o['design_share'] * 100:.0f}%)을 {'크게 ' if dv >= 15 else ''}상회하였으며, "
                   f"{josa(sn(ground, u), '은/는')} {u['share'] * 100:.0f}%로 설계 분담({u['design_share'] * 100:.0f}%)에 "
                   "미치지 못하였습니다." + (f" 이에 따라 {sn(ground, o)} 앞에서 혼잡이 발생하였습니다(CBS {o['cbs']:.1f})."
                                       if (o["cbs"] or 0) > CBS_CUTS[0] else ""))
        if u["width_m"] and o["width_m"] and u["width_m"] > o["width_m"]:
            rec.append(f"(개선) 폭이 더 넓은 {sn(ground, u)}에 여력이 있었음에도 {sn(ground, o)}에 인원이 집중되었으므로, "
                       f"{sn(ground, u)} 이용 유도가 필요합니다.")
        else:
            rec.append(f"(개선) {sn(ground, u)} 이용 유도를 통해 {sn(ground, o)}의 부담을 분산할 필요가 있습니다.")
    for d in ups:
        rec += floor_reading(d, prefix=f"{d['name']}: ")[1]
    read = "".join(f"<li>{t}</li>" for t in con) + "".join(f'<li class="rec">{t}</li>' for t in rec)
    body = f'{tbl}<div class="read"><div class="sh" style="margin-bottom:6px">건물 종합 의견</div><ul>{read}</ul></div>'
    return page("피난훈련 평가 보고서", "건물 종합 평가", D["scope"], body,
                "건물 통합 산정: SEI는 전체 비상구 이용 분포와 설계 분포 비교, CBS는 층별 합계, "
                "EPFI는 전체 인원 평균, IDR은 개시 구역 비율 기준")


def slides(D: dict) -> list[tuple[str, str]]:
    fmt = D["format"]
    vw = FORMAT[fmt][1][0]
    out = [("summary", slide_summary(D)), ("opinion", slide_opinion(D)), ("timeline", slide_timeline(D, vw))]
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
