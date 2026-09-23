/* 4대 지표 5단계 등급 — 운영 뷰·④ 리플레이·결과 리포트가 **같은 기준**을 쓴다.
 *
 * 왜 공용 모듈인가: 등급 문구·경계가 화면마다 따로 박혀 있으면, 같은 세션을
 * 운영 뷰에서 보면 '보통' 인데 리포트에서는 '미흡' 으로 나오는 일이 생긴다.
 * 기준을 한 곳에 두고 세 화면이 이걸 부른다.
 *
 * 주의 — 경계값은 **표시용 참고 기준**이다. 요구사항 D-8 이 종합점수·등급을
 * 범위 밖으로 두었고(고객 확정 필요), 4대 지표 자체의 규정된 등급표는 아직 없다.
 * 확정되면 THRESH 만 고치면 세 화면이 함께 바뀐다.
 *
 * 방향이 지표마다 다르다:
 *   EPFI·SEI·IDR(개시율)  0~100, **클수록 좋다**
 *   CBS                   0~∞,  **작을수록 좋다** (혼잡 누적)
 */
(function () {
  "use strict";

  // 점수형(0~100, 클수록 좋음) 하한 경계 — 위에서부터 1등급
  const SCORE_CUTS = [90, 75, 60, 40];
  // CBS(작을수록 좋음) 상한 경계 — 위에서부터 1등급
  const CBS_CUTS = [0.5, 3, 10, 30];

  const SCORE_LABELS = ["우수", "양호", "보통", "미흡", "불량"];
  const CBS_LABELS = ["원활", "양호", "보통", "혼잡", "심각"];
  // 기존 3색 클래스(g-good/g-mid/g-bad)는 리포트 총평 테두리 등에서 계속 쓰인다
  const LEGACY = ["g-good", "g-good", "g-mid", "g-bad", "g-bad"];

  function rankOf(v, metric) {
    if (v == null || isNaN(v)) return 0;               // 0 = 표본부족
    if (metric === "cbs") {
      for (let i = 0; i < CBS_CUTS.length; i++) if (v <= CBS_CUTS[i]) return i + 1;
      return 5;
    }
    for (let i = 0; i < SCORE_CUTS.length; i++) if (v >= SCORE_CUTS[i]) return i + 1;
    return 5;
  }

  /** 값 → {rank(1~5, 0=표본부족), label, cls, legacy}. metric: epfi|sei|idr|cbs */
  function of(v, metric) {
    const r = rankOf(v, metric);
    if (!r) return { rank: 0, label: "표본부족", cls: "g-na", legacy: "g-na" };
    const labels = metric === "cbs" ? CBS_LABELS : SCORE_LABELS;
    return { rank: r, label: labels[r - 1], cls: "g" + r, legacy: LEGACY[r - 1] };
  }

  /** 등급 배지 HTML. showRank=true 면 "양호 2/5" 처럼 단계도 같이. */
  function pill(v, metric, showRank) {
    const g = of(v, metric);
    const rk = (showRank && g.rank) ? `<i>${g.rank}/5</i>` : "";
    return `<span class="gpill ${g.cls}" title="${title(metric)}">${g.label}${rk}</span>`;
  }

  /** IDR 은 m/s 라 도면(경보원~구역 거리)에 따라 스케일이 달라 값으로 등급을 못 매긴다.
   *  대신 **개시한 구역 비율**(0~100)로 본다 — 도면과 무관하다.
   *
   *  아직 한 구역도 개시하지 않았으면 **등급을 매기지 않는다**(null). 경보 직후나
   *  재생 시작 시점은 "0/5 이라서 불량"이 아니라 아직 판정할 거리가 없는 상태다 —
   *  그때 빨간 '불량' 을 띄우면 오독을 부른다. 화면에는 "0/5 구역 반응" 이 이미
   *  글로 떠 있다. 최종 결과(전 구간 집계)에서 여전히 0 이면 final=true 로 불러
   *  불량으로 판정한다.
   */
  function idrScore(started, total, final) {
    if (!total) return null;
    if (!started && !final) return null;
    return (started / total) * 100;
  }

  function title(metric) {
    const cuts = metric === "cbs" ? CBS_CUTS : SCORE_CUTS;
    const labels = metric === "cbs" ? CBS_LABELS : SCORE_LABELS;
    const op = metric === "cbs" ? "≤" : "≥";
    return labels.map((L, i) => i < cuts.length ? `${L} ${op}${cuts[i]}`
                                                : `${L} 그 외`).join(" · ")
      + (metric === "cbs" ? " (작을수록 좋음)" : " (클수록 좋음)")
      + " — 표시용 참고 기준";
  }

  window.Grade = { of, pill, idrScore, rankOf, title,
                   SCORE_CUTS, CBS_CUTS, SCORE_LABELS, CBS_LABELS };
})();
