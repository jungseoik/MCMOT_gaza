#!/usr/bin/env python
"""CJ 실훈련(2026-10-22) **피난훈련 평가 보고서** 섹션 생성 — 발표 영상 끝 리포트 창에 끼워 넣는다.

④ 리플레이의 [리포트] 창(건물 훈련 결과 리포트)은 지표를 자동으로 해석하지만, 이번 훈련의
**사람이 확인한 사실**(층별 훈련 시작·피난층 종료·최종 종료 시각, 비상구별 근사 정답)은 모른다.
이 스크립트가 그 사실과 측정값을 합쳐 리포트 창과 **같은 마크업·같은 스타일**의 HTML 섹션을
영상(출력)마다 만든다. `tools/report_card.py --inject-html` 이 창 맨 위에 끼워 캡처한다.

숫자는 손으로 적지 않는다 — 세션 저장본(타임라인)과 answers.json 에서 읽는다.
보고서에는 **훈련 사실·결과만** 싣는다 — 측정 방식·카메라·검증(근사 정답 비교) 같은 내부 정보는
넣지 않는다(그건 docs/출구-카운트-채점결과.md 소관).

    python tools/cj1022_eval_report.py            # → results/demo/05_CJ1022/_리포트/eval_<id>.html
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESS = ROOT / "data/sites/default/sessions"
ANS = json.loads((ROOT / "media/vsource/cj/drill-1022/answers.json").read_text(encoding="utf-8"))
OUT = ROOT / "results/demo/05_CJ1022/_리포트"

T0_WALL = 14 * 3600 + 59 * 60 + 55          # 클립 00:00 = 실제 14:59:55
SIREN = 5                                    # 사이렌 00:05 (15:00:00)

# 영상 → (세션, 보여 줄 층). 세션 id 는 최종 녹화(2026-10-24)
OUTPUTS = {
    "16F_floorend": ("sess-1792820885287", ["floor8"], "16층 단독 · 피난층 종료까지"),
    "17F_floorend": ("sess-1792821419865", ["floor7"], "17층 단독 · 피난층 종료까지"),
    "16F1F_final":  ("sess-1792820138660", ["floor8", "floor9"], "16층 → 1층 · 최종 종료까지"),
    "17F1F_final":  ("sess-1792821415890", ["floor7", "floor9"], "17층 → 1층 · 최종 종료까지"),
    "3F_all":       ("sess-1792820130375", ["floor7", "floor8", "floor9"], "전체 3개 층"),
}
NAME = {"floor7": "17층", "floor8": "16층", "floor9": "1층"}
EXIT_NAME = {
    "floor7": {"e1": "왼쪽 계단 비상구 (e1)", "e2": "오른쪽 계단 비상구 (e2)"},
    "floor8": {"e1": "왼쪽 계단 비상구 (e1)", "e2": "오른쪽 계단 비상구 (e2)"},
    "floor9": {"exit-0": "출구1 · 서쪽 출입문", "exit-1": "출구2 · 북쪽 출입문", "exit-2": "출구3 · 남동쪽 출입문"},
}
FLOOR_NOTE = {
    "floor8": "마지막 피난조끼 인원은 엘리베이터로 하강",
    "floor7": "형광조끼 2명이 비상구로 내려가며 17층 인원 최종",
}


def mmss(s: float) -> str:
    s = int(round(s))
    return f"{s // 60:02d}:{s % 60:02d}"


def wall(s: float) -> str:
    t = T0_WALL + int(round(s))
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def counts_at(fl: str, sid: str, cut: float) -> tuple[dict, float, float | None]:
    j = json.loads((SESS / fl / f"{sid}.json").read_text(encoding="utf-8"))
    r = j["result"]; t0 = r["alarm_ts"]; last = {}
    for p in j["timeline"]:
        if p["ts"] - t0 > cut + 1e-6:
            break
        last = dict(p.get("exit_counts") or {})
    return last, r["ended_at"] - t0, idr_dt2(fl, sid)


def idr_dt2(fl: str, sid: str) -> float | None:
    """대피 개시(IDR) — 영상·리플레이와 같은 **dt_hold 2.0** 기준으로 재계산한다.
    저장본의 값은 녹화 당시 기본(3.0)이라 영상 하단 표기와 어긋난다."""
    import sys
    sys.path.insert(0, str(ROOT))
    from system.metrics.replay import run_replay
    res, *_ = run_replay(str(SESS / fl / f"{sid}.db"), {"thresholds": {"dt_hold": 2.0}})
    z = (res.zone_metrics or [None])[0]
    return z.response_delay_sec if z is not None else None


def gt_floor(fl: str) -> dict | None:
    return next((g for g in ANS["floors"] if g["floor"] == fl), None)


def row(k: str, v: str) -> str:
    return f'<div class="resrow"><span>{k}</span><b class="t-num">{v}</b></div>'


def floor_block(fl: str, sid: str, session_end: float) -> tuple[str, dict]:
    g = gt_floor(fl)
    stat: dict = {"floor": NAME[fl]}
    if g:                                          # 16·17층 — 근사 정답 있음
        end, fin = g["floor_end_sec"], g["final_end_sec"]
        c, _, idr = counts_at(fl, sid, end)
        tot = sum(c.values()); gtot = sum(v["count"] for v in g["by_exit"].values())
        rows = (row("훈련 시작", f"00:00 ({wall(0)}) · 사이렌 00:05 ({wall(SIREN)})")
                + row("대피 개시", f"{mmss(idr)} 경과" if idr is not None else "—")
                + row("피난층 종료 (층 비움)", f"<b>{mmss(end)}</b> ({wall(end)})")
                + row("최종 종료 (1층 밖 탈출)", f"{mmss(fin)} ({wall(fin)})")
                + row("피난층 종료까지 비상구 탈출", f"<b>{tot}명</b>")
                + row("비고", FLOOR_NOTE[fl]))
        trs = ""
        for eid, gv in g["by_exit"].items():
            v = c.get(eid, 0); share = v / tot * 100 if tot else 0
            trs += (f"<tr><td>{EXIT_NAME[fl][eid]}</td><td class='t-num'>{v}명</td>"
                    f"<td class='t-num'>{share:.0f}%</td></tr>")
        tbl = (f"<table class='drill-tbl'><thead><tr><th>비상구</th><th>탈출 인원</th><th>분담</th>"
               f"</tr></thead><tbody>{trs}</tbody></table>")
        top = max(c, key=c.get) if c else None
        stat.update(end=end, fin=fin, tot=tot, gtot=gtot,
                    skew=(EXIT_NAME[fl][top].split(" (")[0], c[top] / tot * 100) if tot else None,
                    err=[abs(c.get(e, 0) - gv["count"]) for e, gv in g["by_exit"].items()])
        head = f"{NAME[fl]} — 피난층 종료 {mmss(end)} · 비상구 탈출 {tot}명"
    else:                                          # 1층 — 정답 없음, 세션 끝까지
        c, _, idr = counts_at(fl, sid, session_end)
        tot = sum(c.values())
        rows = (row("구간", f"00:00 ~ {mmss(session_end)} ({wall(0)} ~ {wall(session_end)})")
                + row("출구 통과 합계", f"<b>{tot}명</b>"))
        trs = "".join(f"<tr><td>{EXIT_NAME[fl][e]}</td><td class='t-num'>{c.get(e, 0)}명</td>"
                      f"<td class='t-num'>{(c.get(e, 0) / tot * 100 if tot else 0):.0f}%</td></tr>"
                      for e in EXIT_NAME[fl])
        tbl = (f"<table class='drill-tbl'><thead><tr><th>출구</th><th>통과 인원</th><th>분담</th></tr></thead>"
               f"<tbody>{trs}</tbody></table>")
        stat.update(tot=tot, end=session_end)
        head = f"1층 — 출구 통과 {tot}명 ({mmss(session_end)}까지)"
    html = (f'<div class="repsec-h">{head}</div><div class="reprows">{rows}</div>'
            f'<div class="drill-perfloor" style="margin-top:10px">{tbl}</div>')
    return html, stat


def build(oid: str) -> str:
    sid, floors, subtitle = OUTPUTS[oid]
    j0 = json.loads((SESS / floors[0] / f"{sid}.json").read_text(encoding="utf-8"))
    session_end = j0["result"]["ended_at"] - j0["result"]["alarm_ts"]
    blocks, stats = [], []
    for fl in floors:
        h, st = floor_block(fl, sid, session_end)
        blocks.append(h); stats.append(st)
    # 평가(판정) — 리포트 창의 판정 상자(repverdict)와 같은 모양
    good, warn = [], []
    up = [s for s in stats if "gtot" in s]
    for s in up:
        good.append(f"{s['floor']} {mmss(s['end'])}에 층 비움 완료 · {mmss(s['fin'])}에 1층 밖 탈출")
        if s.get("skew") and s["skew"][1] >= 60:
            warn.append(f"{s['floor']} 비상구 분담 쏠림 — {s['skew'][0]} {s['skew'][1]:.0f}% (반대편 계단 유도 검토)")
    if len(up) == 2:
        a, b = sorted(up, key=lambda s: s["end"])
        good.append(f"{a['floor']}이 {b['floor']}보다 {b['end'] / a['end']:.1f}배 빨리 층을 비움")
    vcls = "g-good" if not warn else "g-mid"
    verdict = (f'<div class="repverdict {vcls}"><div class="repverdict-head">훈련 평가 — {subtitle}</div>'
               + (f'<div class="repverdict-line good">{" · ".join(good)}</div>' if good else "")
               + (f'<div class="repverdict-line warn">{" · ".join(warn)}</div>' if warn else "")
               + "</div>")
    summ = ('<div class="repsum">CJ제일제당센터 피난대피훈련 · 2026-10-22 · '
            '훈련 시작 14:59:55 · 사이렌 15:00:00</div>')
    # 시스템 리포트 '개요'의 시각 두 줄은 리허설을 **녹화한** 벽시계라(10/24 새벽) 실제 훈련
    # 시각과 다르다 — 실훈련 기준 값으로 바꿔 끼우게 data-rows 로 넘긴다(report_card 가 적용).
    starts = []
    for fl in floors:
        d = idr_dt2(fl, sid)
        starts.append(f"{NAME[fl]} {mmss(d)} 경과 ({wall(d)})" if d is not None else f"{NAME[fl]} 미개시")
    rows_json = json.dumps({"경보 시각": "2026-10-22 14:59:55 (실훈련 · 영상 00:00)",
                            "층별 개시시각": " · ".join(starts)}, ensure_ascii=False)
    return (f"<div id=\"cjEval\" data-rows='{rows_json}' style=\"margin-bottom:18px\">"
            '<div class="repsec-h" style="font-size:15px;color:var(--text-1)">피난훈련 평가 보고서</div>'
            + summ + verdict + "".join(blocks)
            + '<div style="border-bottom:1px solid var(--stroke-3);margin-top:14px"></div></div>')


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for oid in OUTPUTS:
        p = OUT / f"eval_{oid}.html"
        p.write_text(build(oid), encoding="utf-8")
        print(f"→ {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
