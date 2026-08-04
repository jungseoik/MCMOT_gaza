/* 화면 4: 리플레이 — 저장된 경보 세션을 2D 도면 위에 그대로 재생하고,
 * 임계값(4대 지표 세팅)을 바꿔 재계산한다 (CONTRACT v1.10).
 * 영상이 아니라 "분석 결과(사람 위치·궤적·지표)"를 재생한다. 도면은 그대로. */
"use strict";

var Views = window.Views || (window.Views = {});

Views.replay = (() => {
  const $ = (id) => document.getElementById(id);
  let inited = false, active = false;
  let mc = null;

  let sessions = [];        // 이력 목록 [{session_id, sei, epfi_avg, cbs_total, has_record}]
  let selId = null;         // 선택된 session_id
  let baseRow = null;       // 선택 세션의 원본 요약(비교용)
  let data = null;          // {result, timeline, frames, site, meta}
  let site = null;          // 세션 당시 공간요소 (배경 렌더)

  // 재생 상태
  let playing = false;
  let cursor = 0;           // 재생 위치(세션 초, frames[0].ts 기준 0)
  let duration = 0;         // 전체 길이(초)
  let speed = 1;
  let lastRaf = 0, rafId = null;
  const renderFps = () => Math.min(30, Math.max(1, parseInt($("rpFps").value) || 20));

  const TH_KEYS = [["rpV","v_th"],["rpA","a_th"],["rpR","r_th"],["rpDt","dt_hold"],
                   ["rpD","d_allow"],["rpQd","q_design"],["rpMc","min_conf"]];

  // ------------------------------------------------------------ 세션 목록
  async function loadList() {
    $("rpConn").textContent = "불러오는 중…";
    try {
      sessions = await API.getSessions();
    } catch (e) { sessions = []; }
    renderList();
    $("rpConn").textContent = `${sessions.length}건`;
  }

  function fmtVal(v, d) { return (v == null) ? "—" : (+v).toFixed(d); }

  function renderList() {
    const box = $("rpSessList");
    if (!sessions.length) {
      box.innerHTML = `<div class="grow">저장된 세션 없음 — ③ 운영 뷰에서 경보 세션을 실행하면 이력이 쌓입니다.</div>`;
      return;
    }
    box.innerHTML = sessions.map((s) => {
      const t = s.alarm_ts ? new Date(s.alarm_ts * 1000).toLocaleString("ko-KR", {hour12:false}) : s.session_id;
      const rec = s.has_record
        ? `<span class="badge ok">재생가능</span>`
        : `<span class="badge" title="녹화 이전 세션 — 지표 요약만 조회 가능">요약만</span>`;
      return `<div class="camrow rpsess${s.session_id === selId ? " sel" : ""}" data-id="${s.session_id}">
        <div class="r1"><span class="nm">${t}</span>${rec}</div>
        <div class="r2"><span>SEI ${fmtVal(s.sei,0)}</span>
          <span>EPFI ${fmtVal(s.epfi_avg,0)}</span>
          <span>CBS ${fmtVal(s.cbs_total,1)}</span></div></div>`;
    }).join("");
    box.querySelectorAll(".rpsess").forEach((el) => {
      el.onclick = () => selectSession(el.dataset.id);
    });
  }

  // ------------------------------------------------------------ 세션 선택·로드
  async function selectSession(id) {
    if (playing) pause();
    selId = id;
    baseRow = sessions.find((s) => s.session_id === id) || null;
    renderList();
    const s = baseRow;
    if (s && !s.has_record) {
      $("rpHint").textContent = "이 세션은 녹화 이전이라 2D 재생이 불가합니다 (지표 요약만 존재).";
      setControlsEnabled(false);
      data = null; site = null; if (mc) mc.render();
      showMetricsFromRow(s);
      return;
    }
    $("rpHint").textContent = "재생 데이터를 불러오는 중…";
    $("rpMsg").textContent = "";
    try {
      data = await API.replaySession(id, { fps: 5 });
    } catch (e) {
      $("rpHint").textContent = "재생 로드 실패: " + e.message;
      return;
    }
    site = data.site || null;
    prepPlayback();
    fillThresholds(site && site.thresholds);
    showMetrics(data.result, "현재값");
    setControlsEnabled(true);
    $("rpReset").disabled = false; $("rpApply").disabled = false;
    $("rpHint").textContent = `${(data.frames||[]).length} 프레임 · ${fmtDur(duration)} · 트랙 ${data.meta && data.meta.track_row_count || 0}행`;
    if (mc) { setCanvasImage(); goTo(0); }
  }

  function prepPlayback() {
    const f = (data && data.frames) || [];
    duration = f.length ? (f[f.length - 1].ts - f[0].ts) : 0;
    cursor = 0;
    $("rpSeek").max = String(Math.max(0, f.length - 1));
    $("rpSeek").value = "0";
  }

  function setCanvasImage() {
    if (App.mapImg && App.site && App.site.map)
      mc.setImage(App.mapImg, App.site.map.w, App.site.map.h);
    else if (site && site.map) mc.setImage(null, site.map.w, site.map.h);
    else mc.setImage(null, 1000, 600);
  }

  // ------------------------------------------------------------ 재생 컨트롤
  function setControlsEnabled(on) {
    ["rpPlay","rpToStart","rpSeek"].forEach((id) => { $(id).disabled = !on; });
  }

  function play() {
    if (!data || !data.frames || !data.frames.length) return;
    if (cursor >= duration - 1e-3) cursor = 0;   // 끝이면 처음부터
    playing = true; $("rpPlay").textContent = "⏸ 일시정지";
    lastRaf = performance.now();
  }
  function pause() { playing = false; $("rpPlay").textContent = "▶ 재생"; }
  function togglePlay() { playing ? pause() : play(); }

  function goTo(idx) {                             // 슬라이더(프레임 인덱스) → cursor
    const f = data && data.frames; if (!f || !f.length) return;
    idx = Math.max(0, Math.min(f.length - 1, idx | 0));
    cursor = f[idx].ts - f[0].ts;
    $("rpSeek").value = String(idx);
    updateTimeLabel();
  }

  function frameIndexAt(cur) {                     // cursor(초) → 프레임 인덱스(≤)
    const f = data.frames, t0 = f[0].ts, target = t0 + cur;
    let lo = 0, hi = f.length - 1, ans = 0;
    while (lo <= hi) { const m = (lo + hi) >> 1;
      if (f[m].ts <= target) { ans = m; lo = m + 1; } else hi = m - 1; }
    return ans;
  }

  function fmtDur(s) {
    s = Math.max(0, Math.round(s));
    return `${String(Math.floor(s/60)).padStart(2,"0")}:${String(s%60).padStart(2,"0")}`;
  }
  function updateTimeLabel() { $("rpTime").textContent = `${fmtDur(cursor)} / ${fmtDur(duration)}`; }

  // ------------------------------------------------------------ 렌더
  function currentInterp() {
    // cursor 위치의 두 프레임 보간 → {objs:[{gid,cam_id,x,y,vx,vy}], state}
    const f = data.frames, t0 = f[0].ts;
    const i = frameIndexAt(cursor);
    const a = f[i], b = f[Math.min(f.length - 1, i + 1)];
    const span = (b.ts - a.ts) || 1;
    const alpha = Math.max(0, Math.min(1, (t0 + cursor - a.ts) / span));
    const bpos = {}; (b.objects || []).forEach((o) => { bpos[o.gid] = o; });
    const objs = (a.objects || []).map((o) => {
      const nb = bpos[o.gid];
      return { gid:o.gid, cam_id:o.cam_id,
               x: nb ? o.x + (nb.x - o.x) * alpha : o.x,
               y: nb ? o.y + (nb.y - o.y) * alpha : o.y,
               vx:o.vx, vy:o.vy };
    });
    return { objs, state: a };
  }

  function overlay(g) {
    if (site) drawSiteElements(g, site, { state: dataState() });
    drawAlarmOrigins(g);
    if (!data || !data.frames || !data.frames.length) return;
    const { ctx, TX, TY } = g;
    const { objs } = currentInterp();
    objs.forEach((o) => {
      const x = TX(o.x), y = TY(o.y), col = camColor(o.cam_id, App.cameras);
      if (o.vx || o.vy) {
        const L = 16, ex = x + o.vx * L, ey = y + o.vy * L;
        ctx.strokeStyle = col; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(ex, ey); ctx.stroke();
        mcArrowHead(ctx, ex, ey, Math.atan2(o.vy, o.vx), 6, col);
      }
      ctx.fillStyle = col;
      ctx.beginPath(); ctx.arc(x, y, 4.5, 0, 7); ctx.fill();
      ctx.strokeStyle = "rgba(0,0,0,.55)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(x, y, 4.5, 0, 7); ctx.stroke();
      if (g.s > 0.7) {
        ctx.font = "10px Pretendard, sans-serif"; ctx.fillStyle = "rgba(0,0,0,.7)";
        ctx.fillText(o.gid, x + 7, y + 4);
      }
    });
  }

  function dataState() {                           // drawSiteElements용 상태(구역/병목/출구 카운트)
    if (!data || !data.frames || !data.frames.length) return null;
    return data.frames[frameIndexAt(cursor)];
  }

  function drawAlarmOrigins(g) {
    const os = data && data.meta && data.meta.alarm_origins;
    if (!os || !os.length) return;
    const { ctx, TX, TY } = g;
    os.forEach((o, i) => {
      const x = TX(o[0]), y = TY(o[1]);
      ctx.strokeStyle = "#ff5b5b"; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, 10, 0, 7); ctx.stroke();
      ctx.beginPath(); ctx.arc(x, y, 3, 0, 7); ctx.fillStyle = "#ff5b5b"; ctx.fill();
      ctx.font = "13px Pretendard, sans-serif";
      ctx.fillText(os.length > 1 ? `🔔${i + 1}` : "🔔", x + 12, y - 8);
    });
  }

  // ------------------------------------------------------------ 지표 패널
  function showMetrics(res, tag) {
    $("rpTag").textContent = tag || "현재값";
    $("rpSei").textContent = res.sei == null ? "—" : Math.round(res.sei);
    $("rpEpfi").textContent = res.epfi_avg == null ? "—" : Math.round(res.epfi_avg);
    $("rpCbs").textContent = (res.cbs_total || 0).toFixed(1);
    const zm = res.zone_metrics || [];
    const started = zm.filter((z) => z.status === "started").length;
    $("rpIdr").textContent = `${started}/${zm.length}`;
    if (baseRow) {
      $("rpBase").innerHTML = `<span class="rpbase">원본 저장값 — SEI ${fmtVal(baseRow.sei,0)} · `
        + `EPFI ${fmtVal(baseRow.epfi_avg,0)} · CBS ${fmtVal(baseRow.cbs_total,1)}</span>`;
    } else $("rpBase").innerHTML = "";
  }

  function showMetricsFromRow(s) {                 // 녹화 없는 세션 — 요약만
    $("rpTag").textContent = "원본 저장값";
    $("rpSei").textContent = fmtVal(s.sei,0) === "—" ? "—" : Math.round(s.sei);
    $("rpEpfi").textContent = s.epfi_avg == null ? "—" : Math.round(s.epfi_avg);
    $("rpCbs").textContent = (s.cbs_total || 0).toFixed(1);
    $("rpIdr").textContent = "—";
    $("rpBase").innerHTML = "";
  }

  // ------------------------------------------------------------ 임계값
  function fillThresholds(th) {
    th = th || {};
    TH_KEYS.forEach(([id, key]) => { if (th[key] != null) $(id).value = th[key]; });
    // ρcrit — 세션 병목들의 대표값(첫 병목) 또는 2.0
    const bns = (site && site.bottlenecks) || [];
    $("rpRho").value = bns.length ? bns[0].rho_crit : 2.0;
  }

  function collectOverrides() {
    const thresholds = {};
    TH_KEYS.forEach(([id, key]) => {
      const v = parseFloat($(id).value);
      if (!isNaN(v)) thresholds[key] = v;
    });
    const ov = { thresholds, fps: 5 };
    const rho = parseFloat($("rpRho").value);
    if (!isNaN(rho)) ov.rho_crit = rho;
    return ov;
  }

  async function recompute() {
    if (!selId || !data) return;
    $("rpMsg").textContent = "재계산 중…"; $("rpApply").disabled = true;
    const keepIdx = frameIndexAt(cursor);
    try {
      data = await API.replaySession(selId, collectOverrides());
      site = data.site || site;
      prepPlayback();
      goTo(Math.min(keepIdx, (data.frames||[]).length - 1));  // 위치 유지
      showMetrics(data.result, "재계산값");
      $("rpMsg").textContent = "재계산 완료 — 원본 저장값은 그대로 보존됩니다.";
      if (mc) mc.render();
    } catch (e) {
      $("rpMsg").textContent = "재계산 실패: " + e.message;
    } finally { $("rpApply").disabled = false; }
  }

  // ------------------------------------------------------------ RAF 루프
  function loop(ts) {
    if (!active) { rafId = null; return; }
    rafId = requestAnimationFrame(loop);
    if (playing && data && data.frames && data.frames.length) {
      const dt = (ts - lastRaf) / 1000;
      lastRaf = ts;
      cursor += dt * speed;
      if (cursor >= duration) { cursor = duration; pause(); }
      $("rpSeek").value = String(frameIndexAt(cursor));
      updateTimeLabel();
    } else { lastRaf = ts; }
    if (mc) mc.render();
  }

  // ------------------------------------------------------------ lifecycle
  function init() {
    if (inited) return;
    inited = true;
    mc = new MapCanvas($("rpCv"), { draw: overlay });
    $("rpPlay").onclick = togglePlay;
    $("rpToStart").onclick = () => { pause(); goTo(0); if (mc) mc.render(); };
    $("rpSeek").oninput = (e) => { pause(); goTo(parseInt(e.target.value)); if (mc) mc.render(); };
    $("rpSpeed").onchange = (e) => { speed = parseFloat(e.target.value) || 1; };
    $("rpApply").onclick = recompute;
    $("rpReset").onclick = () => { fillThresholds(site && site.thresholds); $("rpMsg").textContent = "원래값으로 되돌림 — [재계산]을 눌러 반영"; };
  }

  function enter() {
    init();
    active = true;
    setCanvasImage();
    loadList();
    if (selId) {                                   // 재진입 시 선택 유지
      const still = sessions.find((s) => s.session_id === selId);
      if (!still) { selId = null; data = null; site = null; }
    }
    lastRaf = performance.now();
    rafId = requestAnimationFrame(loop);
  }

  function leave() {
    active = false; pause();
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
  }

  return { enter, leave };
})();
