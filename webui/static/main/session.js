/* 평가 세션(4대 지표 IDR·EPFI·CBS·SEI) — 컨트롤·카드 대시보드·결과 모달.
 * CONTRACT v1.2 /api/session/* + MapState.session(SSE) + TimelinePoint 폴링.
 * 렌더는 전부 vanilla JS + canvas. 세션 종료 후에도 timeline/result로 카드 유지. */
"use strict";

const Session = (() => {
  const $ = (id) => document.getElementById(id);
  let live = null;         // SessionLive | null
  let result = null;       // EvaluationResult | null
  let timeline = [];       // TimelinePoint[]
  let personSeries = null; // {gid: {route_id, series: [[t, d_m],...]}} — v1.4
  let lastSiteV = 0;       // 최신 site_version (설정 변경 경고용)
  let placing = false;     // 경보 위치 클릭 대기
  let pollT = null;
  let stoppedId = null;    // stop 직후 지연 SSE 스냅샷 무시용
  let onMapRender = null;  // 맵 canvas 다시 그리기 콜백
  let inited = false;

  // ================================================== 수신·상태
  async function bootstrap() {
    try { live = await API.getSession(); } catch (e) { live = null; }
    try { result = await API.getSessionResult(); } catch (e) { result = null; }
    try { timeline = await API.getSessionTimeline(); } catch (e) { timeline = []; }
    if (live) { startPoll(); switchPanel("sess"); }
    else if (result) switchPanel("sess");
    if (result) loadSeries();
    updateUI();
  }

  /** SSE MapState 수신 시 view_live가 호출. */
  function onState(st) {
    if (st && st.site_version) lastSiteV = st.site_version;
    const s = st && st.session;
    if (s && s.session_id !== stoppedId) {
      const wasNew = !live;
      live = s;
      if (wasNew) { result = null; startPoll(); switchPanel("sess"); }
      updateUI();
    } else if (!s && live) {              // 다른 클라이언트가 종료한 경우
      live = null;
      stopPoll();
      refreshFinal();
    }
  }

  function startPoll() {
    stopPoll();
    pollT = setInterval(async () => {
      try { timeline = await API.getSessionTimeline(); updateCards(); }
      catch (e) { /* 일시 오류 무시 */ }
    }, 2000);
  }
  function stopPoll() { if (pollT) { clearInterval(pollT); pollT = null; } }

  async function refreshFinal() {
    try { result = await API.getSessionResult(); } catch (e) { /* keep */ }
    try { timeline = await API.getSessionTimeline(); } catch (e) { /* keep */ }
    loadSeries();
    updateUI();
  }

  /** 객체별 d_i(t) 시계열 로드 — 종료 후 저장본에서 지연 표출 (v1.4). */
  async function loadSeries() {
    try { personSeries = await API.getPersonSeries(); }
    catch (e) { personSeries = null; }
    renderDev();
  }

  // ================================================== 컨트롤
  function init(opts) {
    onMapRender = (opts && opts.onMapRender) || null;
    if (inited) return;
    inited = true;
    $("sessBtn").onclick = onBtn;
    $("pmSess").onclick = () => switchPanel("sess");
    $("pmRt").onclick = () => switchPanel("rt");
    $("resClose").onclick = () => $("resultModal").classList.add("hidden");
    $("resultModal").onclick = (e) => {
      if (e.target === $("resultModal")) $("resultModal").classList.add("hidden");
    };
    $("resReopen").onclick = () => { if (result) showResultModal(); };
  }

  async function _startWithOrigins(origins) {
    try {
      live = await API.startSession(null, { origins });
      stoppedId = null; result = null; timeline = []; personSeries = null;
      renderDev(); startPoll(); switchPanel("sess");
      hint(`세션 시작 — ${live.session_id} (경보원 ${origins.length}개)`);
    } catch (e) { hint("세션 시작 실패: " + e.message, true); }
    updateUI();
  }

  function onBtn() {
    if (live) { stop(); return; }
    if (placing) { placing = false; setBtn(); hint(""); return; }
    // 사이트에 alarm_origins 가 설정되어 있으면 즉시 시작 (맵 클릭 불필요)
    const aos = App.site && App.site.alarm_origins;
    if (aos && aos.length) {
      _startWithOrigins(aos.map((ao) => ao.xy));
      return;
    }
    placing = true;
    setBtn();
    hint("맵에서 경보 발생 위치를 클릭하세요 (버튼을 다시 누르면 취소) · 또는 맵설정에서 경보원을 미리 지정하면 즉시 시작");
  }

  /** 맵 클릭 훅 (view_live) — 배치 모드였으면 true. */
  function placeAlarm(p) {
    if (!placing) return false;
    placing = false;
    setBtn();
    (async () => {
      try {
        live = await API.startSession(null, { origins: [[p.x, p.y]] });
        stoppedId = null;
        result = null;
        timeline = [];
        personSeries = null;
        renderDev();
        startPoll();
        switchPanel("sess");
        hint(`세션 시작 — ${live.session_id}`);
      } catch (e) { hint("세션 시작 실패: " + e.message, true); }
      updateUI();
    })();
    return true;
  }

  async function stop() {
    try {
      result = await API.stopSession();
      stoppedId = live && live.session_id;
      live = null;
      stopPoll();
      try { timeline = await API.getSessionTimeline(); } catch (e) { /* keep */ }
      loadSeries();
      hint("세션 종료 — 결과가 산출되었습니다.");
      showResultModal();
    } catch (e) { hint("세션 종료 실패: " + e.message, true); }
    updateUI();
  }

  function setBtn() {
    const b = $("sessBtn");
    if (live) { b.textContent = "⏹ 세션 종료"; b.classList.add("stop"); }
    else if (placing) { b.textContent = "✕ 위치 클릭 대기 — 취소"; b.classList.remove("stop"); }
    else { b.textContent = "🔔 경보 시작"; b.classList.remove("stop"); }
  }

  function hint(msg, warn) {
    const el = $("sessHint");
    el.textContent = msg || "";
    el.classList.toggle("warn", !!warn);
  }

  function switchPanel(mode) {
    $("rtPanel").classList.toggle("hidden", mode !== "rt");
    $("sessPanel").classList.toggle("hidden", mode !== "sess");
    $("pmRt").classList.toggle("on", mode === "rt");
    $("pmSess").classList.toggle("on", mode === "sess");
    $("liveSide").classList.toggle("sesswide", mode === "sess");  // 2열 카드용 확폭
    if (mode === "sess") updateCards();
  }

  // ================================================== 공통 포맷
  const fmt1 = (v) => (v == null ? "—" : (Math.round(v * 10) / 10).toFixed(1));
  const hhmmss = (ts) => new Date(ts * 1000).toTimeString().slice(0, 8);

  function elapsedSec() {
    if (live) return live.elapsed_sec;
    if (result && result.ended_at) return result.ended_at - result.alarm_ts;
    return null;
  }
  function elapsedTxt() {
    const e = elapsedSec();
    if (e == null) return "—";
    const m = Math.floor(e / 60), s = Math.floor(e % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function nameOf(list, id) {
    const e = (list || []).find((x) => x.id === id);
    return (e && e.name) || id;
  }

  // ================================================== canvas 차트 헬퍼
  function cvCtx(cv) {
    const r = cv.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    const dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(r.width * dpr);
    cv.height = Math.round(r.height * dpr);
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, r.width, r.height);
    return { ctx, w: r.width, h: r.height };
  }

  /** 스파크라인 — null 구간 스킵, threshold 초과 구간은 붉게. */
  function spark(cv, vals, opts = {}) {
    const g = cvCtx(cv);
    if (!g) return;
    const { ctx, w, h } = g;
    const nums = vals.filter((v) => v != null);
    if (!nums.length) {
      ctx.fillStyle = "rgba(255,255,255,.3)";
      ctx.font = "10px Pretendard, sans-serif";
      ctx.fillText("데이터 없음", 5, h / 2 + 3);
      return;
    }
    let lo = opts.min != null ? opts.min : Math.min(...nums);
    let hi = opts.max != null ? opts.max : Math.max(...nums);
    if (opts.threshold != null) { lo = Math.min(lo, opts.threshold); hi = Math.max(hi, opts.threshold); }
    if (hi - lo < 1e-9) hi = lo + 1;
    const pad = 3;
    const X = (i) => pad + (w - 2 * pad) * (vals.length < 2 ? 1 : i / (vals.length - 1));
    const Y = (v) => h - pad - (h - 2 * pad) * ((v - lo) / (hi - lo));
    if (opts.threshold != null) {
      ctx.strokeStyle = "rgba(255,74,68,.55)";
      ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(0, Y(opts.threshold)); ctx.lineTo(w, Y(opts.threshold)); ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.lineWidth = 1.5;
    ctx.lineJoin = "round";
    let lastIdx = -1;
    for (let i = 0; i < vals.length; i++) {
      if (vals[i] == null) continue;
      if (lastIdx >= 0) {
        const over = opts.threshold != null &&
          (vals[i] > opts.threshold || vals[lastIdx] > opts.threshold);
        ctx.strokeStyle = over ? "#FF4A44" : (opts.color || "#30DCFB");
        ctx.beginPath();
        ctx.moveTo(X(lastIdx), Y(vals[lastIdx]));
        ctx.lineTo(X(i), Y(vals[i]));
        ctx.stroke();
      }
      lastIdx = i;
    }
    if (lastIdx >= 0) {                              // 마지막 점
      const over = opts.threshold != null && vals[lastIdx] > opts.threshold;
      ctx.fillStyle = over ? "#FF4A44" : (opts.color || "#30DCFB");
      ctx.beginPath(); ctx.arc(X(lastIdx), Y(vals[lastIdx]), 2.2, 0, 7); ctx.fill();
    }
  }

  /** 반원 게이지 (0~100) + 중앙 숫자. null → "—" (insufficient_data). */
  function gauge(cv, val) {
    const g = cvCtx(cv);
    if (!g) return;
    const { ctx, w, h } = g;
    const cx = w / 2, cy = h * 0.88, R = Math.min(w / 2 - 8, h * 0.74);
    ctx.lineWidth = 9;
    ctx.lineCap = "round";
    ctx.strokeStyle = "rgba(255,255,255,.12)";
    ctx.beginPath(); ctx.arc(cx, cy, R, Math.PI, 2 * Math.PI); ctx.stroke();
    let col = "rgba(255,255,255,.4)";
    if (val != null) {
      col = val >= 80 ? "#3FB950" : (val >= 60 ? "#F5A623" : "#FF4A44");
      ctx.strokeStyle = col;
      ctx.beginPath();
      ctx.arc(cx, cy, R, Math.PI, Math.PI + Math.PI * Math.min(100, Math.max(0, val)) / 100);
      ctx.stroke();
    }
    ctx.textAlign = "center";
    ctx.fillStyle = val != null ? "#fff" : "rgba(255,255,255,.4)";
    ctx.font = "700 26px Pretendard, sans-serif";
    ctx.fillText(val != null ? fmt1(val) : "—", cx, cy - 4);
    ctx.font = "10px Pretendard, sans-serif";
    ctx.fillStyle = "rgba(255,255,255,.45)";
    ctx.fillText(val != null ? "/ 100" : "insufficient data", cx, cy + 11);
    ctx.textAlign = "left";
  }

  /** 히스토그램 (0~100, 10구간). */
  function hist(cv, values) {
    const g = cvCtx(cv);
    if (!g) return;
    const { ctx, w, h } = g;
    const bins = new Array(10).fill(0);
    values.forEach((v) => {
      if (v == null) return;
      bins[Math.min(9, Math.max(0, Math.floor(v / 10)))]++;
    });
    const mx = Math.max(1, ...bins);
    const bw = (w - 12) / 10;
    for (let i = 0; i < 10; i++) {
      const bh = (h - 16) * (bins[i] / mx);
      ctx.fillStyle = i >= 8 ? "#3FB950" : (i >= 6 ? "#30DCFB" : "#F5A623");
      ctx.fillRect(6 + i * bw + 1, h - 12 - bh, bw - 2, bh);
    }
    ctx.fillStyle = "rgba(255,255,255,.4)";
    ctx.font = "9px Pretendard, sans-serif";
    ctx.fillText("0", 6, h - 2);
    ctx.fillText("EPFI 100", w - 46, h - 2);
  }

  // ================================================== 카드 렌더
  function updateCards() {
    if (!$("sessGrid")) return;
    const has = !!(live || result);
    $("sessNone").classList.toggle("hidden", has);
    $("sessGrid").classList.toggle("hidden", !has);
    $("sessMeta").classList.toggle("hidden", !has);
    $("sessFoot").classList.toggle("hidden", !result);
    if (!has) return;

    const sid = live ? live.session_id : result.session_id;
    const alarmTs = live ? live.alarm_ts : result.alarm_ts;
    // 세션은 시작 시점 설정 스냅샷으로 계산 (결정성) — 이후 변경은 다음 세션부터
    const cfgWarn = (live && live.config_version && lastSiteV > live.config_version)
      ? ` · <span class="warnbadge">⚠ 설정 v${lastSiteV} 변경됨 — 이 세션은 v${live.config_version} 기준, 다음 세션부터 적용</span>`
      : "";
    $("sessMeta").innerHTML =
      `<b>${sid}</b> · 경보 ${hhmmss(alarmTs)} · ` +
      (live ? `<span class="st-run">진행 중</span>` : `<span class="st-end">종료</span>`) +
      ` · 경과 <span class="t-num">${elapsedTxt()}</span>` + cfgWarn;
    document.querySelectorAll("#sessGrid .mela").forEach((el) => {
      el.textContent = "경과 " + elapsedTxt();
    });

    renderSei();
    renderCbs();
    renderEpfi();
    renderDev();
    renderIdr();
  }

  function renderSei() {
    const sei = live ? live.sei : result.sei;
    $("seiVal").textContent = sei != null ? fmt1(sei) : "—";

    const exits = (App.site && App.site.exits) || [];
    const box = $("seiBars");
    if (!exits.length) { box.innerHTML = `<div class="mnote">출입구 없음 — 맵 설정에서 추가</div>`; return; }

    // 실제 통과 인원 수집
    let actual = {};
    if (result) {
      result.exit_metrics.forEach((m) => { actual[m.exit_id] = m.actual_count; });
    } else {
      const tp = timeline[timeline.length - 1];
      actual = (tp && tp.exit_counts) || {};
    }
    const totE = exits.reduce((s, e) => s + (actual[e.id] || 0), 0);
    const totC = exits.reduce((s, e) => s + (e.design_capacity || 0), 0);
    const labels = exits.map((e) => e.name || e.id);
    const dShares = exits.map((e) => totC > 0 ? (e.design_capacity || 0) / totC : 1 / exits.length);
    const aShares = exits.map((e) => totE > 0 ? (actual[e.id] || 0) / totE : 0);
    const aCounts = exits.map((e) => actual[e.id] || 0);
    const cCounts = exits.map((e) => e.design_capacity || 0);

    const deltas = dShares.map((d, i) => aShares[i] - d);
    const maxAbsDelta = Math.max(...deltas.map(Math.abs), 0.001);

    box.innerHTML =
      `<div class="sei-legend"><span class="sei-leg-d">■ 설계</span><span class="sei-leg-a">■ 실제</span></div>` +
      `<canvas id="seiChartCv"></canvas>` +
      `<div class="sei-delta-hd">출구별 분포 차이 (실제 − 설계)</div>` +
      `<div id="seiDeltas"></div>`;

    requestAnimationFrame(() => {
      const cv = $("seiChartCv");
      if (cv) drawSeiGrouped(cv, labels, dShares, aShares);

      const dbox = $("seiDeltas");
      if (!dbox) return;
      const worstIdx = deltas.reduce((mi, d, i) => Math.abs(d) > Math.abs(deltas[mi]) ? i : mi, 0);
      dbox.innerHTML = deltas.map((d, i) => {
        const pct = Math.round(Math.abs(d) / maxAbsDelta * 100);
        const sign = d >= 0 ? "+" : "";
        const cls = Math.abs(d) < 0.005 ? "sei-even" : d > 0 ? "sei-over" : "sei-under";
        const worst = i === worstIdx && Math.abs(d) >= 0.005 ? " sei-worst" : "";
        return `<div class="sei-drow${worst}">
          <div class="sei-dlab">${labels[i]}</div>
          <div class="sei-dbar-wrap"><div class="sei-dbar ${cls}" style="width:${pct}%"></div></div>
          <div class="sei-dval ${cls}">${sign}${(d * 100).toFixed(1)}%</div>
        </div>`;
      }).join("");
    });
  }

  function drawSeiGrouped(cv, labels, dShares, aShares) {
    const n = labels.length;
    const W = cv.parentElement.clientWidth || 260;
    const H = 150;
    cv.width = W; cv.height = H;
    const ctx = cv.getContext("2d");

    const PAD_L = 30, PAD_R = 8, PAD_T = 10, PAD_B = 26;
    const plotW = W - PAD_L - PAD_R;
    const plotH = H - PAD_T - PAD_B;
    const slotW = plotW / n;
    const bw = Math.max(5, Math.min(18, slotW * 0.28));
    const bg = 3;

    // 배경
    ctx.fillStyle = "#111722";
    ctx.fillRect(0, 0, W, H);

    // 격자
    [0.25, 0.5, 0.75, 1.0].forEach((v) => {
      const y = PAD_T + plotH * (1 - v);
      ctx.strokeStyle = v === 1.0 ? "#2a3a50" : "#1e2b3a";
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - PAD_R, y); ctx.stroke();
      ctx.fillStyle = "#4a6078"; ctx.font = "9px sans-serif"; ctx.textAlign = "right";
      ctx.fillText(v.toFixed(2), PAD_L - 3, y + 3.5);
    });

    // 그룹 바
    labels.forEach((lbl, i) => {
      const cx = PAD_L + slotW * (i + 0.5);
      const bx_d = cx - bw - bg / 2;
      const bx_a = cx + bg / 2;

      // 설계 (청색)
      const h_d = plotH * Math.min(dShares[i], 1);
      const gd = ctx.createLinearGradient(0, PAD_T + plotH - h_d, 0, PAD_T + plotH);
      gd.addColorStop(0, "#5ab4e0"); gd.addColorStop(1, "#1e6898");
      ctx.fillStyle = gd;
      ctx.fillRect(bx_d, PAD_T + plotH - h_d, bw, h_d);

      // 실제 (오렌지)
      const h_a = plotH * Math.min(aShares[i], 1);
      const ga = ctx.createLinearGradient(0, PAD_T + plotH - h_a, 0, PAD_T + plotH);
      ga.addColorStop(0, "#f09050"); ga.addColorStop(1, "#b04818");
      ctx.fillStyle = ga;
      ctx.fillRect(bx_a, PAD_T + plotH - h_a, bw, h_a);

      // 비율 레이블 (바 위)
      ctx.font = "8px sans-serif"; ctx.textAlign = "center";
      if (dShares[i] > 0.04) {
        ctx.fillStyle = "#7ac8f0";
        ctx.fillText((dShares[i] * 100).toFixed(0) + "%", bx_d + bw / 2, PAD_T + plotH - h_d - 2);
      }
      if (aShares[i] > 0.04) {
        ctx.fillStyle = "#f0c090";
        ctx.fillText((aShares[i] * 100).toFixed(0) + "%", bx_a + bw / 2, PAD_T + plotH - h_a - 2);
      }

      // x 레이블
      ctx.fillStyle = "#5a7890"; ctx.font = "9px sans-serif";
      const short = lbl.length > 5 ? lbl.slice(0, 4) + "…" : lbl;
      ctx.fillText(short, cx, H - PAD_B + 12);
    });

    // 축
    ctx.strokeStyle = "#2e4060"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(PAD_L, PAD_T); ctx.lineTo(PAD_L, PAD_T + plotH);
    ctx.lineTo(W - PAD_R, PAD_T + plotH); ctx.stroke();
  }

  function renderCbs() {
    const cbs = live ? live.cbs_total : result.cbs_total;
    $("cbsVal").textContent = fmt1(cbs);
    spark($("cbsSpark"), timeline.map((t) => t.cbs_total), { min: 0, color: "#FF6F21" });

    const bns = (App.site && App.site.bottlenecks) || [];
    const box = $("cbsBn");
    if (!bns.length) { box.innerHTML = `<div class="mnote">병목 없음 — 맵 설정에서 추가</div>`; return; }
    const resBn = {};
    if (result) result.bottleneck_metrics.forEach((m) => { resBn[m.bottleneck_id] = m; });
    box.innerHTML = bns.map((b) => {
      const m = resBn[b.id];
      const last = timeline.length ? (timeline[timeline.length - 1].bottleneck_density || {})[b.id] : null;
      const tail = m ? `CBS ${fmt1(m.cbs)} · <span class="risk ${m.risk_level}">${m.risk_level}</span>`
                     : (last != null ? `${fmt1(last)}/m²` : "—");
      return `<div class="bnrow">
        <div class="bnlab"><span>${nameOf(bns, b.id)}</span><span class="bnval t-num">${tail}</span></div>
        <canvas class="bnspark" data-bn="${b.id}"></canvas>
      </div>`;
    }).join("");
    box.querySelectorAll(".bnspark").forEach((cv) => {
      const bid = cv.dataset.bn;
      const b = bns.find((x) => x.id === bid);
      spark(cv, timeline.map((t) => (t.bottleneck_density || {})[bid] != null
        ? t.bottleneck_density[bid] : null),
        { min: 0, threshold: b ? b.rho_crit : null, color: "#FF6F21" });
    });
  }

  function renderEpfi() {
    const ep = live ? live.epfi_avg : result.epfi_avg;
    $("epfiVal").textContent = ep != null ? fmt1(ep) : "—";
    spark($("epfiSpark"), timeline.map((t) => t.epfi_avg), { min: 0, max: 100, color: "#3FB950" });
    const cv = $("epfiHist"), note = $("epfiNote");
    if (result && result.person_metrics.length) {
      cv.classList.remove("hidden");
      note.textContent = `객체 ${result.person_metrics.length}개 분포`;
      hist(cv, result.person_metrics.map((p) => p.epfi));
    } else {
      const g = cvCtx(cv);
      if (g) { /* 비움 */ }
      note.textContent = live ? "객체별 분포는 세션 종료 후 표시됩니다." : "객체 데이터 없음";
    }
  }

  // ------------------------------- 객체별 d_i(t) 이탈 곡선 (EPFI 근거 시각화)
  function renderDev() {
    const wrap = $("devWrap");
    if (!wrap) return;
    const ids = personSeries
      ? Object.keys(personSeries).filter((g) => personSeries[g].series.length > 1) : [];
    if (!result || !ids.length) { wrap.classList.add("hidden"); return; }
    wrap.classList.remove("hidden");
    const sel = $("devSel");
    if (sel.dataset.sid !== result.session_id) {      // 세션 바뀔 때만 재구성
      const em = {};
      (result.person_metrics || []).forEach((p) => { em[p.global_track_id] = p.epfi; });
      sel.innerHTML = ids
        .sort((a, b) => (em[a] != null ? em[a] : 101) - (em[b] != null ? em[b] : 101))
        .map((g) => `<option value="${g}">${g} — EPFI ${em[g] != null ? Math.round(em[g]) : "—"}</option>`)
        .join("");
      sel.dataset.sid = result.session_id;
      sel.onchange = renderDev;
    }
    const gid = sel.value || ids[0];
    if (personSeries[gid]) drawDev($("devChart"), personSeries[gid].series);
  }

  function drawDev(cv, series) {
    const g = cvCtx(cv);
    if (!g || !series.length) return;
    const { ctx, w, h } = g;
    const dAllow = (App.site && App.site.thresholds) ? App.site.thresholds.d_allow : null;
    const t0 = series[0][0], t1 = series[series.length - 1][0] || t0 + 1;
    const dmax = Math.max(dAllow || 0, ...series.map((s) => s[1])) * 1.15 || 1;
    const PL = 5, PR = 5, PT = 6, PB = 14;
    const X = (t) => PL + (t - t0) / (t1 - t0 || 1) * (w - PL - PR);
    const Y = (d) => PT + (1 - d / dmax) * (h - PT - PB);

    // ∫d dt 면적
    ctx.beginPath(); ctx.moveTo(X(t0), Y(0));
    series.forEach(([t, d]) => ctx.lineTo(X(t), Y(d)));
    ctx.lineTo(X(t1), Y(0)); ctx.closePath();
    ctx.fillStyle = "rgba(63,185,80,.2)"; ctx.fill();

    // d(t) 곡선
    ctx.beginPath();
    series.forEach(([t, d], i) => (i ? ctx.lineTo(X(t), Y(d)) : ctx.moveTo(X(t), Y(d))));
    ctx.strokeStyle = "#3FB950"; ctx.lineWidth = 1.5; ctx.stroke();

    // d_allow 기준선 — 레이블은 우측 끝 + 반투명 배경으로 겹침 방지
    if (dAllow != null) {
      const ly = Y(dAllow);
      ctx.setLineDash([4, 3]); ctx.strokeStyle = "rgba(255,91,91,.7)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(PL, ly); ctx.lineTo(w - PR, ly); ctx.stroke();
      ctx.setLineDash([]);
      const lbl = `d_allow ${dAllow}m`;
      ctx.font = "9px Pretendard, sans-serif";
      const tw = ctx.measureText(lbl).width;
      const lx = w - PR - tw - 3;
      const lblY = ly - 3;
      ctx.fillStyle = "rgba(14,18,28,.75)";
      ctx.fillRect(lx - 2, lblY - 9, tw + 4, 12);
      ctx.fillStyle = "#FF5B5B"; ctx.textAlign = "left";
      ctx.fillText(lbl, lx, lblY);
    }

    // 시간 레이블 (하단)
    ctx.font = "9px Pretendard, sans-serif"; ctx.fillStyle = "rgba(255,255,255,.32)";
    ctx.textAlign = "left";  ctx.fillText("0s", PL, h - 2);
    ctx.textAlign = "right"; ctx.fillText(`${Math.round(t1 - t0)}s`, w - PR, h - 2);
    ctx.textAlign = "left";
  }

  function drawIdrTimeline(cv, { maxWindow, delay, elapsed, isDetected }) {
    const ctx = cv.getContext("2d");
    const W = cv.width, H = cv.height;
    ctx.clearRect(0, 0, W, H);

    const AX = 6, AXR = W - 6, AY = Math.round(H * 0.45);
    const usable = AXR - AX;
    const scale = usable / maxWindow;

    const dashV = (x, color, y0, y1) => {
      ctx.save(); ctx.setLineDash([3, 3]);
      ctx.strokeStyle = color; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x + 0.5, y0); ctx.lineTo(x + 0.5, y1); ctx.stroke();
      ctx.restore();
    };

    // axis + arrow
    ctx.strokeStyle = "#3a3f4b"; ctx.lineWidth = 1.5; ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(AX, AY); ctx.lineTo(AXR, AY); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(AXR, AY); ctx.lineTo(AXR - 5, AY - 3);
    ctx.moveTo(AXR, AY); ctx.lineTo(AXR - 5, AY + 3);
    ctx.stroke();

    if (isDetected) {
      const dx = Math.min(AX + delay * scale, AXR - 4);
      const grd = ctx.createLinearGradient(AX, 0, dx, 0);
      grd.addColorStop(0, "rgba(63,185,80,.12)");
      grd.addColorStop(1, "rgba(63,185,80,.45)");
      ctx.fillStyle = grd;
      ctx.fillRect(AX, AY - 8, dx - AX, 16);

      dashV(AX, "#6a7080", AY - 16, AY + 14);
      dashV(dx, "#3FB950", AY - 16, AY + 14);

      ctx.font = "9px sans-serif"; ctx.textAlign = "center";
      ctx.fillStyle = "#6a7080"; ctx.fillText("경보", AX + 10, AY + 24);
      ctx.fillStyle = "#3FB950"; ctx.fillText("개시", Math.min(dx, AXR - 16), AY + 24);

      ctx.fillStyle = "#3FB950"; ctx.font = "bold 10px monospace";
      ctx.textAlign = "right";
      ctx.fillText(`Δt ${delay.toFixed(1)}s`, AXR - 2, AY - 11);
    } else {
      const nx = Math.min(AX + elapsed * scale, AXR - 8);
      ctx.fillStyle = "rgba(200,210,230,.07)";
      ctx.fillRect(AX, AY - 8, nx - AX, 16);

      dashV(AX, "#6a7080", AY - 16, AY + 14);
      ctx.save(); ctx.setLineDash([3, 4]);
      ctx.strokeStyle = "#3a3f4b"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(nx + 0.5, AY - 12); ctx.lineTo(nx + 0.5, AY + 10); ctx.stroke();
      ctx.restore();

      ctx.font = "9px sans-serif"; ctx.fillStyle = "#6a7080"; ctx.textAlign = "center";
      ctx.fillText("경보", AX + 10, AY + 24);
      ctx.fillStyle = "#464c58"; ctx.textAlign = "right"; ctx.font = "10px monospace";
      ctx.fillText(`${elapsed.toFixed(0)}s…`, AXR - 2, AY - 11);
    }
  }

  function renderIdr() {
    const zones = (App.site && App.site.zones) || [];
    const wrap = $("idrTblWrap");

    const zm = live
      ? (live.zone_metrics || [])
      : (result ? (result.zone_metrics || []) : []);
    const elapsed = live ? live.elapsed_sec : (result ? (result.ended_at || result.alarm_ts) - result.alarm_ts : 0);

    if (live) {
      $("idrProg").textContent = `${live.zones_started}/${live.zones_total}`;
    } else if (result) {
      $("idrProg").textContent = `${zm.filter((z) => z.status === "started").length}/${zm.length}`;
    }

    if (!zm.length) {
      wrap.innerHTML = `<div class="mnote">구역 없음 — 맵 설정에서 추가</div>`;
      return;
    }

    const maxDelay = zm.reduce((mx, z) => Math.max(mx, z.response_delay_sec || 0), 0);
    const maxWindow = Math.max(elapsed, maxDelay + 5, 20);

    wrap.innerHTML = zm.map((z) => {
      const name = nameOf(zones, z.zone_id);
      const det = z.status === "started";
      const perO = (z.idr_per_origin || []).filter((v) => v != null);
      const idrTip = perO.length > 1
        ? `title="경보원별: ${perO.map((v) => v.toFixed(2)).join(" / ")} m/s"` : "";
      const idrTxt = det && z.idr != null ? z.idr.toFixed(2) : "—";
      const delayTxt = det ? `반응 ${fmt1(z.response_delay_sec)}s` : "미반응";
      return `<div class="idr-row ${det ? "det" : ""}">
        <div class="idr-top">
          <span class="idr-zn" title="${name}">${name}</span>
          <canvas class="idr-cv" id="idrcv_${z.zone_id}" height="46"></canvas>
        </div>
        <div class="idr-stat">
          <span class="idr-delay ${det ? "ok" : ""}">${delayTxt}</span>
          <span class="idr-val ${det && z.idr != null ? "ok" : ""}" ${idrTip}>${idrTxt}</span>
          <span class="idr-unit">m/s</span>
        </div>
      </div>`;
    }).join("");

    requestAnimationFrame(() => {
      zm.forEach((z) => {
        const cv = document.getElementById(`idrcv_${z.zone_id}`);
        if (!cv) return;
        cv.width = cv.getBoundingClientRect().width || 180;
        drawIdrTimeline(cv, {
          maxWindow,
          delay: z.response_delay_sec,
          elapsed,
          isDetected: z.status === "started",
        });
      });
    });
  }

  // ================================================== 결과 모달
  function showResultModal() {
    if (!result) return;
    const r = result;
    const dur = r.ended_at ? (r.ended_at - r.alarm_ts) : 0;
    const started = r.zone_metrics.filter((z) => z.status === "started").length;
    const totOut = r.exit_metrics.reduce((s, e) => s + e.actual_count, 0);
    const row = (k, v) => `<div class="resrow"><span>${k}</span><b class="t-num">${v}</b></div>`;
    $("resTitle").textContent = `평가 세션 결과 — ${r.session_id}`;
    $("resBody").innerHTML = `
      <div class="resbig">
        <div class="resmet"><span>SEI</span><b>${r.sei != null ? fmt1(r.sei) : "—"}</b><i>${r.sei == null ? "insufficient_data" : "출구 활용 효율"}</i></div>
        <div class="resmet"><span>EPFI 평균</span><b>${r.epfi_avg != null ? fmt1(r.epfi_avg) : "—"}</b><i>경로 충실도</i></div>
        <div class="resmet"><span>CBS 총</span><b>${fmt1(r.cbs_total)}</b><i>혼잡 누적</i></div>
        <div class="resmet"><span>IDR 개시</span><b>${started}/${r.zone_metrics.length}</b><i>구역 반응</i></div>
      </div>
      ${row("경보 시각", hhmmss(r.alarm_ts))}
      ${row("종료 시각", r.ended_at ? hhmmss(r.ended_at) : "—")}
      ${row("평가 시간", `${Math.floor(dur / 60)}분 ${Math.floor(dur % 60)}초`)}
      ${row("경보 발생원", (() => {
        const aos = r.alarm_origins && r.alarm_origins.length
          ? r.alarm_origins
          : [r.alarm_origin];
        return aos.map((o, i) => `#${i+1}(${Math.round(o[0])},${Math.round(o[1])})`).join(" · ");
      })())}
      ${row("출구 총 통과", `${totOut}명 · 출구 ${r.exit_metrics.length}곳`)}
      ${row("추적 객체", `${r.person_metrics.length}개`)}
      ${row("설정 버전", `calibration v${r.calibration_version} · config v${r.config_version}`)}
    `;
    $("resultModal").classList.remove("hidden");
  }

  // ================================================== 외부 노출
  function updateUI() {
    setBtn();
    updateCards();
    if (onMapRender) onMapRender();
  }

  return {
    init, bootstrap, onState, placeAlarm, updateCards,
    isPlacing: () => placing,
    alarmOrigins: () => {
      const src = live || result;
      if (!src) return [];
      return (src.alarm_origins && src.alarm_origins.length)
        ? src.alarm_origins
        : (src.alarm_origin ? [src.alarm_origin] : []);
    },
    alarmOrigin: () => (live ? live.alarm_origin : (result ? result.alarm_origin : null)),
    isActive: () => !!live,
  };
})();

window.Session = Session;  // 명시적 전역 노출 — view_live의 window.Session 가드용
