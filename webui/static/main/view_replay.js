/* 화면 4: 리플레이 — 저장된 경보 세션을 2D 도면 위에 그대로 재생하고,
 * 임계값(4대 지표 세팅)을 바꿔 재계산한다 (CONTRACT v1.10).
 * 영상이 아니라 "분석 결과(사람 위치·궤적·지표)"를 재생한다. 도면은 그대로. */
"use strict";

var Views = window.Views || (window.Views = {});

Views.replay = (() => {
  const $ = (id) => document.getElementById(id);
  let inited = false, active = false;
  let mc = null;

  let bnPanel = null;       // CBS 병목 선택 집계 패널 (session.js CbsBnPanel — ③과 동일 UI)
  let sessions = [];        // 이력 목록 [{session_id, sei, epfi_avg, cbs_total, has_record}]
  let selId = null;         // 선택된 session_id
  let baseRow = null;       // 선택 세션의 원본 요약(비교용)
  let data = null;          // {result, timeline, frames, site, meta}
  let site = null;          // 세션 당시 공간요소 (배경 렌더)

  // 건물 드릴 모드(Phase 2·3) — 전 층 공유 세션 이력·재계산
  let mode = "drill";       // "drill"(건물 훈련, 기본 — 리허설도 건물 세션) | "sess"(개별 층, 디버그)
  let modeAuto = false;     // 최초 진입 시 사이트에 맞는 기본 모드 1회 자동 설정
  let drills = [];          // 드릴 이력 [{session_id, alarm_ts, floors, epfi_avg, ..., has_record}]
  let drill = null;         // 선택 드릴의 재산출 DrillResult
  let drillFrames = {};     // {floor: frames[]} — 층별 2D 재생 프레임
  let drillSites = {};      // {floor: site_view} — 층별 배경 공간요소
  let curDrillFloor = null; // 현재 재생 중인 층

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
    if (mode === "drill") {
      try { drills = await API.getDrills(); } catch (e) { drills = []; }
      renderDrillList();
      $("rpConn").textContent = `${drills.length}건`;
      return;
    }
    try {
      sessions = await API.getSessions();
      // 훈련(건물)에 속한 층별 세션은 '개별 층' 목록에서 제외 — 같은 이벤트 중복 노출 방지.
      // (진짜 단독 단일-층 세션만 남긴다.)
      try {
        const dids = new Set((await API.getDrills()).map((d) => d.session_id));
        sessions = sessions.filter((s) => !dids.has(s.session_id));
      } catch (e) { /* 훈련 조회 실패 시 원본 유지 */ }
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

  // ------------------------------------------------------------ 건물 드릴 모드
  function renderDrillList() {
    const box = $("rpSessList");
    if (!drills.length) {
      box.innerHTML = `<div class="grow">저장된 건물 훈련 없음 — ③ 운영 뷰에서 [🔔 건물 전체 경보]를 실행하면 이력이 쌓입니다.</div>`;
      return;
    }
    box.innerHTML = drills.map((d) => {
      const dt = d.alarm_ts ? new Date(d.alarm_ts * 1000) : null;
      const p2 = (n) => String(n).padStart(2, "0");
      // 좁은 사이드바(280px) — 시각은 "08/28 04:05" 로, 전체 시각은 title 로
      const t = dt ? `${p2(dt.getMonth() + 1)}/${p2(dt.getDate())} ${p2(dt.getHours())}:${p2(dt.getMinutes())}` : d.session_id;
      const tFull = dt ? dt.toLocaleString("ko-KR", {hour12:false}) : d.session_id;
      const rec = d.has_record
        ? `<span class="badge ok">재계산가능</span>`
        : `<span class="badge" title="일부 층 녹화 없음 — 재계산·재생 불가">요약만</span>`;
      const gidb = d.global_id
        ? `<span class="badge" title="글로벌 ID(카메라 간 동일인 연결)로 측정된 훈련 — 결과에 개인 이동 기록 포함">🌐</span>` : "";
      // 라벨(🎬 패키지 — 시나리오)은 행 제목으로 — 배지에 넣으면 길어서 행이 두세 줄로 깨진다.
      // 패키지명은 title 로 내리고 시나리오 부분만 보인다("🎬 전체 (01~14 연속)").
      let title = tFull;
      if (d.label) {
        const parts = String(d.label).split(" — ");
        title = parts.length > 1 ? `${parts[0].split(" ")[0]} ${parts.slice(1).join(" — ")}` : d.label;
      }
      const floors = (d.floors || []).map((f) => floorName(f)).join("·");
      return `<div class="camrow rpsess${d.session_id === selId ? " sel" : ""}" data-id="${d.session_id}">
        <div class="r1"><span class="nm" title="${(d.label || "") + " · " + d.session_id}">${title}</span>${gidb}${rec}</div>
        <div class="r2"><span class="cid" title="${tFull}">${t}</span><span>${floors}</span>
          <span class="mtr">EPFI ${fmtVal(d.epfi_avg,0)} · CBS ${fmtVal(d.cbs_total,1)} · 통과 ${d.total_passed || 0}</span></div></div>`;
    }).join("");
    box.querySelectorAll(".rpsess").forEach((el) => {
      el.onclick = () => {
        const d = drills.find((x) => x.session_id === el.dataset.id);
        if (d && !d.has_record) {
          selId = el.dataset.id; renderDrillList(); clearMetrics();
          setControlsEnabled(false); $("rpReset").disabled = true; $("rpApply").disabled = true;
          $("rpReport").disabled = false;
          $("rpHint").textContent = "이 훈련은 일부 층 녹화가 없어 재계산·재생이 불가합니다 (요약만).";
          drill = null;
          return;
        }
        selectDrill(el.dataset.id);
      };
    });
  }

  const floorName = (f) => (typeof App !== "undefined" ? App.floorName(f) : f);
  const floorResultOf = (f) => {
    const pf = (drill && drill.per_floor || []).find((p) => p.floor_id === f);
    return pf ? pf.result : null;
  };

  async function selectDrill(id) {
    if (playing) pause();
    selId = id;
    baseRow = drills.find((d) => d.session_id === id) || null;
    renderDrillList();
    $("rpHint").textContent = "건물 훈련 재계산·재생 데이터를 불러오는 중…";
    $("rpMsg").textContent = "";
    let resp;
    try { resp = await API.drillReplay(id, { fps: 5 }); }
    catch (e) { $("rpHint").textContent = "훈련 로드 실패: " + e.message; return; }
    drill = resp.drill;
    drillFrames = resp.frames_by_floor || {};
    drillSites = resp.site_by_floor || {};
    const floors = drill.floors || [];
    $("rpFloorSel").innerHTML = floors.map((f) =>
      `<option value="${f}">${floorName(f)}</option>`).join("");
    showBuildingMetrics(drill, "원본값");
    const st0 = drillSites[floors[0]];
    fillThresholds(st0 && st0.thresholds);
    setControlsEnabled(true);
    $("rpReset").disabled = false; $("rpApply").disabled = false; $("rpReport").disabled = false;
    if (floors.length) loadDrillFloor(floors[0]);
    $("rpHint").textContent = `건물 훈련 · 참여 ${floors.length}개 층 — 층을 골라 2D 재생, 임계값을 바꿔 [재계산]하면 건물 지표가 갱신됩니다.`;
  }

  // 선택 층의 프레임을 기존 재생 파이프라인(data/site)에 실어 그대로 재생.
  // seekAbsTs(선택): 이 절대 시각(초)으로 맞춘다 — 층 전환 시 같은 순간 유지용.
  // (드릴은 전 층 t_alarm 공유라 절대 ts로 맞추면 다른 층의 '같은 시점'이 보인다.)
  function loadDrillFloor(floor, seekAbsTs) {
    curDrillFloor = floor;
    $("rpFloorSel").value = floor;
    const st = drillSites[floor] || null;
    site = st;
    data = { frames: drillFrames[floor] || [], site: st, result: floorResultOf(floor),
             meta: { alarm_origins: (st && st.alarm_origins) || [] } };
    prepPlayback();
    setDrillCanvasImage(floor, st);
    const f = data.frames;
    if (seekAbsTs != null && f.length) {
      cursor = Math.max(0, Math.min(duration, seekAbsTs - f[0].ts));
      $("rpSeek").value = String(frameIndexAt(cursor));
      updateTimeLabel();
    } else {
      goTo(0);
    }
    renderRpBn();                       // 층 전환·재계산 후 그 층 병목 기준으로 갱신
    if (mc) mc.render();
  }

  function setDrillCanvasImage(floor, st) {
    if (!mc || !st || !st.map) { if (mc) mc.render(); return; }
    const img = new Image();
    img.onload = () => { mc.setImage(img, st.map.w, st.map.h); mc.render(); };
    img.onerror = () => { mc.setImage(null, st.map.w, st.map.h); mc.render(); };
    img.src = API.mapImageUrl(floor);
  }

  function showBuildingMetrics(dr, tag) {
    const b = dr.building || {};
    $("rpTag").textContent = tag || "건물값";
    $("rpSei").textContent = fmtVal(b.sei, 1);
    $("rpEpfi").textContent = fmtVal(b.epfi_avg, 1);
    $("rpCbs").textContent = fmtVal(b.cbs_total, 1);
    let zSt = 0, zTot = 0;
    Object.values(b.idr_by_floor || {}).forEach((zs) =>
      (zs || []).forEach((z) => { zTot++; if (z.status === "started") zSt++; }));
    $("rpIdr").textContent = `${zSt}/${zTot}`;
    $("rpBase").innerHTML = (dr.per_floor || []).map((pf) => {
      const r = pf.result || {};
      return `<div class="rpbase-row"><b>${floorName(pf.floor_id)}</b> · SEI ${fmtVal(r.sei,0)} · EPFI ${fmtVal(r.epfi_avg,0)} · CBS ${fmtVal(r.cbs_total,1)}</div>`;
    }).join("");
  }

  function clearMetrics() {
    ["rpSei","rpEpfi","rpCbs","rpIdr"].forEach((id) => { $(id).textContent = "—"; });
    $("rpBase").innerHTML = "";
    $("rpTag").textContent = mode === "drill" ? "건물값" : "현재값";
    $("rpBnTag").textContent = "";
    if (bnPanel) bnPanel.clear("이력을 선택하면 병목별 CBS가 표시됩니다");
  }

  // CBS 병목 선택 집계 (v1.12 — ③ 운영뷰와 동일 패널). 결과의 병목별 CBS·초과초를
  // 집계로, 재생 프레임(fps 격자)의 병목 밀도를 스파크라인으로 쓴다.
  // 드릴 모드에선 현재 재생 중인 층의 병목 기준 — 층을 바꾸면 따라간다.
  function renderRpBn() {
    if (!bnPanel) return;
    const res = (mode === "drill") ? floorResultOf(curDrillFloor) : (data && data.result);
    const bns = (site && site.bottlenecks) || [];
    $("rpBnTag").textContent = (mode === "drill" && curDrillFloor) ? floorName(curDrillFloor) : "";
    if (!res || !bns.length) {
      bnPanel.clear(bns.length ? "재계산 결과 없음" : "이 층에 병목 없음 — 맵 설정에서 추가");
      return;
    }
    const per = {}, resBn = {};
    (res.bottleneck_metrics || []).forEach((m) => { per[m.bottleneck_id] = m.cbs; resBn[m.bottleneck_id] = m; });
    const frames = (data && data.frames) || [];
    const dOf = (f, bid) => {
      const b = (f.bottlenecks || []).find((x) => x.id === bid);
      return (b && b.density != null) ? b.density : null;
    };
    bnPanel.render({ bns, per, resBn,
                     series: (bid) => frames.map((f) => dOf(f, bid)) });
  }

  function setMode(m) {
    if (mode === m) return;
    mode = m;
    $("rpModeSess").classList.toggle("on", m === "sess");
    $("rpModeDrill").classList.toggle("on", m === "drill");
    $("rpFloorWrap").classList.toggle("hidden", m !== "drill");
    $("rpReport").classList.toggle("hidden", m !== "drill");
    pause();
    selId = null; data = null; site = null; drill = null;
    setControlsEnabled(false);
    $("rpReset").disabled = true; $("rpApply").disabled = true; $("rpReport").disabled = true;
    clearMetrics();
    $("rpHint").textContent = m === "drill"
      ? "건물 훈련 이력을 선택하면 전 층 결과를 건물 롤업으로 보여주고, 층을 골라 2D 재생·재계산할 수 있습니다."
      : "좌측에서 경보 세션을 선택하면 그 세션의 이동 기록을 도면 위에 그대로 재생합니다.";
    loadList();
    if (mc) mc.render();
  }

  async function recomputeDrill() {
    if (!selId) return;
    $("rpMsg").textContent = "건물 재계산 중…"; $("rpApply").disabled = true;
    const keepFloor = curDrillFloor, keepIdx = frameIndexAt(cursor);
    try {
      const resp = await API.drillReplay(selId, collectOverrides());
      drill = resp.drill;
      drillFrames = resp.frames_by_floor || {};
      drillSites = resp.site_by_floor || {};
      showBuildingMetrics(drill, "재계산값");
      const floors = drill.floors || [];
      const fl = floors.includes(keepFloor) ? keepFloor : floors[0];
      if (fl) { loadDrillFloor(fl); goTo(Math.min(keepIdx, (data.frames || []).length - 1)); }
      $("rpMsg").textContent = "재계산 완료 — 원본 저장값은 그대로 보존됩니다.";
      if (mc) mc.render();
    } catch (e) {
      $("rpMsg").textContent = "재계산 실패: " + e.message;
    } finally { $("rpApply").disabled = false; }
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
    renderRpBn();
  }

  function showMetricsFromRow(s) {                 // 녹화 없는 세션 — 요약만
    $("rpTag").textContent = "원본 저장값";
    $("rpSei").textContent = fmtVal(s.sei,0) === "—" ? "—" : Math.round(s.sei);
    $("rpEpfi").textContent = s.epfi_avg == null ? "—" : Math.round(s.epfi_avg);
    $("rpCbs").textContent = (s.cbs_total || 0).toFixed(1);
    $("rpIdr").textContent = "—";
    $("rpBase").innerHTML = "";
    $("rpBnTag").textContent = "";
    if (bnPanel) bnPanel.clear("녹화 이전 세션 — 병목별 CBS 없음");
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
    if (mode === "drill") return recomputeDrill();
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
    if (window.CbsBnPanel) bnPanel = CbsBnPanel($("rpBn"));
    $("rpPlay").onclick = togglePlay;
    $("rpToStart").onclick = () => { pause(); goTo(0); if (mc) mc.render(); };
    $("rpSeek").oninput = (e) => { pause(); goTo(parseInt(e.target.value)); if (mc) mc.render(); };
    $("rpSpeed").onchange = (e) => { speed = parseFloat(e.target.value) || 1; };
    $("rpApply").onclick = recompute;
    $("rpReset").onclick = () => { fillThresholds(site && site.thresholds); $("rpMsg").textContent = "원래값으로 되돌림 — [재계산]을 눌러 반영"; };
    $("rpModeSess").onclick = () => setMode("sess");
    $("rpModeDrill").onclick = () => setMode("drill");
    $("rpFloorSel").onchange = (e) => {
      // 재생 중 층 전환: 현재 절대 시각을 유지해 다른 층의 같은 순간으로 자연스럽게 전환.
      // (재생 중이면 그대로 계속 재생, 정지 중이면 그 시점에 멈춰 있음.)
      const absNow = (data && data.frames && data.frames.length)
        ? data.frames[0].ts + cursor : null;
      loadDrillFloor(e.target.value, absNow);
    };
    $("rpReport").onclick = () => {
      if (drill && window.Session && Session.openDrillReport) Session.openDrillReport(drill);
    };
  }

  function enter() {
    init();
    active = true;
    // 다층 사이트는 '건물 훈련'이 기본(실사용 단위). 최초 1회만 자동 설정 —
    // 이후 사용자가 '개별 층'을 고르면 그대로 존중.
    if (!modeAuto) {
      modeAuto = true;
      const multi = App.site && (App.site.floors || []).length >= 2;
      mode = "drill";                      // 리허설·훈련 모두 건물 세션 — 개별 층은 디버그용
      $("rpModeDrill").classList.toggle("on", mode === "drill");
      $("rpModeSess").classList.toggle("on", mode === "sess");
      $("rpFloorWrap").classList.toggle("hidden", mode !== "drill");
      $("rpReport").classList.toggle("hidden", mode !== "drill");
    }
    setCanvasImage();
    loadList();
    if (selId && mode === "sess") {                // 재진입 시 선택 유지(세션 모드)
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
