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
  let liveBns = null;      // 최근 MapState.bottlenecks — 병목별 CBS 진행값 (v1.12)
  let placing = false;     // 경보 위치 클릭 대기 (레거시 — addingOrigin으로 대체)
  let addingOrigin = false;    // + 추가 모드: 맵 클릭 → pendingOrigins에 추가
  let pendingOrigins = [];    // [[x,y], ...] — 세션 시작 전 경보 발생원 목록 (세션 한 회용)
  let pendingInited = false;  // _initPending 1회 수행 여부 — clear 후 재로드 방지
  // 건물 드릴(ADR 06) — 층별 원점을 층을 가로질러 모은다. pendingOrigins는 현재 층 별칭.
  let drillOrigins = {};      // {floor_id: [[x,y],...]} — 참여 층별 이번 드릴용 경보 원점
  let rehearsalExtra = null;      // 리허설로 시작한 드릴의 라벨·시나리오 (드릴 레코드에 저장)
  let drillActive = false;    // 드릴 진행중(전 층 공유 세션)
  let drillReport = null;     // 마지막 드릴 롤업 결과(리포트 재열람용)
  let partCache = null;       // 참여(카메라 매핑) 층 id[] 캐시 — 원점 현황 표시용
  let pollT = null;
  let stoppedId = null;    // stop 직후 지연 SSE 스냅샷 무시용
  let onMapRender = null;  // 맵 canvas 다시 그리기 콜백
  let inited = false;

  // ================================================== 수신·상태
  async function bootstrap() {
    try { live = await API.getSession(); } catch (e) { live = null; }
    try { result = await API.getSessionResult(); } catch (e) { result = null; }
    try { timeline = await API.getSessionTimeline(); } catch (e) { timeline = []; }
    syncPending();  // 현재 층 드릴 원점 별칭 정렬
    // 리로드 대비: 진행중 세션이 저장된 드릴 키와 일치하면 드릴 모드 복원
    try {
      const dk = sessionStorage.getItem("macs_drill");
      drillActive = !!(dk && live && live.session_id === dk);
    } catch (e) { /* sessionStorage 불가 시 무시 */ }
    if (live) { startPoll(); switchPanel("sess"); }
    else if (result) switchPanel("sess");
    if (result) loadSeries();
    updateUI();
    if (drillCapable()) refreshParts();  // 층별 원점 현황 표시(비동기, 완료 시 재렌더)
  }

  /** SSE MapState 수신 시 view_live가 호출. */
  function onState(st) {
    if (st && st.site_version) lastSiteV = st.site_version;
    if (st && st.bottlenecks) liveBns = st.bottlenecks;
    const s = st && st.session;
    if (s && s.session_id !== stoppedId) {
      const wasNew = !live;
      live = s;
      if (wasNew) { result = null; startPoll(); switchPanel("sess"); }
      updateUI();
    } else if (!s && live) {              // 다른 클라이언트가 종료했거나, 영상이 끝나 서버가 자동 종료한 경우
      const endedId = live.session_id;
      live = null;
      stopPoll();
      if (drillActive) {
        // 건물 훈련(리허설 포함)은 롤업 결과 모달까지 띄운다 — 자동 종료라도 ③에서 결과를 본다
        drillActive = false;
        API._j(`/api/drill/${endedId}/result`).then((roll) => {
          drillReport = roll; result = null; updateUI();
          hint("영상 종료 → 건물 훈련 자동 종료 — 롤업 결과가 산출되었습니다.");
          showDrillModal(roll);
        }).catch(() => refreshFinal());
      } else {
        refreshFinal();
      }
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
    if ($("sessRehearsalBtn")) $("sessRehearsalBtn").onclick = onRehearsalBtn;
    $("sessStopBtn").onclick = stop;
    $("alarmAddBtn").onclick = toggleAdding;
    $("alarmClearBtn").onclick = () => {
      drillOrigins[curFloor()] = [];
      syncPending();
      addingOrigin = false;
      renderAlarmPanel();
      hint("");
    };
    $("pmSess").onclick = () => switchPanel("sess");
    $("pmRt").onclick = () => switchPanel("rt");
    $("resClose").onclick = () => { jyZoomHide(); $("resultModal").classList.add("hidden"); };
    $("resPdf").onclick = saveReportPdf;
    $("resultModal").onclick = (e) => {
      if (e.target === $("resultModal")) $("resultModal").classList.add("hidden");
    };
    $("resReopen").onclick = () => {
      if (drillReport) showDrillModal(drillReport);
      else if (result) showResultModal();
    };
  }

  function _initPending() {
    pendingInited = true;  // App.site 미사용 — 운영뷰 패널에서만 관리
  }

  // 건물 드릴 헬퍼 ------------------------------------------------
  const curFloor = () =>
    (typeof App !== "undefined" && App.currentFloor) || "default";

  // pendingOrigins를 현재 층의 drillOrigins 배열로 정렬(별칭). 층 전환 시 그 층 원점 표시.
  function syncPending() {
    const f = curFloor();
    if (!drillOrigins[f]) drillOrigins[f] = [];
    pendingOrigins = drillOrigins[f];
  }

  // 드릴 가능 사이트(층 2개 이상) — 버튼 비활성 게이트는 onBtn에서 전 층 검사.
  const drillCapable = () =>
    typeof App !== "undefined" && App.site &&
    (App.site.floors || []).length >= 2;

  // 참여(카메라 매핑) 층 캐시 갱신 후 패널 재렌더 — 원점 지정 현황 표시용.
  async function refreshParts() {
    try {
      const cams = await API.getCameras();
      partCache = [...new Set(cams.filter((c) => c.mapping && c.enabled)
        .map((c) => c.floor_id || "default"))];
    } catch (e) { partCache = null; }
    renderAlarmPanel();
  }

  function toggleAdding() {
    addingOrigin = !addingOrigin;
    $("alarmAddBtn").classList.toggle("on", addingOrigin);
    hint(addingOrigin ? "맵에서 경보 발생 위치를 클릭하세요 (여러 개 가능). 완료 후 [+ 추가] 재클릭 또는 [🔔 경보 시작]." : "");
    if (onMapRender) onMapRender();
  }

  function renderAlarmPanel() {
    const setup = $("alarmSetup");
    const list = $("alarmOriginList");
    if (!setup || !list) return;
    const isLive = !!live || drillActive;  // 드릴 중엔 어느 층에서든 종료 버튼 노출
    setup.classList.toggle("hidden", isLive);
    $("sessStopBtn").classList.toggle("hidden", !isLive);
    if (isLive) return;

    _initPending();
    syncPending();  // 현재 층 원점 배열로 정렬 (드릴: 층별 수집)
    // 드릴(참여 2+층): 층별 원점 지정 현황 — 전 층 지정돼야 시작 가능(●n=지정, ○=미지정).
    const drillStatus = (partCache && partCache.length >= 2)
      ? `<div class="drill-floorstat" title="참여 각 층에 경보 원점이 지정돼야 건물 훈련을 시작합니다">${
          partCache.map((f) => {
            const n = (drillOrigins[f] || []).length;
            const cur = f === curFloor();
            return `<span class="dfs ${n ? "ok" : "miss"}${cur ? " cur" : ""}">${App.floorName(f)} ${n ? "●" + n : "○"}</span>`;
          }).join("")}</div>`
      : "";
    list.innerHTML = drillStatus + (pendingOrigins.length
      ? pendingOrigins.map((o, i) =>
          `<span class="alarm-chip">경보원 ${i + 1}<button class="alarm-chip-x" data-idx="${i}">×</button></span>`
        ).join("")
      : `<span class="alarm-none">없음 — [+ 추가] 또는 맵설정에서 지정</span>`);

    list.querySelectorAll(".alarm-chip-x").forEach((btn) => {
      btn.onclick = () => {
        pendingOrigins.splice(Number(btn.dataset.idx), 1);
        renderAlarmPanel();
      };
    });

    // 드릴 사이트(층 2+)는 버튼 항상 활성 — 전 층 원점 게이트는 onBtn에서 검사·안내.
    $("sessBtn").disabled = drillCapable() ? false : (pendingOrigins.length === 0);
    if (onMapRender) onMapRender();
  }

  async function _startWithOrigins(origins, tAlarm) {
    addingOrigin = false;
    try {
      // tAlarm 이 있으면(리허설) 영상 t=0 과 경보 시각을 같은 값으로 맞춘다
      live = await API.startSession(null,
        tAlarm ? { origins, t_alarm: tAlarm } : { origins });
      stoppedId = null; result = null; timeline = []; personSeries = null;
      renderDev(); startPoll(); switchPanel("sess");
      hint(`세션 시작 — ${live.session_id} (경보원 ${origins.length}개)`);
    } catch (e) { hint("세션 시작 실패: " + e.message, true); }
    updateUI();
  }

  /** 리허설 대기 중이면 경보와 함께 본영상을 튼다 (ADR 08).
   *
   *  경보 = 송출 시작이다. 매핑까지는 정지화면으로 멈춰 있다가, 이 버튼을 누른
   *  순간 전 채널이 t=0부터 함께 흐른다. 리허설을 안 쓰면 아무 일도 안 한다.
   *  반환값은 경보 시각으로 쓸 t0 (리허설이 아니면 null → 서버가 지금 시각 사용). */
  async function startRehearsalIfStandby() {
    try {
      const st = await (await fetch("/api/vsource/status")).json();
      if (!st || !st.running || st.mode !== "standby") return null;
      // 대기 송출에서 실측한 부착시간을 넘긴다 — 서버가 그 길이만큼 앞머리를 깐다
      const at = (typeof VSource !== "undefined" && VSource.attachSec) || null;
      const r = await fetch("/api/vsource/start", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario_id: st.scenario_id, loop: false,
                               attach_sec: at }),
      });
      if (!r.ok) return null;
      const d = await r.json();
      return { t0: d.t0 || null, floors: d.floors || [],
               alarmAt: d.alarm_at || d.t0 || null, leadSec: d.lead_sec || 0 };
    } catch (e) { return null; }
  }

  /** 평상시 경보 시작 — 리허설과 무관하게 기존 동작 그대로. */
  async function onBtn() { return _startAlarm(null); }

  /** 리허설 훈련 시작 — 영상 t=0 재생과 경보를 같은 시각으로 묶는다.
   *  ③ 에 리허설 대기 중일 때만 나타나는 별도 버튼이 부른다. */
  async function onRehearsalBtn() {
    if (live || drillActive) return;
    let st0 = null;
    try { st0 = await (await fetch("/api/vsource/status")).json(); } catch (e) { /* 아래서 처리 */ }
    if (!st0 || !st0.running || st0.mode !== "standby") {
      hint("리허설이 준비 중이 아닙니다 — ⑤ 리허설에서 [준비]를 먼저 누르세요.", true); return;
    }
    const floors = st0.floors || [];
    rehearsalExtra = { label: `🎬 ${st0.scenario_name || st0.scenario_id}`,
                       rehearsal: { scenario_id: st0.scenario_id, source: st0.source || null } };
    // ① 리허설 층으로 자동 전환 — 상단 층 셀렉터를 손으로 바꾸지 않으면 다른 층 도면을
    //    보고 있게 되고 "아무것도 안 나온다"로 읽힌다(실측).
    if (floors.length && App.currentFloor !== floors[0]
        && (App.floors || []).some((f) => f.id === floors[0])) {
      try { await App.setFloor(floors[0], { discardEdits: true }); } catch (e) { /* 무시 */ }
    }
    // ② 경보 발생원 사전 검사 — **재생을 시작하기 전에**. 시작한 뒤 걸리면 영상 한
    //    사이클(27s)을 허비한다(실측). 규칙은 _startAlarm 과 같다.
    syncPending();                                   // 현재 층 원점을 drillOrigins 에 반영
    {
      const missing = floors.filter((f) => !(drillOrigins[f] && drillOrigins[f].length));
      if (missing.length) {
        hint(`경보 발생원 미지정 층: ${missing.map((f) => App.floorName(f)).join(", ")} — 그 층 도면에서 [+ 추가] 후 클릭 (IDR 기준점)`, true);
        return;
      }
    }
    const r = await startRehearsalIfStandby();
    if (!r) { hint("리허설 시작 실패 — ⑤ 리허설 탭 상태를 확인하세요.", true); return; }
    // 앞머리(정지화면) 구간이 있으면(rtsp 모드) 그게 끝나는 순간 = 본영상 t=0 = 경보 시각.
    // 파일 모드는 lead 0 → 바로.
    if (r.leadSec > 0 && r.alarmAt) {
      await waitForAlarm(r.alarmAt, r.leadSec);
    }
    return _startAlarm(r.alarmAt || r.t0, r.floors);
  }

  /** 앞머리가 끝날 때까지 대기 — 남은 시간과 카메라 복귀 상태를 보여준다. */
  async function waitForAlarm(alarmAt, leadSec) {
    const btn = document.getElementById("sessRehearsalBtn");
    if (btn) btn.disabled = true;
    try {
      while (true) {
        const left = alarmAt - Date.now() / 1000;
        if (left <= 0.15) break;
        let rx = "";
        try {
          const st = await (await fetch("/api/vsource/status")).json();
          if (st && st.cams_total) rx = ` · 카메라 ${st.cams_receiving}/${st.cams_total}`;
        } catch (e) { /* 무시 */ }
        hint(`🎬 훈련 시작까지 ${left.toFixed(0)}초${rx}`
             + ` — 정지화면 ${leadSec.toFixed(0)}초로 카메라 복귀를 덮는 중`);
        await new Promise((z) => setTimeout(z, 1000));
      }
    } finally { if (btn) btn.disabled = false; }
  }

  async function _startAlarm(tAlarm, scopeFloors) {
    if (live || drillActive) return;
    // 참여 층 = 카메라가 매핑된 층(추적이 실제로 일어나는 층). 2개 이상이면 건물 드릴.
    let cams = [];
    try { cams = await API.getCameras(); } catch (e) { /* 폴백: 단일 층 */ }
    // 매핑 + 활성이어야 참여 층 — 비활성은 수신을 안 해 추적이 없다
    // (백엔드 RT.participating_floors 와 같은 규칙).
    let parts = [...new Set(cams.filter((c) => c.mapping && c.enabled)
      .map((c) => c.floor_id || "default"))];
    // 리허설이면 그 시나리오가 다루는 층으로만 좁힌다 — 카메라를 끄지 않고도
    // 무관한 층(예: 17F 실영상)이 훈련 지표에 섞이지 않게 한다.
    if (scopeFloors && scopeFloors.length) {
      parts = parts.filter((f) => scopeFloors.includes(f));
      if (!parts.length) { hint("리허설 시나리오의 층에 매핑된 카메라가 없습니다.", true); return; }
      // 리허설은 층 수와 무관하게 **건물 세션**이다 — 층 1개라도 건물 훈련 이력으로 남겨
      // ④ 리플레이 [건물 훈련]에서 층을 골라 보게 한다(개별 층 목록으로 흩어지지 않게).
      const missing = parts.filter((f) => !(drillOrigins[f] && drillOrigins[f].length));
      if (missing.length) {
        hint(`경보 발생원 미지정 층: ${missing.map((f) => App.floorName(f)).join(", ")} — 도면을 클릭해 [+ 추가]`, true);
        return;
      }
      startDrill(parts, tAlarm, rehearsalExtra || {});
      return;
    }

    if (parts.length >= 2) {
      const missing = parts.filter((f) => !(drillOrigins[f] && drillOrigins[f].length));
      if (missing.length) {
        const names = missing.map((f) => App.floorName(f)).join(", ");
        hint(`경보 발생원 미지정 층: ${names} — 해당 층으로 이동해 경보 위치를 지정하세요.`, true);
        return;
      }
      startDrill(parts, tAlarm);
      return;
    }
    // 단일 층(참여 층 ≤1) — 기존 층별 세션.
    syncPending();
    if (!pendingOrigins.length) { hint("경보 발생원을 먼저 추가하세요.", true); return; }
    _startWithOrigins(pendingOrigins.slice(), tAlarm);
  }

  /** 맵 클릭 훅 (view_live) — addingOrigin 모드면 경보원 추가 후 true 반환. */
  function placeAlarm(p) {
    if (!addingOrigin) return false;
    syncPending();  // 현재 층 배열에 추가 (드릴: 층별 수집)
    pendingOrigins.push([p.x, p.y]);
    renderAlarmPanel();
    hint(`경보원 ${pendingOrigins.length}개 추가됨 — 계속 클릭하거나 [🔔 경보 시작]을 누르세요.`);
    return true;
  }

  // ---- 건물 드릴 시작/종료 (ADR 06) --------------------------------
  async function startDrill(parts, tAlarm, extra) {
    addingOrigin = false;
    const payload = {};
    parts.forEach((f) => { payload[f] = (drillOrigins[f] || []).map((o) => [o[0], o[1]]); });
    try {
      // tAlarm 이 있으면(리허설) 영상 t=0 과 경보 시각을 같은 값으로 맞춘다.
      // floors 범위를 함께 보낸다 — 파킹된 사이트 카메라 층이 참여 층으로 섞이지 않게.
      const resp = await API.drillStart(payload, tAlarm || undefined, { floors: parts, ...(extra || {}) });
      drillActive = true;
      try { sessionStorage.setItem("macs_drill", resp.session_id); } catch (e) { /* noop */ }
      // 현재 층이 참여 층이면 그 층 세션을 live로 표시(폴링). 아니면 live 없음(다른 층에서 진행).
      const mine = resp.floors.find((f) => f.floor_id === curFloor());
      live = mine ? mine.session : null;
      stoppedId = null; result = null; timeline = []; personSeries = null; drillReport = null;
      renderDev(); switchPanel("sess");
      if (live) startPoll();
      hint(`건물 훈련 시작 — ${resp.session_id} · 참여 ${resp.floors.length}개 층`);
    } catch (e) {
      const d = e.detail;
      if (e.status === 409 && d && d.missing_floors) {
        const names = d.missing_floors.map((f) => App.floorName(f)).join(", ");
        hint(`경보 발생원 미지정 층: ${names} — 해당 층으로 이동해 경보 위치를 지정하세요.`, true);
      } else if (e.status === 409 && d && d.busy_floors) {
        hint(`이미 세션 진행 중인 층: ${d.busy_floors.map((f) => App.floorName(f)).join(", ")} — 먼저 종료하세요.`, true);
      } else {
        hint("훈련 시작 실패: " + e.message, true);
      }
    }
    updateUI();
  }

  async function stopDrill() {
    try {
      const roll = await API.drillStop();
      drillActive = false;
      try { sessionStorage.removeItem("macs_drill"); } catch (e) { /* noop */ }
      stoppedId = live && live.session_id;
      live = null; addingOrigin = false;
      stopPoll();
      drillReport = roll;
      hint("건물 훈련 종료 — 롤업 결과가 산출되었습니다.");
      showDrillModal(roll);
    } catch (e) { hint("훈련 종료 실패: " + e.message, true); }
    updateUI();
  }

  async function stop() {
    if (drillActive) return stopDrill();
    try {
      result = await API.stopSession();
      stoppedId = live && live.session_id;
      live = null;
      addingOrigin = false;
      // 직전 세션에서 쓴 경보원 그대로 유지 (사용자가 명시적으로 바꾸지 않는 한 유지)
      const usedOrigins = result &&
        ((result.alarm_origins && result.alarm_origins.length)
          ? result.alarm_origins
          : (result.alarm_origin ? [result.alarm_origin] : []));
      if (usedOrigins && usedOrigins.length) {
        pendingOrigins = usedOrigins.map((o) => [o[0], o[1]]);
        pendingInited = true;
      } else {
        pendingOrigins = [];
        pendingInited = false;
      }
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
    b.textContent = drillCapable() ? "🔔 건물 전체 경보 시작" : "🔔 경보 시작";
    b.classList.remove("stop");
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

  /* 경보 경과시간 — 훈련의 모든 지표가 "경보 후 몇 초"를 기준으로 읽히는데,
   * 작은 회색 글씨로 메타 줄 끝에 붙어 있어 눈에 안 들어왔다. 큰 숫자로 따로 뺀다.
   * 진행 중이면 1초마다 스스로 갱신한다 — SSE(5Hz)를 기다리면 초가 끊겨 보인다. */
  let _elTimer = 0;
  function renderElapsed() {
    const box = $("sessElapsed");
    if (!box) return;
    const e = elapsedSec();
    const has = !!(live || result);
    box.classList.toggle("hidden", !has);
    if (!has) return;
    const running = !!live;
    // 진행 중에는 마지막 갱신 이후 흐른 벽시계를 더해 초가 매끄럽게 흐르게 한다
    const shown = (running && _elBase != null)
      ? _elBase + (performance.now() - _elAt) / 1000 : e;
    const v = Math.max(0, shown == null ? 0 : shown);
    const m = Math.floor(v / 60), sec = Math.floor(v % 60);
    box.classList.toggle("running", running);
    box.innerHTML =
      `<span class="el-lab">경보 후 경과</span>`
      + `<span class="el-big t-num">${m}<i>:</i>${String(sec).padStart(2, "0")}</span>`
      + `<span class="el-sub">${running ? "진행 중" : "종료"}</span>`;
  }
  let _elBase = null, _elAt = 0;
  function noteElapsed() {
    _elBase = elapsedSec(); _elAt = performance.now();
    renderElapsed();
    clearInterval(_elTimer);
    if (live) _elTimer = setInterval(renderElapsed, 250);
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
    noteElapsed();
    document.querySelectorAll("#sessGrid .mela:not(#idrHeadEl)").forEach((el) => {
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
    setGrade("gSei", sei, "sei");

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
    // 발산형 바: 기준점 50pp 이탈 = 절반 꽉 참
    const DELTA_MAX_SCALE = 0.50;

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
      const basis = {};                       // C_j 근거 (결과에 남은 폭·기준)
      if (result) result.exit_metrics.forEach((m) => { basis[m.exit_id] = m; });
      dbox.innerHTML = deltas.map((d, i) => {
        const cj = cCounts[i], ej = aCounts[i];
        // C_j가 어떤 폭·기준에서 나왔는지 한 줄 — "정확한 값이 들어갔나" 확인용
        const bm = basis[exits[i].id] || exits[i];
        const bw = bm.width_m != null ? bm.width_m
                 : (exits[i].width_m != null ? exits[i].width_m : null);
        const bq = bm.q_design != null ? bm.q_design
                 : (exits[i].q_design != null ? exits[i].q_design
                    : ((App.site && App.site.thresholds) || {}).q_design);
        const bmanual = bm.width_manual || (exits[i].width_m != null);
        const why = bw != null
          ? `폭 ${bw.toFixed(2)}m${bmanual ? "(수동)" : ""} × ${bq}인/분/m`
          : "폭 미확정 — 분포 제외";
        // 발산형 바: 중심(50%)에서 좌(under) 또는 우(over)로 뻗음
        const halfPct = Math.min(Math.abs(d) / DELTA_MAX_SCALE * 50, 50).toFixed(1);
        const posStyle = d >= 0
          ? `left:50%;width:${halfPct}%;border-radius:0 3px 3px 0`
          : `right:50%;width:${halfPct}%;border-radius:3px 0 0 3px`;
        const sign = d >= 0 ? "+" : "";
        const cls = Math.abs(d) < 0.005 ? "sei-even" : d > 0 ? "sei-over" : "sei-under";
        const worst = i === worstIdx && Math.abs(d) >= 0.005 ? " sei-worst" : "";
        return `<div class="sei-drow${worst}">
          <div class="sei-dlab">
            <div>${labels[i]}</div>
            <div class="sei-dcnt">${ej}명 / ${cj != null ? cj + '명' : '—'}</div>
            <div class="sei-dwhy" title="C_j = 유효폭 × q_design">${why}</div>
          </div>
          <div class="sei-dbar-wrap sei-diverge">
            <div class="sei-div-center"></div>
            <div class="sei-dbar ${cls}" style="${posStyle}"></div>
          </div>
          <div class="sei-dval ${cls}">${sign}${(d * 100).toFixed(1)}%</div>
        </div>`;
      }).join("");
    });
  }

  function drawSeiGrouped(cv, labels, dShares, aShares) {
    const n = labels.length;
    // 부모 폭·고정 높이 180 으로 비트맵을 잡던 코드였다. 표출 밀도를 바꾸면 CSS 가
    // 캔버스 박스를 바꾸는데(요약 모드는 height:100%) 비트맵은 그대로라, 브라우저가
    // 옛 그림을 늘려 그려 **확대·픽셀 깨짐**이 났다. 스파크·히스토그램이 쓰는
    // cvCtx 로 통일한다 — 자기 박스 × devicePixelRatio 로 잡아 항상 선명하다.
    const g = cvCtx(cv);
    if (!g) return;
    const { ctx, w: W, h: H } = g;

    const PAD_L = 44, PAD_R = 8, PAD_T = 12, PAD_B = 34;
    const plotW = W - PAD_L - PAD_R;
    const plotH = H - PAD_T - PAD_B;
    const slotW = plotW / n;
    const bw = Math.max(8, Math.min(26, slotW * 0.35));
    const bg = 4;

    // 배경
    ctx.fillStyle = "#111722";
    ctx.fillRect(0, 0, W, H);

    // 격자 + y축 레이블
    [0.25, 0.5, 0.75, 1.0].forEach((v) => {
      const y = PAD_T + plotH * (1 - v);
      ctx.strokeStyle = v === 1.0 ? "#2a3a50" : "#1e2b3a";
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - PAD_R, y); ctx.stroke();
      ctx.fillStyle = "#8aaac8"; ctx.font = "12px sans-serif"; ctx.textAlign = "right";
      ctx.fillText((v * 100).toFixed(0) + "%", PAD_L - 5, y + 4);
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
      ctx.font = "11px sans-serif"; ctx.textAlign = "center";
      if (dShares[i] > 0.04) {
        ctx.fillStyle = "#7ac8f0";
        ctx.fillText((dShares[i] * 100).toFixed(0) + "%", bx_d + bw / 2, PAD_T + plotH - h_d - 3);
      }
      if (aShares[i] > 0.04) {
        ctx.fillStyle = "#f0c090";
        ctx.fillText((aShares[i] * 100).toFixed(0) + "%", bx_a + bw / 2, PAD_T + plotH - h_a - 3);
      }

      // x 레이블
      ctx.fillStyle = "#8aaac8"; ctx.font = "12px sans-serif";
      const short = lbl.length > 5 ? lbl.slice(0, 4) + "…" : lbl;
      ctx.fillText(short, cx, H - PAD_B + 16);
    });

    // 축
    ctx.strokeStyle = "#2e4060"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(PAD_L, PAD_T); ctx.lineTo(PAD_L, PAD_T + plotH);
    ctx.lineTo(W - PAD_R, PAD_T + plotH); ctx.stroke();
  }

  // ---------------------------------- CBS 선택 집계 (v1.12)
  // 전체 합계 하나로는 "어느 문·계단이 문제였나"가 묻힌다. 병목을 골라
  // 그 묶음의 합계·평균·최악을 본다. 그룹 라벨(맵 설정)은 빠른 선택용 프리셋.
  // ③ 운영뷰·④ 리플레이 공용 팩토리 — 선택 상태(sel)는 패널 인스턴스별 유지.
  //
  // render(d) 인자:
  //   bns         SiteConfig.bottlenecks (id·name·group·rho_crit)
  //   per         {bn_id: CBS 진행/최종값}
  //   resBn       {bn_id: BottleneckMetric} — 결과 있을 때(위험도·초과초 표시)
  //   tailDensity (bn_id)=>밀도|null — CBS 값이 아직 없을 때 우측 꼬리표 대체
  //   series      (bn_id)=>밀도 시계열|null — 스파크라인 (없으면 "데이터 없음")
  function makeCbsBnPanel(box) {
    let sel = null;            // Set(bottleneck_id) | null = 전체
    let last = null;           // 마지막 render 인자 — 선택 변경 시 재렌더용

    function render(d) {
      last = d;
      const bns = d.bns || [];
      if (!bns.length) { box.innerHTML = `<div class="mnote">병목 없음 — 맵 설정에서 추가</div>`; return; }
      const per = d.per || {};
      const resBn = d.resBn || {};
      const hasRes = Object.keys(resBn).length > 0;
      const groups = [...new Set(bns.map((b) => b.group).filter(Boolean))];
      const chosen = bns.filter((b) => !sel || sel.has(b.id));

      // 선택 집계 — 합계·1개당 평균·최악 병목
      const vals = chosen.map((b) => per[b.id] || 0);
      const sum = vals.reduce((a, v) => a + v, 0);
      const worstI = vals.reduce((mi, v, i) => (v > vals[mi] ? i : mi), 0);
      const overs = chosen.map((b) => (resBn[b.id] ? resBn[b.id].over_threshold_sec : 0));
      const aggr = !chosen.length
        ? `<span class="cbs-agg-none">선택된 병목 없음</span>`
        : `<b class="t-num">합계 ${fmt1(sum)}</b>` +
          `<span>평균 <span class="t-num">${fmt1(sum / chosen.length)}</span></span>` +
          `<span>최악 <span class="t-num">${fmt1(vals[worstI])}</span> ` +
            `(${nameOf(bns, chosen[worstI].id)})</span>` +
          (hasRes ? `<span>초과 <span class="t-num">${fmt1(Math.max(...overs))}</span>s</span>` : "");

      box.innerHTML =
        `<div class="cbs-sel">` +
          `<button class="tag-btn cbs-g${sel ? "" : " on"}" data-g="">전체 ${bns.length}</button>` +
          groups.map((g) => {
            const ids = bns.filter((b) => b.group === g).map((b) => b.id);
            const on = sel && ids.length === sel.size && ids.every((i) => sel.has(i));
            return `<button class="tag-btn cbs-g${on ? " on" : ""}" data-g="${g}">${g} ${ids.length}</button>`;
          }).join("") +
        `</div>` +
        `<div class="cbs-agg">선택 ${chosen.length}/${bns.length} · ${aggr}</div>` +
        bns.map((b) => {
          const m = resBn[b.id];
          const dens = d.tailDensity ? d.tailDensity(b.id) : null;
          const tail = m ? `CBS ${fmt1(m.cbs)} · <span class="risk ${m.risk_level}">${m.risk_level}</span>`
                         : (per[b.id] != null ? `CBS ${fmt1(per[b.id])}`
                            : (dens != null ? `${fmt1(dens)}/m²` : "—"));
          const on = !sel || sel.has(b.id);
          return `<div class="bnrow${on ? "" : " off"}">
            <div class="bnlab">
              <label class="cbs-ck"><input type="checkbox" data-bn="${b.id}"${on ? " checked" : ""}>
                ${nameOf(bns, b.id)}${b.group ? ` <i class="mtag">${b.group}</i>` : ""}</label>
              <span class="bnval t-num">${tail}</span>
            </div>
            <canvas class="bnspark" data-bn="${b.id}"></canvas>
          </div>`;
        }).join("");

      box.querySelectorAll(".cbs-g").forEach((btn) => {
        btn.onclick = () => {
          const g = btn.dataset.g;
          sel = g ? new Set(bns.filter((b) => b.group === g).map((b) => b.id)) : null;
          render(last);
        };
      });
      box.querySelectorAll(".cbs-ck input").forEach((ck) => {
        ck.onchange = () => {
          if (!sel) sel = new Set(bns.map((b) => b.id));
          if (ck.checked) sel.add(ck.dataset.bn); else sel.delete(ck.dataset.bn);
          if (sel.size === bns.length) sel = null;     // 전체면 필터 해제
          render(last);
        };
      });
      box.querySelectorAll(".bnspark").forEach((cv) => {
        const bid = cv.dataset.bn;
        const b = bns.find((x) => x.id === bid);
        spark(cv, (d.series && d.series(bid)) || [],
          { min: 0, threshold: b ? b.rho_crit : null, color: "#FF6F21" });
      });
    }

    return { render,
             clear: (msg) => { last = null; box.innerHTML = msg ? `<div class="mnote">${msg}</div>` : ""; },
             reset: () => { sel = null; } };
  }
  window.CbsBnPanel = makeCbsBnPanel;   // ④ 리플레이 탭도 같은 패널을 쓴다 (v1.12 공용화)

  /** 병목별 CBS 진행값 — 종료 후엔 결과, 진행 중엔 라이브 스냅샷. */
  function cbsPerBn() {
    const out = {};
    if (result) {
      result.bottleneck_metrics.forEach((m) => { out[m.bottleneck_id] = m.cbs; });
    } else {
      (liveBns || []).forEach((b) => {
        if (b.cbs != null) out[b.id] = b.cbs;
      });
    }
    return out;
  }

  let bnPanel = null;           // ③ 운영뷰 인스턴스 (지연 생성)

  function renderCbs() {
    const cbs = live ? live.cbs_total : result.cbs_total;
    $("cbsVal").textContent = fmt1(cbs);
    setGrade("gCbs", cbs, "cbs");
    spark($("cbsSpark"), timeline.map((t) => t.cbs_total), { min: 0, color: "#FF6F21" });

    if (!bnPanel) bnPanel = makeCbsBnPanel($("cbsBn"));
    const resBn = {};
    if (result) result.bottleneck_metrics.forEach((m) => { resBn[m.bottleneck_id] = m; });
    bnPanel.render({
      bns: (App.site && App.site.bottlenecks) || [],
      per: cbsPerBn(),
      resBn,
      tailDensity: (bid) => timeline.length
        ? (timeline[timeline.length - 1].bottleneck_density || {})[bid] : null,
      series: (bid) => timeline.map((t) => (t.bottleneck_density || {})[bid] != null
        ? t.bottleneck_density[bid] : null),
    });
  }

  function renderEpfi() {
    const ep = live ? live.epfi_avg : result.epfi_avg;
    $("epfiVal").textContent = ep != null ? fmt1(ep) : "—";
    setGrade("gEpfi", ep, "epfi");
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
    const g = cvCtx(cv);
    if (!g) return;
    const { ctx, w: W, h: H } = g;      // cvCtx 가 dpr 보정·clear 까지 한다

    const PAD = 4, MID = Math.round(H / 2), BH = Math.round(H * 0.38);
    const AX = PAD, AXR = W - PAD, usable = AXR - AX;
    const scale = usable / maxWindow;

    // 배경 트랙
    ctx.fillStyle = "rgba(255,255,255,.05)";
    ctx.fillRect(AX, MID - BH, usable, BH * 2);

    if (isDetected) {
      const dx = Math.min(AX + delay * scale, AXR - 2);
      // 반응까지 채움 (gradient)
      const grd = ctx.createLinearGradient(AX, 0, dx, 0);
      grd.addColorStop(0, "rgba(63,185,80,.08)");
      grd.addColorStop(1, "rgba(63,185,80,.42)");
      ctx.fillStyle = grd;
      ctx.fillRect(AX, MID - BH, dx - AX, BH * 2);

      // 반응 마커
      ctx.strokeStyle = "#3FB950"; ctx.lineWidth = 1.5; ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(dx + .5, MID - BH - 2); ctx.lineTo(dx + .5, MID + BH + 2); ctx.stroke();

      // Δt 레이블
      ctx.fillStyle = "#3FB950"; ctx.font = "bold 9px monospace"; ctx.textAlign = "left";
      ctx.fillText(`Δt ${delay.toFixed(1)}s`, AX + 3, MID + BH - 1);
    } else {
      // 경과 진행 표시
      const nx = Math.min(AX + elapsed * scale, AXR - 6);
      ctx.fillStyle = "rgba(255,255,255,.04)";
      ctx.fillRect(AX, MID - BH, nx - AX, BH * 2);

      // 이동 커서
      ctx.save(); ctx.setLineDash([2, 3]);
      ctx.strokeStyle = "#464c58"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(nx + .5, MID - BH - 2); ctx.lineTo(nx + .5, MID + BH + 2); ctx.stroke();
      ctx.restore();

      // 경과 시간
      ctx.fillStyle = "#464c58"; ctx.font = "9px monospace"; ctx.textAlign = "right";
      ctx.fillText(`${elapsed.toFixed(0)}s`, AXR - 2, MID + BH - 1);
    }

    // 경보 기점 마커
    ctx.save(); ctx.setLineDash([2, 3]);
    ctx.strokeStyle = "#6a7080"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(AX + .5, MID - BH - 2); ctx.lineTo(AX + .5, MID + BH + 2); ctx.stroke();
    ctx.restore();
  }

  function renderIdr() {
    const zones = (App.site && App.site.zones) || [];
    const wrap = $("idrTblWrap");

    const zm = live
      ? (live.zone_metrics || [])
      : (result ? (result.zone_metrics || []) : []);
    const elapsed = live ? live.elapsed_sec : (result ? (result.ended_at || result.alarm_ts) - result.alarm_ts : 0);

    // 진행 카운터 + 평균 IDR
    const startedZones = zm.filter((z) => z.status === "started");
    const tot = zm.length;
    if (live) {
      $("idrProg").textContent = `${live.zones_started}/${live.zones_total}`;
    } else if (result) {
      $("idrProg").textContent = `${startedZones.length}/${tot}`;
    }
    // IDR 등급은 값(m/s)이 아니라 **개시 구역 비율**로 본다 — m/s 는 경보원~구역
    // 거리에 비례해 도면마다 스케일이 달라, 값으로 등급을 매기면 층끼리 비교가 깨진다.
    setGrade("gIdr", Grade.idrScore(
      live ? live.zones_started : startedZones.length,
      live ? live.zones_total : tot,
      !live), "idr");        // 진행 중이면 잠정(0 개시는 무등급), 종료 결과면 확정
    const validIdrs = startedZones.map((z) => z.idr).filter((v) => v != null);
    const avgIdr = validIdrs.length ? validIdrs.reduce((s, v) => s + v, 0) / validIdrs.length : null;
    const avgEl = $("idrAvgEl");
    if (avgEl) {
      avgEl.innerHTML = avgIdr != null
        ? `${avgIdr.toFixed(2)}<span style="font-size:.55em;font-weight:400;margin-left:4px">m/s</span>`
        : "—";
    }

    if (!zm.length) {
      wrap.innerHTML = `<div class="mnote">구역 없음 — 맵 설정에서 추가</div>`;
      return;
    }

    const maxDelay = zm.reduce((mx, z) => Math.max(mx, z.response_delay_sec || 0), 0);
    const maxWindow = Math.max(elapsed, maxDelay + 5, 20);

    wrap.innerHTML = `<div class="idr-colhd">IDR&nbsp;<small>m/s</small></div>` + zm.map((z) => {
      const name = nameOf(zones, z.zone_id);
      const det = z.status === "started";
      const perO = (z.idr_per_origin || []).filter((v) => v != null);
      const tip = perO.length > 1
        ? `title="경보원별: ${perO.map((v) => v.toFixed(2)).join(" / ")} m/s"` : "";
      const idrTxt = det && z.idr != null ? z.idr.toFixed(2) : "—";
      const dtTxt  = det ? `${fmt1(z.response_delay_sec)}s` : "—";
      return `<div class="idr-row ${det ? "det" : ""}">
        <div class="idr-zg">
          <span class="idr-zn" title="${name}">${name}</span>
          <span class="idr-dt">${dtTxt}</span>
        </div>
        <canvas class="idr-cv" id="idrcv_${z.zone_id}" height="38"></canvas>
        <div class="idr-sc" ${tip}>
          <span class="idr-val ${det && z.idr != null ? "ok" : ""}">${idrTxt}</span>
          <span class="idr-unit">m/s</span>
        </div>
      </div>`;
    }).join("");

    requestAnimationFrame(() => {
      zm.forEach((z) => {
        const cv = document.getElementById(`idrcv_${z.zone_id}`);
        if (!cv) return;
        // 폭만 재고 높이는 HTML 속성(38)에 맡기던 코드 — dpr 미적용이라 HiDPI 에서
        // 흐렸고, 밀도 전환으로 CSS 높이가 바뀌면 비트맵이 늘어났다. cvCtx 로 통일.
        drawIdrTimeline(cv, {
          maxWindow,
          delay: z.response_delay_sec,
          elapsed,
          isDetected: z.status === "started",
        });
      });
    });
  }

  // 5단계 등급 배지 — 운영 뷰 카드 머리. 기준은 grade.js 한 곳(세 화면 공용).
  function setGrade(slotId, v, metric) {
    const el = $(slotId);
    if (!el || !window.Grade) return;
    el.innerHTML = (v == null) ? "" : Grade.pill(v, metric, true);
  }

  /* 표출 밀도(요약·기본·카드) 전환·패널 접기·창 크기 변경 → 캔버스 박스가 바뀐다.
   * ③ 운영 뷰에는 resize 리스너가 **없었다** — PanelView 가 resize 를 쏘는데 듣는
   * 곳이 ④ 리플레이뿐이라, 밀도를 바꾸면 SEI·IDR 그림이 옛 비트맵 그대로 CSS 로
   * 늘어나 확대·픽셀 깨짐이 났다. 한 프레임 뒤(레이아웃 확정 후) 다시 그린다. */
  let _redrawRaf = 0;
  function redrawCanvases() {
    cancelAnimationFrame(_redrawRaf);
    _redrawRaf = requestAnimationFrame(() => {
      if (!$("sessGrid") || $("sessGrid").classList.contains("hidden")) return;
      try { renderSei(); renderCbs(); renderEpfi(); renderIdr(); }
      catch (e) { /* 표본 없는 시점 — 다음 갱신에서 다시 그린다 */ }
    });
  }
  window.addEventListener("resize", redrawCanvases);

  // ================================================== 결과 모달
  // ==================== 결과 리포트 헬퍼 (해석형, v1.13) ====================
  // "지표 이름과 숫자만 있는 결과"는 훈련 담당자가 해석할 수 없다 — 지표마다
  // ①무엇을 재는지 ②이번 값을 어떻게 읽는지 문장을 자동으로 붙인다.
  // 등급은 grade.js(5단계) 한 곳에서 온다 — 운영 뷰·리플레이·리포트가 같은 기준.
  // [라벨, 3색클래스, 5색클래스, 등수] 형태로 돌려준다(총평 테두리가 3색을 쓴다).
  function gradeOf(v, kind) {
    if (v == null || !window.Grade) return null;
    const g = Grade.of(v, kind === "cbs" ? "cbs" : "epfi");
    return [g.label, g.legacy, g.cls, g.rank];
  }
  const pill = (g) => g ? `<span class="gpill ${g[2] || g[1]}">${g[0]}<i>${g[3]}/5</i></span>`
                        : `<span class="gpill g-na">표본부족</span>`;
  const repRow = (k, v) => `<div class="resrow"><span>${k}</span><b class="t-num">${v}</b></div>`;
  const fmtDT = (ts) => ts ? new Date(ts * 1000).toLocaleString("ko-KR", { hour12: false }) : "—";

  // 지표별 "무엇을 재는가" — 보고서 문장(공공기관 보고 어투, ~합니다)으로 쓴다.
  const REP_WHAT = {
    epfi: "재실자가 권장 피난경로를 얼마나 따라 이동하였는지 나타내며, 100에 가까울수록 양호합니다.",
    sei:  "비상구가 설계 기준대로 고르게 이용되었는지 나타내며, 100에 가까울수록 양호합니다.",
    cbs:  "병목(비상구·계단 앞) 구역에서 임계밀도를 초과한 혼잡의 시간 누적값으로, 0에 가까울수록 양호합니다.",
    idr:  "경보 후 각 구역이 피난을 개시하였는지를 개시 구역 비율로 나타내며, 반응 지연(초)을 함께 표기합니다.",
  };

  function repCard(key, name, sub, value, grade, what, read) {
    return `<div class="repcard">
      <div class="repcard-h"><b class="mname m-${key}">${name}</b><span>${sub}</span>${pill(grade)}</div>
      <div class="repcard-v t-num">${value}</div>
      <div class="repcard-what">${what}</div>
      <div class="repcard-read">${read}</div>
    </div>`;
  }

  // ---------------------------------------------------------------- 이름·조사·시간 표기
  // 리포트가 읽을 층별 도면(출구 이름·병목 도형). 리플레이는 응답의 site_by_floor
  // (세션 당시 도면·편집본)를 넘겨 주고, 그 외(라이브 종료·단일 세션)는 현재 사이트
  // 설정(App.site.floors)을 쓴다. 단일 세션은 floor=null → 현재 층 별칭(App.site).
  let REP_SITES = {};
  function repSiteOf(floor) {
    if (floor && REP_SITES[floor]) return REP_SITES[floor];
    if (typeof App === "undefined" || !App.site) return null;
    if (!floor) return App.site;
    return (App.site.floors || []).find((f) => f.id === floor) || null;
  }
  // 출구·병목 이름 — 도면에 name 이 있으면 그것(예: "출구2"), 없으면 id
  function exName(floor, id) {
    const st = repSiteOf(floor);
    const e = st && (st.exits || []).find((x) => x.id === id);
    return (e && e.name) || id;
  }
  function bnName(floor, id) {
    const st = repSiteOf(floor);
    const b = st && (st.bottlenecks || []).find((x) => x.id === id);
    return (b && b.name) || id;
  }
  const flPre = (floor, fname) => (floor && fname ? fname(floor) + " " : "");

  /** 조사 고르기 — pair 는 '을/를' 처럼 (받침 있음/없음). 괄호가 있으면 괄호 앞 글자로,
   *  숫자·영문은 읽는 소리로 판단한다(출구2 → '이' → 를, e1 → '일' → 을). */
  function josa(word, pair) {
    const base = String(word).split("(")[0].trim();
    const ch = base.slice(-1);
    let has = false, rieul = false;
    if (ch >= "가" && ch <= "힣") {
      const j = (ch.charCodeAt(0) - 0xAC00) % 28;
      has = j !== 0; rieul = j === 8;
    } else if (/[0-9]/.test(ch)) {
      has = "013678".includes(ch); rieul = "178".includes(ch);
    } else if (/[lmnLMN]/.test(ch)) {
      has = true; rieul = /[lL]/.test(ch);
    }
    const [a, b] = pair.split("/");
    if (pair === "으로/로" && rieul) return word + "로";
    return word + (has ? a : b);
  }
  // 경보 후 경과 — 분:초 / "1분 20초"
  const mmss = (s) => {
    const t = Math.max(0, Math.round(s));
    return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
  };
  const kdur = (s) => {
    const t = Math.max(0, Math.round(s)), m = Math.floor(t / 60), ss = t % 60;
    return m ? (ss ? `${m}분 ${ss}초` : `${m}분`) : `${ss}초`;
  };
  const RISK_KO = { low: "낮음", mid: "보통", high: "높음" };

  // 해석 문장 — 아래 함수들의 인자는 {m: 지표, floor: 층id|null} 목록 (드릴은 층 표기 포함)
  function readEpfi(avg, persons) {
    if (avg == null) return "경로에 배정된 이동 객체가 없어 표본이 부족하므로 산출하지 않았습니다.";
    const ps = persons.filter((p) => p.epfi != null);
    const low = ps.filter((p) => p.epfi < 60).length;
    const devs = ps.map((p) => p.mean_deviation_m).filter((v) => v != null);
    const md = devs.length ? devs.reduce((a, v) => a + v, 0) / devs.length : null;
    // 표본은 추적 구간(끊긴 트랙 조각 포함) 단위라 '명'이 아니라 비율로 적는다
    let t = `경로 충실도 평균은 <b>${fmt1(avg)}점</b>`
          + (md != null ? `이며, 평균 경로 이탈 거리는 ${fmt1(md)}m입니다.` : "입니다.");
    t += low ? ` 추적 구간의 약 ${Math.round(low / ps.length * 100)}%는 60점 미만으로, 권장 피난경로를 크게 벗어난 이동이 관측되었습니다.`
             : " 대부분의 인원이 권장 피난경로를 따라 이동하였습니다.";
    return t;
  }
  function readSei(sei, exits, fname) {
    if (sei == null) return "비상구를 통과한 인원이 없어 표본이 부족하므로 산출하지 않았습니다.";
    const es = exits.filter((e) => e.m.actual_share != null && e.m.design_share != null);
    if (!es.length) return `<b>${fmt1(sei)}점</b>으로 산출되었습니다.`;
    let w = es[0];
    es.forEach((e) => {
      if (Math.abs(e.m.actual_share - e.m.design_share)
          > Math.abs(w.m.actual_share - w.m.design_share)) w = e;
    });
    const d = (w.m.actual_share - w.m.design_share) * 100;
    const nm = flPre(w.floor, fname) + exName(w.floor, w.m.exit_id);
    if (Math.abs(d) < 5) return "실제 비상구 이용 분포가 설계 분담과 거의 일치하였습니다.";
    const lead = `<b>${nm}</b>의 이용 분담이 설계보다 ${Math.abs(d).toFixed(0)}%p ${d > 0 ? "높았" : "낮았"}`;
    // 10%p 기준은 비상구별 분석(floorReading)의 '고르게 이용' 판정과 같다
    return Math.abs(d) < 10
      ? lead + "으나, 전반적으로 설계 분담과 유사하였습니다."
      : lead + "으며, 안내 및 유도 방식에 대한 검토가 필요합니다.";
  }
  function readCbs(total, bns, fname) {
    if (!bns.length) return "병목 구역이 설정되지 않아 관측 대상이 없었습니다.";
    if ((total || 0) <= 0.01) return "훈련 전 구간에서 모든 병목 구역이 임계밀도 이하로 유지되어 정체 없이 통과하였습니다.";
    let w = bns[0];
    bns.forEach((b) => { if ((b.m.cbs || 0) > (w.m.cbs || 0)) w = b; });
    const nm = flPre(w.floor, fname) + bnName(w.floor, w.m.bottleneck_id);
    return `혼잡이 가장 컸던 지점은 <b>${nm}</b>이며, 임계밀도 초과 ${Math.round(w.m.over_threshold_sec)}초, `
         + `최대 밀도 ${fmt1(w.m.peak_density)}명/㎡(위험도 ${RISK_KO[w.m.risk_level] || w.m.risk_level})로 측정되었습니다.`;
  }
  function readIdr(zones, fname) {
    if (!zones.length) return "구역이 설정되지 않았습니다.";
    const zn = (z) => {
      const st = repSiteOf(z.floor);
      const zz = st && (st.zones || []).find((x) => x.id === z.m.zone_id);
      return flPre(z.floor, fname) + ((zz && zz.name) || z.m.zone_id);
    };
    const st = zones.filter((z) => z.m.status === "started");
    const miss = zones.filter((z) => z.m.status !== "started");
    let t = `${zones.length}개 구역 중 <b>${st.length}개</b> 구역에서 피난 개시가 확인되었습니다.`;
    const dl = st.filter((z) => z.m.response_delay_sec != null);
    if (dl.length) {
      let w = dl[0];
      dl.forEach((z) => { if (z.m.response_delay_sec > w.m.response_delay_sec) w = z; });
      const wn = josa(zn(w), "으로/로"), wb = zn(w);
      t += ` 가장 늦게 개시한 구역은 <b>${wb}</b>${wn.slice(wb.length)} 경보 후 ${Math.round(w.m.response_delay_sec)}초에 개시하였습니다.`;
    }
    if (miss.length) t += ` 미개시 구역(${miss.map(zn).join(", ")})은 판정 조건 미충족 또는 인원 부재로 판단됩니다.`;
    return t;
  }

  /** 종합 요약 배너 — 한 줄로 "이번 훈련이 어땠는가". */
  function repSummary(o) {
    const seg = [];
    seg.push(`경로 준수 <b class="t-num">${o.epfi != null ? fmt1(o.epfi) : "—"}</b> ${pill(gradeOf(o.epfi))}`);
    seg.push(`출구 활용 <b class="t-num">${o.sei != null ? fmt1(o.sei) : "—"}</b> ${pill(gradeOf(o.sei))}`);
    seg.push(`병목 혼잡 <b class="t-num">${fmt1(o.cbs || 0)}</b> ${pill(gradeOf(o.cbs || 0, "cbs"))}`);
    seg.push(`반응 개시 <b class="t-num">${o.zStarted}/${o.zTot}</b> 구역`);
    if (o.totOut != null) seg.push(`출구 통과 <b class="t-num">${o.totOut}</b>명`);
    if (o.globalId) seg.push(`<span class="gpill g-good" title="카메라 간 동일인 연결(글로벌 ID)로 측정된 세션 — 개인 이동 기록 포함">🌐 글로벌 ID</span>`);
    return `<div class="repsum">${seg.join(" · ")}</div>`;
  }

  /** 종합평 — 일반인이 "훈련이 어땠는가"를 한 문단으로 읽게 한다.
   *  등급을 잘된 점/개선 지점 문장으로 풀고 총평 한 문장을 머리에 단다. */
  function repVerdict(o) {
    const pos = [], neg = [];
    const add = (grade, posTxt, midTxt, badTxt) => {
      if (!grade) return;
      if (grade[1] === "g-good") pos.push(posTxt);
      else neg.push(grade[1] === "g-mid" ? midTxt : badTxt);
    };
    add(gradeOf(o.epfi),
        "재실자 대부분이 권장 피난경로를 따라 이동하였습니다.",
        "일부 인원이 권장 피난경로를 벗어나 이동하였습니다.",
        "다수 인원이 권장 피난경로를 벗어나 이동하여 경로 안내에 대한 점검이 필요합니다.");
    add(gradeOf(o.sei),
        "비상구가 설계 기준대로 고르게 이용되었습니다.",
        "일부 비상구로 이용이 편중되어 유도 안내에 대한 점검이 필요합니다.",
        "특정 비상구로 이용이 크게 편중되어 유도 안내 개선이 필요합니다.");
    add(gradeOf(o.cbs || 0, "cbs"),
        "병목 구역을 정체 없이 원활하게 통과하였습니다.",
        "병목 구역에서 경미한 정체가 발생하였습니다.",
        "병목 구역에서 심한 정체가 발생하여 해당 지점에 대한 점검이 필요합니다.");
    if (o.zTot) {
      if (o.zStarted === o.zTot) pos.push("모든 구역에서 피난 개시가 확인되었습니다.");
      else neg.push(`${o.zTot - o.zStarted}개 구역에서는 피난 개시가 확인되지 않았습니다.`);
    }
    const head = !neg.length
      ? ["이번 훈련은 전반적으로 <b>우수</b>하게 수행되었습니다.", "g-good"]
      : neg.length <= pos.length
        ? ["이번 훈련은 전반적으로 <b>양호</b>하였으나, 일부 개선이 필요한 사항이 확인되었습니다.", "g-mid"]
        : ["이번 훈련은 <b>개선이 필요</b>한 것으로 평가되었습니다.", "g-bad"];
    return `<div class="repverdict ${head[1]}">
      <div class="repverdict-head">${head[0]}</div>
      ${pos.map((t) => `<div class="repverdict-line good">${t}</div>`).join("")}
      ${neg.map((t) => `<div class="repverdict-line warn">${t}</div>`).join("")}
    </div>`;
  }

  /** 4대 지표 카드(설명+해석) 묶음. */
  function repCards(o) {
    const fn = o.fname || ((f) => f);
    const started = o.zones.filter((z) => z.m.status === "started").length;
    // IDR 도 다른 셋과 같은 5단계로 — 값(m/s)이 아니라 **개시 구역 비율**이 기준이다
    // (m/s 는 경보원~구역 거리에 비례해 도면마다 스케일이 달라 등급을 못 매긴다).
    // 리포트는 종료된 결과라 final=true — 끝까지 0 개시면 불량으로 확정한다.
    const idrG = gradeOf(Grade.idrScore(started, o.zones.length, true));
    return `<div class="repgrid">`
      + repCard("epfi", "EPFI", "경로 충실도 (0~100)", o.epfi != null ? fmt1(o.epfi) : "—",
                gradeOf(o.epfi), REP_WHAT.epfi, readEpfi(o.epfi, o.persons))
      + repCard("sei", "SEI", "출구 활용 효율 (0~100)", o.sei != null ? fmt1(o.sei) : "—",
                gradeOf(o.sei), REP_WHAT.sei, readSei(o.sei, o.exits, fn))
      + repCard("cbs", "CBS", "병목 혼잡 누적 (0=최선)", fmt1(o.cbs || 0),
                gradeOf(o.cbs || 0, "cbs"), REP_WHAT.cbs, readCbs(o.cbs, o.bns, fn))
      + repCard("idr", "IDR", "구역 반응 개시", `${started}/${o.zones.length}`,
                idrG, REP_WHAT.idr, readIdr(o.zones, fn))
      + `</div>`;
  }

  /** 4대 지표 등급 기준표 — 경계값은 grade.js 상수에서 만든다(하드코딩 금지).
   *  기준이 바뀌면 이 표도 함께 바뀐다. */
  const GRADE_MEAN = {
    score: ["기준 충분히 충족", "대체로 충족", "일부 개선 필요", "개선 필요", "전반적 개선 필요"],
    cbs:   ["혼잡 거의 없음", "일시적 경미한 혼잡", "단시간 혼잡", "지속적 혼잡", "장시간 심한 정체"],
  };
  function gradeLegend() {
    if (!window.Grade) return "";
    const S = Grade.SCORE_CUTS, C = Grade.CBS_CUTS;
    // 점수형: 하한 경계(위에서부터) — "90 이상", "75~90", …, "40 미만"
    const sR = [`${S[0]} 이상`].concat(S.slice(1).map((c, i) => `${c}~${S[i]}`), [`${S[S.length - 1]} 미만`]);
    // CBS: 상한 경계 — "0.5 이하", "0.5~3", …, "30 초과"
    const cR = [`${C[0]} 이하`].concat(C.slice(1).map((c, i) => `${C[i]}~${c}`), [`${C[C.length - 1]} 초과`]);
    const n = Grade.SCORE_LABELS.length;
    const pills = (labs) => labs.map((l, i) => `<td><span class="gpill g${i + 1}">${l}</span></td>`).join("");
    const cells = (arr) => arr.map((t) => `<td>${t}</td>`).join("");
    return `<div class="repsec-h">등급 기준</div>
      <table class="drill-tbl replg">
        <thead><tr><th>구분</th>${Array.from({ length: n }, (_, i) => `<th>${i + 1}단계</th>`).join("")}</tr></thead>
        <tbody>
          <tr class="lg-h"><td>SEI · EPFI · IDR</td>${pills(Grade.SCORE_LABELS)}</tr>
          <tr><td>점수 구간</td>${cells(sR)}</tr>
          <tr><td>의미</td>${cells(GRADE_MEAN.score)}</tr>
          <tr class="lg-h"><td>CBS</td>${pills(Grade.CBS_LABELS)}</tr>
          <tr><td>누적 구간</td>${cells(cR)}</tr>
          <tr><td>의미</td>${cells(GRADE_MEAN.cbs)}</tr>
        </tbody></table>
      <div class="repnote">※ 등급 구간은 참고 기준이며, 고객 확정 시 조정될 수 있습니다.
        SEI·EPFI는 0~100으로 높을수록, CBS는 0에 가까울수록 양호합니다.
        IDR은 개시 구역 비율(개시 구역 수 ÷ 전체 구역 수 × 100)로 등급을 매기며, 반응 지연(초)을 함께 표기합니다.</div>`;
  }

  /** 출구별 실제(막대) vs 설계(주황 눈금) 분포 — SEI의 근거 시각화. */
  function exitBars(exits, fname) {
    const es = exits.filter((e) => e.m.actual_share != null);
    if (!es.length) return "";
    return `<div class="repsec-h">출구별 이용 분포 (막대: 실제, <span style="color:#F5A623">▏주황 눈금</span>: 설계 기대)</div>`
      + es.map((e) => {
          const a = e.m.actual_share * 100, d = (e.m.design_share || 0) * 100;
          const nm = flPre(e.floor, fname) + exName(e.floor, e.m.exit_id);
          return `<div class="ebar"><span>${nm}</span>
            <div class="ebar-t"><i class="a" style="width:${a.toFixed(1)}%"></i><i class="d" style="left:${d.toFixed(1)}%"></i></div>
            <span class="ebar-v">${e.m.actual_count}명 · ${a.toFixed(0)}% (설계 ${d.toFixed(0)}%)</span></div>`;
        }).join("");
  }

  // ---------------------------------------------------------------- 비상구별 분석
  // 이상적인 피난은 출구가 설계대로 고르게 쓰이고(SEI→100) 출구 앞 혼잡이 없는(CBS→0) 것이다.
  // 그래서 CBS 를 층 합계로만 보지 않고 **출구별로**(출구 앞 병목을 그 출구에 묶어) 보고,
  // 쏠린 출구와 혼잡한 출구가 같은지 다른지로 원인을 읽는다.
  // 문구·판정은 발표용 오프라인 보고서(tools/cj1022_eval_report.py floor_reading)와 같다.

  /** 한 층(또는 단일 세션)의 출구별 분석 행.
   *  r = EvaluationResult, tl = TimelinePoint[](없으면 시간대 열 생략), t0 = 경보 시각.
   *  출구 앞 CBS = 병목 도형 중심(shape.center 또는 polygon 평균)이 가장 가까운
   *  출구(출구 선 중점 기준)에 그 병목을 묶어 합산. 도형이 없으면 CBS 열 생략. */
  function exitAnalysis(r, floor, tl, t0) {
    const ems = (r && r.exit_metrics) || [];
    if (!ems.length) return null;
    // 출구별 통과 시각(경보 후 초) — 누적 카운트 타임라인의 증가분
    const passes = {}, prev = {};
    let hasTl = false;
    if (t0 != null) {
      (tl || []).forEach((p) => {
        Object.entries(p.exit_counts || {}).forEach(([k, v]) => {
          const d = v - (prev[k] || 0);
          if (d > 0) {
            hasTl = true;
            const arr = passes[k] || (passes[k] = []);
            for (let i = 0; i < d; i++) arr.push(p.ts - t0);
          }
          prev[k] = v;
        });
      });
    }
    // 병목 → 가장 가까운 출구
    const st = repSiteOf(floor);
    const mid = {};
    ((st && st.exits) || []).forEach((e) => {
      if (e.line && e.line.length >= 2)
        mid[e.id] = [(e.line[0][0] + e.line[1][0]) / 2, (e.line[0][1] + e.line[1][1]) / 2];
    });
    const mids = Object.keys(mid);
    const bms = {};
    (r.bottleneck_metrics || []).forEach((b) => { bms[b.bottleneck_id] = b; });
    const bnExit = {};
    if (mids.length) {
      ((st && st.bottlenecks) || []).forEach((b) => {
        if (!bms[b.id]) return;
        const pg = b.polygon || [];
        const c = (b.shape && b.shape.center)
          || (pg.length ? [pg.reduce((a, q) => a + q[0], 0) / pg.length,
                           pg.reduce((a, q) => a + q[1], 0) / pg.length] : null);
        if (!c) return;
        let best = null, bd = Infinity;
        mids.forEach((k) => {
          const dd = Math.hypot(c[0] - mid[k][0], c[1] - mid[k][1]);
          if (dd < bd) { bd = dd; best = k; }
        });
        bnExit[b.id] = best;
      });
    }
    const hasCbs = Object.keys(bnExit).length > 0;
    const rows = ems.map((em) => {
      const ts = (passes[em.exit_id] || []).slice().sort((a, b) => a - b);
      const n = ts.length;
      let peak = 0;
      for (let i = 0, j = 0; i < n; i++) {          // 최대 분당 통과 — 60초 창
        while (j < n && ts[j] < ts[i] + 60) j++;
        peak = Math.max(peak, j - i);
      }
      const bids = Object.keys(bnExit).filter((b) => bnExit[b] === em.exit_id);
      return {
        id: em.exit_id, name: exName(floor, em.exit_id),
        count: em.actual_count || 0, share: em.actual_share, design: em.design_share,
        first: n ? ts[0] : null, last: n ? ts[n - 1] : null,
        p10: n ? ts[Math.floor(n * 0.1)] : null, p90: n ? ts[Math.min(n - 1, Math.floor(n * 0.9))] : null,
        peakMin: peak,
        cbs: hasCbs ? bids.reduce((a, b) => a + (bms[b].cbs || 0), 0) : null,
        overSec: bids.length ? bids.reduce((a, b) => a + (bms[b].over_threshold_sec || 0), 0) : null,
        peakDens: bids.length ? Math.max(...bids.map((b) => bms[b].peak_density || 0)) : null,
      };
    });
    return { floor, rows, hasTl, hasCbs, sei: r.sei, cbs: r.cbs_total };
  }

  /** 층 하나의 비상구별 해석 → [관찰 문장[], 개선 제안[]]. 공공기관 보고 어투.
   *  근거 없는 합성 점수로 순위를 매기지 않는다 — 측정값(분담·CBS·통과 시간대)만 쓴다. */
  function floorReading(fa, prefix) {
    prefix = prefix || "";
    const obs = [], rec = [];
    const tot = fa.rows.reduce((a, e) => a + e.count, 0);
    const es = fa.rows.filter((e) => e.share != null && e.design != null);
    if (!tot || !es.length) return [["비상구 통과 인원이 확인되지 않았습니다."], []];
    const dv = (e) => e.share - e.design;
    const over = es.reduce((w, e) => (dv(e) > dv(w) ? e : w));
    const under = es.reduce((w, e) => (dv(e) < dv(w) ? e : w));
    const dev = dv(over) * 100;
    const seiTag = fa.sei != null ? `(SEI ${fa.sei.toFixed(0)}, ${Grade.of(fa.sei, "sei").label})` : "";
    const C = Grade.CBS_CUTS;
    if (es.length < 2) {
      obs.push(`${josa(over.name, "을/를")} 통해 ${over.count}명이 피난하였습니다.`);
    } else if (dev < 10) {
      obs.push(`비상구별 이용 분담이 설계 기준과 유사하여 비상구가 고르게 이용되었습니다${seiTag}.`);
    } else {
      obs.push(`피난 인원의 ${(over.share * 100).toFixed(0)}%가 ${josa(over.name, "을/를")} 이용하여 `
        + `설계 분담(${(over.design * 100).toFixed(0)}%)보다 ${dev.toFixed(0)}%p 높았으며, `
        + `${josa(under.name, "은/는")} ${(under.share * 100).toFixed(0)}%를 처리하였습니다${seiTag}.`);
    }
    const skew = es.length >= 2 && dev >= 10;
    if (fa.hasCbs) {
      const worst = fa.rows.reduce((w, e) => ((e.cbs || 0) > (w.cbs || 0) ? e : w));
      if ((worst.cbs || 0) > C[0]) {
        const tail = `(CBS ${worst.cbs.toFixed(1)}, ${Grade.of(worst.cbs, "cbs").label}, `
                   + `임계밀도 초과 ${Math.round(worst.overSec || 0)}초)`;
        if (worst === over && skew) {
          obs.push(`혼잡 또한 이용이 편중된 ${worst.name} 앞에서 발생하였습니다${tail}. `
            + "특정 비상구로의 편중이 혼잡으로 이어진 것으로 판단됩니다.");
          rec.push(`(개선) ${prefix}안내방송 및 유도원 배치를 통해 ${under.name} 이용을 유도하면 `
            + `${worst.name} 앞 혼잡을 줄일 수 있을 것으로 판단됩니다.`);
        } else {
          const few = worst.share != null && worst.design != null && worst.share < worst.design;
          obs.push(`혼잡은 ${few ? "이용 인원이 상대적으로 적은 " : ""}${worst.name} 앞에서 발생하였습니다${tail}.`);
          if (worst.p10 != null)
            obs.push(`이는 ${worst.name} 이용 인원의 80%가 ${kdur(worst.p90 - worst.p10)} 동안 `
              + `집중적으로 통과(최대 분당 ${worst.peakMin}명)하였기 때문으로 판단됩니다.`);
          rec.push(`(개선) ${prefix}시차 피난 또는 유도원 배치를 통해 ${worst.name} 진입 시점을 분산할 필요가 있습니다.`);
        }
        const clean = fa.rows.filter((e) => (e.cbs || 0) <= C[0] && e.count);
        if (clean.length && worst.cbs > C[1]) {
          const c = clean[0];
          obs.push(`${josa(c.name, "은/는")} ${c.count}명이 통과하는 동안 혼잡이 발생하지 않아 수용 여력이 있었습니다.`);
        }
      } else {
        obs.push("비상구 앞에서 임계밀도를 초과하는 혼잡은 거의 발생하지 않았습니다.");
        if (skew)
          rec.push(`(개선) ${prefix}안내방송 및 유도원 배치를 통해 ${under.name} 이용을 유도할 필요가 있습니다.`);
      }
    } else if (skew) {
      rec.push(`(개선) ${prefix}안내방송 및 유도원 배치를 통해 ${under.name} 이용을 유도할 필요가 있습니다.`);
    }
    obs.push(`평가 구간 동안 총 ${tot}명이 비상구를 통해 피난하였습니다.`);
    return [obs, rec];
  }

  /** 비상구별 분석 블록(층 하나) — 머리줄 + 표 + 해석. title 은 층 이름(단일 세션은 null). */
  function exitSection(fa, title) {
    if (!fa) return "";
    const tot = fa.rows.reduce((a, e) => a + e.count, 0);
    const pct = (v) => (v != null ? (v * 100).toFixed(0) + "%" : "—");
    const head = `<div class="exsec-h"><b>${title ? title + " · " : ""}비상구 ${fa.rows.length}개소 · ${tot}명</b>`
      + `<span>SEI ${fa.sei != null ? fmt1(fa.sei) : "—"} ${pill(gradeOf(fa.sei))}`
      + ` · CBS ${fmt1(fa.cbs || 0)} ${pill(gradeOf(fa.cbs || 0, "cbs"))}</span></div>`;
    const th = `<th>비상구</th><th>인원</th><th>실제 / 설계 분담</th><th>분담 편차</th>`
      + (fa.hasTl ? `<th>80% 통과 시간대</th><th>최대 명/분</th>` : "")
      + (fa.hasCbs ? `<th>비상구 앞 CBS</th><th>임계 초과</th><th>최대 밀도 명/㎡</th>` : "");
    const trs = fa.rows.map((e) => {
      const dv = (e.share != null && e.design != null) ? (e.share - e.design) * 100 : null;
      const dvc = dv == null ? "" : (dv >= 10 ? "dv-up" : (dv <= -10 ? "dv-dn" : ""));
      return `<tr><td>${e.name}</td><td class="t-num"><b>${e.count}명</b></td>`
        + `<td class="t-num">${pct(e.share)} / ${pct(e.design)}</td>`
        + `<td class="t-num ${dvc}">${dv == null ? "—" : (dv >= 0 ? "+" : "") + dv.toFixed(0) + "%p"}</td>`
        + (fa.hasTl ? `<td class="t-num">${e.p10 != null ? mmss(e.p10) + "~" + mmss(e.p90) : "—"}</td>`
                    + `<td class="t-num">${e.first != null ? e.peakMin : "—"}</td>` : "")
        + (fa.hasCbs ? `<td class="t-num"><b>${fmt1(e.cbs || 0)}</b> ${pill(gradeOf(e.cbs || 0, "cbs"))}</td>`
                     + `<td class="t-num">${e.overSec != null ? Math.round(e.overSec) + "초" : "—"}</td>`
                     + `<td class="t-num">${e.peakDens != null ? e.peakDens.toFixed(2) : "—"}</td>` : "")
        + `</tr>`;
    }).join("");
    const [obs, rec] = floorReading(fa);
    return `<div class="exsec">${head}
      <table class="drill-tbl extbl"><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table>
      <ul class="repread">${obs.map((t) => `<li>${t}</li>`).join("")}${rec.map((t) => `<li class="rec">${t}</li>`).join("")}</ul>
    </div>`;
  }
  /** 비상구별 분석 아래 용어 설명 — 실제로 나온 열만 설명한다. fas = exitAnalysis 결과[] */
  function exitNote(fas) {
    const cbs = fas.some((f) => f && f.hasCbs), tl = fas.some((f) => f && f.hasTl);
    return `<div class="repnote">`
      + (cbs ? "비상구 앞 CBS: 비상구 앞 병목 구역(도형 중심이 가장 가까운 비상구에 귀속)에서 "
             + "임계밀도를 초과한 혼잡의 시간 누적값으로, 0에 가까울수록 양호합니다. " : "")
      + "분담 편차: 실제 분담 − 설계 분담."
      + (tl ? " 80% 통과 시간대: 해당 비상구 이용 인원의 10%~90%가 통과한 시간(경보 후 분:초)." : "")
      + `</div>`;
  }

  // ---------------------------------------------------------------- 피난 타임라인
  const TL_COLS = ["#30DCFB", "#FF9F43", "#A78BFA", "#3FB950", "#FF6F9C", "#F5D90A"];
  /** 층별 대피 개시 → 출구별 통과 시간대(가는 선 = 최초~최종, 굵은 막대 = 80% 통과 구간).
   *  items = [{label, start(경보 후 초|null), fa}]. 통과 기록이 하나도 없으면 "" (섹션 생략).
   *  SVG(viewBox 고정 폭)라 창 크기·인쇄에 맞춰 같이 늘고 준다. 색은 main.css .tlsvg 토큰. */
  function repTimeline(items) {
    const its = (items || []).filter((it) => it.fa && it.fa.hasTl);
    if (!its.length) return "";
    let T = 0;
    its.forEach((it) => {
      it.fa.rows.forEach((e) => { if (e.last != null) T = Math.max(T, e.last); });
      if (it.start != null) T = Math.max(T, it.start);
    });
    const step = [30, 60, 120, 300, 600].find((s) => (T + 20) / s <= 12) || 900;
    const TA = Math.max(step, Math.ceil((T + 20) / step) * step);
    // 오른쪽에 "인원 · 최초~최종" 글자 칸을 따로 둔다 — 막대 옆에 붙이면 왼쪽 끝 막대가
    // 출구 이름과 겹친다.
    const W = 1000, LW = 110, RW = 170;
    const X = (s) => LW + (W - LW - RW) * s / TA;
    let y = 22;
    const g = [];
    its.forEach((it) => {
      const ends = it.fa.rows.map((e) => e.last).filter((v) => v != null);
      const end = ends.length ? Math.max(...ends) : null;
      g.push(`<text class="tl-fl" x="0" y="${y + 12}">${it.label}</text>`);
      if (end != null)
        g.push(`<rect class="tl-base" x="${X(0)}" y="${y + 6}" width="${X(end) - X(0)}" height="5" rx="2.5"/>`);
      if (it.start != null) {
        g.push(`<circle class="tl-start" cx="${X(it.start)}" cy="${y + 8.5}" r="6"/>`);
        const sx = X(it.start), right = sx > W - RW - 110;
        g.push(`<text class="tl-start-t" x="${right ? sx - 10 : sx + 10}" y="${y + 4}"`
          + `${right ? ' text-anchor="end"' : ""}>대피 개시 ${mmss(it.start)}</text>`);
      }
      y += 26;
      it.fa.rows.forEach((e, k) => {
        const col = TL_COLS[k % TL_COLS.length];
        g.push(`<text class="tl-ex" x="14" y="${y + 11}">${e.name}</text>`);
        if (e.first == null) {
          g.push(`<text class="tl-lab" x="${W}" y="${y + 11}" text-anchor="end">통과 없음</text>`);
        } else {
          g.push(`<rect x="${X(e.first)}" y="${y + 6}" width="${Math.max(2, X(e.last) - X(e.first))}" height="2" fill="${col}" opacity=".55"/>`);
          g.push(`<rect x="${X(e.p10)}" y="${y + 1}" width="${Math.max(4, X(e.p90) - X(e.p10))}" height="12" rx="3" fill="${col}"/>`);
          const txt = `${e.count}명 · ${mmss(e.first)}~${mmss(e.last)}`;
          g.push(`<text class="tl-lab" x="${W}" y="${y + 11}" text-anchor="end">${txt}</text>`);
        }
        y += 22;
      });
      y += 14;
    });
    const H = y + 18;
    const ticks = [];
    for (let t = 0; t <= TA; t += step) {
      ticks.push(`<line class="tl-grid" x1="${X(t)}" y1="0" x2="${X(t)}" y2="${H - 20}"/>`
        + `<text class="tl-tick" x="${X(t)}" y="${H - 5}" text-anchor="middle">${mmss(t)}</text>`);
    }
    return `<div class="repsec-h">피난 타임라인 (경보 후 경과 시간, 분:초)</div>
      <div class="tl-legend">굵은 막대: 이용 인원의 80%가 통과한 시간대 · 가는 선: 최초~최종 통과 시간
        <span class="tl-leg-start">● 대피 개시</span></div>
      <svg class="tlsvg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMin meet">${ticks.join("")}${g.join("")}</svg>`;
  }

  /** 층 대피 개시(경보 후 초) — 개시한 구역 중 가장 빠른 반응 지연. 없으면 fallback. */
  function floorStartSec(r, fallback) {
    const ds = ((r && r.zone_metrics) || [])
      .filter((z) => z.status === "started" && z.response_delay_sec != null)
      .map((z) => z.response_delay_sec);
    return ds.length ? Math.min(...ds) : (fallback != null ? fallback : null);
  }

  /** 개인 이동 기록(여정) 표 — 🌐 글로벌 ID 모드 전용. items = [{j: PersonJourney, floor}] */
  function journeysTable(items, fname) {
    const fn = fname || ((f) => f);
    const rows = (items || []).filter((it) => it && it.j);
    if (!rows.length) return "";
    rows.sort((a, b) => (a.j.exit_ts == null) - (b.j.exit_ts == null) || a.j.start_ts - b.j.start_ts);
    const body = rows.map(({ j, floor }) => {
      const cams = (j.segments || []).map((s) => s.cam_id.replace(/^rh_/, "")).join("→");
      const cov = Math.round((j.coverage_ratio || 0) * 100);
      return `<tr>
        <td><b>${j.gid}</b>${floor ? ` <i class="mtag">${fn(floor)}</i>` : ""}</td>
        <td>${hhmmss(j.start_ts)}${j.start_zone ? ` (${j.start_zone})` : ""}</td>
        <td>${j.exit_id ? `${exName(floor, j.exit_id)} · ${hhmmss(j.exit_ts)}` : "—"}</td>
        <td class="t-num">${j.total_dist_m != null ? fmt1(j.total_dist_m) + "m" : "—"}</td>
        <td class="t-num">${fmt1(j.duration_sec)}s</td>
        <td class="t-num">${j.avg_speed_mps != null ? fmt1(j.avg_speed_mps) : "—"}</td>
        <td class="t-num" title="관측 시간 비율 — 낮으면 중간에 id 연결이 끊겼다는 뜻">${cov}%</td>
        <td class="jt-cams">${cams}</td>
      </tr>`;
    }).join("");
    return `<div class="repsec-h">개인 이동 기록 (🌐 글로벌 ID 기준 ${rows.length}명)
        <span class="mtag" title="같은 사람이 카메라를 옮겨도 하나의 id 로 이어붙인 기록. 출구 통과 = 그 id 의 최초 퇴장">여정</span></div>
      <div class="jt-wrap"><table class="drill-tbl jt">
        <thead><tr><th>ID</th><th>시작 (구역)</th><th>출구 통과</th><th>이동거리</th><th>소요</th><th>평균 m/s</th><th>커버리지</th><th>경유 카메라</th></tr></thead>
        <tbody>${body}</tbody></table></div>`;
  }

  function showResultModal() {
    if (!result) return;
    const r = result;
    REP_SITES = {};                       // 단일 세션 — 현재 층 도면(App.site)
    const dur = r.ended_at ? (r.ended_at - r.alarm_ts) : 0;
    const zs = (r.zone_metrics || []).map((m) => ({ m, floor: null }));
    const es = (r.exit_metrics || []).map((m) => ({ m, floor: null }));
    const bs = (r.bottleneck_metrics || []).map((m) => ({ m, floor: null }));
    const started = zs.filter((z) => z.m.status === "started").length;
    const totOut = es.reduce((s, e) => s + (e.m.actual_count || 0), 0);
    const aos = (r.alarm_origins && r.alarm_origins.length) ? r.alarm_origins : [r.alarm_origin];
    // 타임라인은 이 결과의 세션 것만 쓴다(경보~종료 구간 밖이면 다른 세션 것 — 생략)
    const tEnd = r.ended_at || Infinity;
    const tl = (timeline || []).filter((p) => p.ts >= r.alarm_ts - 1 && p.ts <= tEnd + 5);
    const fa = exitAnalysis(r, null, tl, r.alarm_ts);
    $("resTitle").textContent = `평가 세션 결과 — ${r.session_id}`;
    const ov = { sei: r.sei, epfi: r.epfi_avg, cbs: r.cbs_total,
                 zStarted: started, zTot: zs.length, totOut,
                 globalId: !!r.global_id };
    $("resBody").innerHTML =
      repSummary(ov)
      + repVerdict(ov)
      + `<div class="repsec-h">개요</div>`
      + `<div class="reprows">`
        + repRow("경보 시각", fmtDT(r.alarm_ts))
        + repRow("평가 시간", `${Math.floor(dur / 60)}분 ${Math.floor(dur % 60)}초 (종료 ${r.ended_at ? hhmmss(r.ended_at) : "—"})`)
        + repRow("경보 발생원", aos.map((o, i) => `#${i + 1}(${Math.round(o[0])},${Math.round(o[1])})`).join(" · "))
        + repRow("출구 총 통과", `${totOut}명 · 출구 ${es.length}곳`)
        + repRow("설정 버전", `calibration v${r.calibration_version} · config v${r.config_version}`)
      + `</div>`
      + `<div class="repsec-h">4대 지표 해석</div>`
      + repCards({ sei: r.sei, epfi: r.epfi_avg, cbs: r.cbs_total,
                   exits: es, persons: r.person_metrics || [], zones: zs, bns: bs })
      + gradeLegend()
      + exitBars(es)
      + (fa ? `<div class="repsec-h">비상구별 분석</div>` + exitSection(fa, null) + exitNote([fa]) : "")
      + repTimeline(fa ? [{ label: "평가 층", start: floorStartSec(r), fa }] : [])
      + journeysTable((r.journeys || []).map((j) => ({ j, floor: null })));
    $("resultModal").classList.remove("hidden");
  }

  /** 건물 드릴 롤업 리포트 (ADR 06 §3) — 종합 요약 + 지표 해석 카드 + 층별 상세. */
  /** 여정 재구성 섹션 — 리포트 전용(사이드바는 좁아 그림이 안 들어간다).
   *  왜 이렇게 묶였는지를 보여준다:
   *   ① 산점도  임베딩 2D 투영(PCA→t-SNE), 사람별 색 — 덩어리가 갈렸는지
   *   ② 유사도 행렬  클러스터 순 정렬 — **대각 블록**이 뚜렷하면 잘 묶인 것이고,
   *      블록 밖 밝은 점은 "합쳤어야 했는데 못 합친 쌍"이라 바로 눈에 띈다.
   */
  /** 리포트 탭 — 이미 채워진 resBody 를 [종합] 패널로 감싸고, [ID 재구성] 패널을
   *  형제로 붙인다. 기존 리포트 마크업은 건드리지 않는다. */
  function jyZoomHide() {
    const z = document.getElementById("jyZoom");
    if (z) z.classList.add("hidden");
  }

  /** [PDF 저장] — 리포트 창에 **지금 보이는 그대로**를 브라우저 인쇄로 PDF 로 뽑는다.
   *  서버 렌더를 따로 두지 않는 이유: 리플레이 편집본·선택한 탭·재구성 결과까지
   *  화면 상태가 곧 리포트라, 같은 DOM 을 찍는 것이 가장 정확하다.
   *  인쇄 전용 스타일(main.css @media print)이 리포트 창만 남기고 펼친다.
   *  파일 이름은 인쇄 창이 문서 제목을 쓰므로 잠시 바꿨다가 되돌린다. */
  function saveReportPdf() {
    const t0 = document.title;
    const ttl = ($("resTitle").textContent || "리포트").replace(/[\\/:*?"<>|]+/g, " ").trim();
    const d = new Date();
    const ymd = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
    document.title = `${ttl} (${ymd})`;
    document.body.classList.add("print-report");
    const done = () => {
      document.body.classList.remove("print-report");
      document.title = t0;
      window.removeEventListener("afterprint", done);
    };
    window.addEventListener("afterprint", done);
    window.print();
  }

  function mountRepTabs(sid, floor, pm) {
    jyZoomHide();
    const body = $("resBody");
    const main = body.innerHTML;
    JY_SID = sid; JY_FLOOR = floor || "";
    JY = null; JY_PARAMS = null; JY_OPEN = {}; JY_MODE = "pr"; JY_TL = []; JY_PM = pm || [];
    body.innerHTML =
      `<div class="reptabs">`
      + `<button class="tag-btn on" data-rt="sum">종합</button>`
      + `<button class="tag-btn" data-rt="jy">ID 재구성</button>`
      + `</div>`
      + `<div class="reppane" data-rp="sum">${main}</div>`
      + `<div class="reppane hidden" data-rp="jy"></div>`;
    body.querySelectorAll(".reptabs .tag-btn").forEach((b) => {
      b.onclick = () => {
        const k = b.dataset.rt;
        body.querySelectorAll(".reptabs .tag-btn").forEach((x) => x.classList.toggle("on", x === b));
        body.querySelectorAll(".reppane").forEach((x) => x.classList.toggle("hidden", x.dataset.rp !== k));
      };
    });
    // 사람별 지표가 [종합]에 있으므로 재구성은 리포트를 열 때 바로 돌린다.
    jyLoad();
  }

  // ================================================== 리포트 — ID 재구성 탭
  // 재생 화면 옆에 객체를 하나씩 늘어놓으면 조잡하다. 필요한 것은 "리포트를 눌러
  // 분석 결과를 따로 확인" 하는 것이라, 전/후 표·썸네일·인자를 전부 이 탭에 둔다.
  let JY = null;        // 현재 탭의 재구성 결과
  let JY_SID = null;    // 대상 세션 id
  let JY_FLOOR = null;  // 대상 층
  let JY_PARAMS = null; // 사용자가 바꾼 인자 (null = 기본값)
  let JY_MODE = "pr";   // pr=사람(재구성 후) · tl=트랙렛(재구성 전)
  // 사람별 지표 표의 EPFI 기준. person = 사람당 경로 1개로 재적분(재구성 기준),
  // track = 기존 방식(조각별 EPFI 를 관측 수로 가중평균). 둘을 동시에 늘어놓으면
  // 어느 쪽을 봐야 하는지 흐려져, 버튼으로 갈아 본다.
  let JY_BASIS = "person";
  let JY_OPEN = {};     // 사람 행 펼침 상태
  let JY_BUSY = false;
  let JY_TL = [];       // 재구성 전 트랙렛 목록 (segments 를 펼쳐 만든다)
  let JY_PM = [];       // 그 층의 person_metrics (트랙렛 단위 EPFI·이탈)
  let JY_REFINE = false; // 정밀 재구성(crop 재임베딩) 사용 여부

  const JY_FIELDS = { jyCosTh: "cos_th", jyRrTh: "rerank_th", jyLinkTol: "link_tol",
                      jySpeed: "max_speed_mps", jySlack: "slack_m", jyFragObs: "fragment_obs" };

  const jyColor = (pid, jy) => {
    const ids = (((jy || JY) || {}).persons || []).map((p) => p.person_id);
    const i = Math.max(0, ids.indexOf(pid));
    return `hsl(${(i * 47) % 360},70%,58%)`;
  };

  /** 트랙렛 키("cam:local")의 대표 프레임 썸네일 — 임베딩과 같은 프레임의 crop.
   *  옛 녹화(schema ≤4)는 404 → onerror 로 지워 빈칸을 남기지 않는다. */
  function jyThumb(key, i, cls) {
    if (!key || !JY_SID) return "";
    const k = String(key).lastIndexOf(":");
    if (k < 0) return "";
    const cam = key.slice(0, k), lid = key.slice(k + 1);
    if (!/^\d+$/.test(lid)) return "";
    return `<img class="jythumb ${cls || ""}" loading="lazy" alt=""`
      + ` src="${API.drillThumbUrl(JY_SID, cam, lid, JY_FLOOR, i || 0)}" onerror="this.remove()">`;
  }

  /** 한 트랙렛의 대표 프레임 띠. n 장까지 — 접힘 1장, 펼침 EMB_PER_TRACK(8)장.
   *  없는 인덱스는 서버가 마지막 것으로 되돌려주므로 중복이 보일 수 있는데,
   *  그게 "이 트랙렛은 프레임이 적다" 는 신호라 지우지 않는다. */
  function jyStrip(seg, frames) {
    const cap = `${seg.key.replace("rh_", "")} · ${(seg.t1 - seg.t0).toFixed(1)}s · ${seg.n}관측`;
    const imgs = Array.from({ length: frames }, (_, i) => jyThumb(seg.key, i)).join("");
    return `<figure class="jyfig" title="${cap}">${imgs}<figcaption>${cap}</figcaption></figure>`;
  }

  function jySection() {
    if (JY_BUSY) return `<div class="repsec-h">여정 재구성</div>`
      + `<div class="mnote">${JY_REFINE ? "정밀 재구성 중 — crop 재임베딩(GPU)…" : "재구성 중…"}</div>`;
    if (!JY || !JY.ok) {
      return `<div class="repsec-h">여정 재구성</div>`
        + `<div class="mnote">${(JY && JY.reason) || "재구성 결과 없음"}</div>`;
    }
    const cfgOpen = JY_PARAMS != null;
    let h = `<div class="repsec-h">여정 재구성 <i class="mtag">ReID 후처리</i>`
      + `<button class="tag-btn xs" id="jyCfgBtn" title="재구성 인자 조정">⚙ 인자</button></div>`
      + `<div class="repgrid">`
      + repRow("재구성", `트랙렛 <b>${JY.tracklets}</b>개 → 사람 <b>${JY.n_persons}</b>명`
               + ` + 파편 ${JY.n_fragments}개`)
      + repRow("병합 문턱", `코사인 ${JY.params.cos_th}`
               + (JY.params.rerank ? ` · 재랭킹 ${JY.params.rerank_th} (k-reciprocal, CVPR 2017)` : " · 재랭킹 없음"))
      + repRow("파편 기준", `관측 ${JY.fragment_obs_th} 미만 — 오탐·스침으로 분리`)
      + repRow("특징", JY.refined
          ? `crop 재임베딩 <b>(정밀)</b> — 트랙 안쪽 뒤바뀜까지 분리`
          : `녹화 EMA — 트랙 <b>안쪽</b>의 뒤바뀜은 못 본다 (⚙ 정밀 재구성)`)
      + repRow("물리 제약", `보행 ${JY.params.max_speed_mps}m/s · 매핑 여유 ${JY.params.slack_m}m`
               + ` · 제약 완화 ${JY.params.link_tol}`)
      + `</div>`
      + `<div class="jycfg${cfgOpen ? "" : " hidden"}" id="jyCfg">
          <div class="jycfg-g">
            <label title="원본 코사인 하한 — 재랭킹과 무관하게 의미 고정">코사인 하한
              <input type="number" id="jyCosTh" step="0.05" min="0" max="0.99"></label>
            <label title="k-reciprocal 재랭킹 유사도 하한">재랭킹 하한
              <input type="number" id="jyRrTh" step="0.02" min="0" max="0.99"></label>
            <label title="군집 간 허용 금지쌍 비율. 0=한 쌍만 어겨도 병합 거부(엄격)">제약 완화
              <input type="number" id="jyLinkTol" step="0.05" min="0" max="1"></label>
            <label title="사람 보행 상한 — 넘으면 다른 사람으로 본다">보행 상한 m/s
              <input type="number" id="jySpeed" step="0.5" min="0.5" max="20"></label>
            <label title="카메라 간 매핑 오차 여유 — 이 안은 속도 판정 생략">매핑 여유 m
              <input type="number" id="jySlack" step="0.5" min="0" max="30"></label>
            <label title="관측이 이보다 적은 군집은 '파편'으로 빼고 사람 수에서 제외">파편 기준 관측
              <input type="number" id="jyFragObs" step="10" min="0"></label>
            <label title="끄면 원본 코사인만으로 묶는다">재랭킹
              <input type="checkbox" id="jyRerank"></label>
            <label title="같은 출구를 두 번 나갈 수 없다는 물리 제약">출구 유일성
              <input type="checkbox" id="jyExitUniq"></label>
            <label title="저장된 crop 을 ReID 로 다시 임베딩해 트랙 안쪽 뒤바뀜까지 잡는다 (GPU·수십 초)">정밀 재구성
              <input type="checkbox" id="jyRefine"></label>
          </div>
          <div class="jycfg-b">
            <button class="tag-btn" id="jyReset">기본값</button>
            <button class="btn primary sm" id="jyApply">재구성</button>
          </div>
        </div>`;
    const v = JY.viz;
    if (v && v.xy && v.xy.length) {
      h += `<div class="jyviz">
        <div class="jyv"><div class="jyv-h">임베딩 2D 투영 <i>점=트랙렛 · 색=사람</i></div>
          <canvas id="jyScatter" width="440" height="340"></canvas></div>
        <div class="jyv"><div class="jyv-h">유사도 행렬 <i>클러스터 순 · 대각 블록이 뚜렷할수록 잘 묶임</i></div>
          <canvas id="jyMatrix" width="340" height="340"></canvas></div>
      </div>`;
    }
    h += `<div class="jybar">객체별
        <span class="seg">
          <button class="tag-btn xs${JY_MODE === "tl" ? " on" : ""}" id="jyModeTl"
            title="재구성 전 — 카메라별 트랙 조각">트랙렛 ${JY.tracklets}</button>
          <button class="tag-btn xs${JY_MODE === "pr" ? " on" : ""}" id="jyModePr"
            title="재구성 후 — ReID 로 묶은 사람">사람 ${JY.n_persons}</button>
        </span>
        <i>${JY_MODE === "pr" ? "카드를 누르면 조각별 대표 프레임 8장이 펼쳐집니다 · 썸네일에 마우스를 올리면 확대" : "재구성 전 조각 — 같은 사람이 여러 개로 흩어져 있습니다. 색이 소속"}</i>
      </div>`;
    h += JY_MODE === "pr" ? jyPersonTbl() : jyTrackletTbl();
    return h;
  }

  /** 사람 단위 EPFI·이탈.
   *
   *  서버(여정 재구성)가 person.epfi 를 주면 **그걸 쓴다** — 사람당 경로를
   *  한 번만 배정해 정의식대로 다시 적분한 값이다.
   *  엔진의 person_metrics 는 gid=카메라별 트랙 조각 단위라 카메라가 바뀔 때마다
   *  경로가 재배정돼, 우회한 사람일수록 이탈이 0 으로 리셋된다(실측 최대 4.5배
   *  과소평가 — MACS-EVAC-VR-2026-004). 조각값을 평균해도 각 조각이 서로 다른
   *  경로 기준이라 의미가 섞인다.
   *  옛 서버 응답(epfi 없음)일 때만 조각값 가중평균으로 물러난다. */
  function jyFoldMetrics(person, pmByKey) {
    let wsum = 0, epfi = 0, dev = 0, devMax = null;
    const routes = {};
    (person.segments || []).forEach((s) => {
      const m = pmByKey[s.key];
      if (!m) return;
      const w = s.n || 1;
      if (m.epfi != null) { epfi += m.epfi * w; wsum += w; }
      if (m.mean_deviation_m != null) dev += m.mean_deviation_m * w;
      if (m.max_deviation_m != null) devMax = Math.max(devMax == null ? -1 : devMax, m.max_deviation_m);
      if (m.assigned_route_id) routes[m.assigned_route_id] = (routes[m.assigned_route_id] || 0) + w;
    });
    const route = Object.entries(routes).sort((a, b) => b[1] - a[1])[0];
    return {
      // 조각 기준 (기존 방식) — 조각마다 배정 경로가 다르므로 참고값이다
      epfi: wsum ? epfi / wsum : null,
      dev: wsum ? dev / wsum : null,
      devMax,
      route: route ? route[0] : null,
      // 배정 경로가 조각마다 갈리면 경로를 오갔거나 배정이 리셋됐다는 뜻
      routeSplit: Object.keys(routes).length > 1,
      // 사람 기준 (서버가 경로 1개로 재적분) — 옛 응답이면 없다
      epfiP: person && person.epfi != null ? person.epfi : null,
      devP: person && person.dev_m != null ? person.dev_m : null,
      routeP: (person && person.route_id) || null,
    };
  }

  /** 사람별 4대지표·운동 지표 표 — 재구성이 만든 '사람' 단위로 본 결과.
   *  ④ 리플레이 사이드바의 표는 트랙렛(조각) 단위라 한 사람이 여러 줄로 나온다. */
  function jyMetricTbl(jy) {
    jy = jy || JY;
    if (!jy || !jy.ok) return "";
    const ps = (jy.persons || []).filter((p) => !p.fragment);
    if (!ps.length) return "";
    const pm = {};
    (JY_PM || []).forEach((m) => { if (m.global_track_id) pm[m.global_track_id] = m; });
    const ex = jy.exit_summary || {};
    const f = (v, d) => v == null ? "—" : (+v).toFixed(d);
    const rows = ps.map((p) => {
      const m = jyFoldMetrics(p, pm);
      const exits = Object.entries(p.exit_at || {});
      const useP = JY_BASIS === "person" && m.epfiP != null;
      const eShow = useP ? m.epfiP : m.epfi;
      const dShow = useP ? m.devP : m.dev;
      const bad = eShow != null && eShow < 60;
      return `<tr>
        <td><span class="jydot" style="background:${jyColor(p.person_id, jy)}"></span>
            <b>${p.person_id}</b></td>
        <td class="t-num${bad ? " bad" : ""}">${f(eShow, 0)}</td>
        <td class="t-num">${f(dShow, 2)}</td>
        <td class="t-num">${f(m.devMax, 2)}</td>
        <td class="t-num">${f(p.dist_m, 1)}</td>
        <td class="t-num">${f(p.speed_avg, 2)}</td>
        <td class="t-num">${f(p.speed_p95, 2)}</td>
        <td class="t-num">${f(p.accel_p95, 2)}</td>
        <td class="t-num">${exits.length
            ? exits.map(([k, v]) => `${v}s`).join(" ")
              + (p.exit_count > 1
                 ? ` <i class="warn" title="이 사람 이름으로 게이트가 ${p.exit_count}번 셌다 — 서로 다른 사람이 하나로 묶였을 수 있다">×${p.exit_count}</i>`
                 : "")
            : `<i class="dim" title="이 사람의 조각 중 출구 게이트를 통과한 것이 없다">—</i>`}</td>
        <td class="t-num">${p.evac_sec != null ? f(p.evac_sec, 1) : "—"}</td>
        <td class="t-num">${f(p.t1 - p.t0, 1)}</td>
        <td>${m.route ? m.route.replace("auto-evac-", "ae") : "—"}${m.routeSplit ? ' <i title="조각마다 배정 경로가 달랐다 — 경로를 오갔을 수 있음">↔</i>' : ""}</td>
      </tr>`;
    }).join("");
    const note = ex.events != null
      ? `출구 게이트 ${ex.events}건 → 사람 ${ex.persons_with_exit}명 귀속`
        + (ex.unowned ? ` · ${ex.unowned}건 미귀속(재구성에서 빠진 조각)` : "")
      : "";
    const srv = ps.length && jyFoldMetrics(ps[0], pm).epfiP != null;
    const seg = srv
      ? `<span class="seg jybasis">
           <button class="tag-btn xs${JY_BASIS === "person" ? " on" : ""}" id="jyBasisP"
             title="이 사람에게 경로를 하나만 배정해 정의식대로 다시 적분 — 우회를 반영한다">재구성 기준</button>
           <button class="tag-btn xs${JY_BASIS === "track" ? " on" : ""}" id="jyBasisT"
             title="기존 방식 — 카메라별 트랙 조각의 EPFI 를 관측 수로 가중평균">조각 기준</button>
         </span>` : "";
    const basisNote = srv
      ? (JY_BASIS === "person"
         ? `사람당 경로 1개로 재적분${jy.d_allow ? ` (d_allow ${jy.d_allow}m)` : ""} — 두 기준의
            EPFI 가 크게 다르면 카메라를 옮기며 경로 배정이 리셋된 것이다`
         : `조각별 EPFI 를 관측 수로 가중평균 — 조각마다 배정 경로가 달라 우회가 가려질 수 있다`)
      : "EPFI·이탈은 조각값을 관측 수로 가중평균";
    return `<div class="jybar">사람별 지표 ${seg}
        <i>재구성된 ${ps.length}명(global id) · ${basisNote}</i></div>`
      + (note ? `<div class="jynote">${note} — 게이트는 <b>트랙렛 단위</b>로 세므로
          한 사람이 조각나면 여러 번, 재구성에서 빠진 조각은 아무에게도 안 붙는다.
          사람 수와 통과 수가 다른 것 자체가 품질 신호다.</div>` : "")
      + `
      <div class="jymwrap"><table class="reptbl jymtbl">
        <thead><tr>
          <th>사람</th>
          <th title="경로 충실도 0~100 — 높을수록 권장 경로를 따름">EPFI<i class="thsub">${
            JY_BASIS === "person" ? "재구성 기준" : "조각 기준"}</i></th>
          <th title="권장 경로에서 평균 얼마나 벗어났나">이탈 평균 m</th>
          <th title="가장 크게 벗어난 순간">최대 m</th>
          <th title="관측된 구간의 이동 거리 합">이동 m</th>
          <th title="이동거리 / 관측 구간 시간">평균 m/s</th>
          <th title="1초 창 속도의 상위 5% — 단발 이상치에 흔들리지 않게">빠른 구간 m/s</th>
          <th title="속도 변화율의 상위 5%">가속 m/s²</th>
          <th title="경보 이후 출구 통과까지 걸린 시간">출구 통과</th>
          <th title="처음 화면에 보인 때부터 출구를 통과하기까지 — 개인의 실제 이동 시간">대피 s</th>
          <th title="처음 보인 때부터 마지막까지">추적 s</th>
          <th title="가장 많이 배정된 대피 경로">경로</th>
        </tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function jyPersonTbl() {
    // 표 대신 카드 — 행 높이에 갇히면 썸네일을 키울 수 없어 "뭐가 사람인지"
    // 안 보였다. 카드는 폭을 통째로 써서 조각별 대표 프레임을 나란히 건다.
    return `<div class="jycards">` + (JY.persons || []).map((p) => {
      const open = !!JY_OPEN[p.person_id];
      const cams = p.cams.map((c) => c.replace("rh_", "")).join(" ");
      const segs = (p.segments || []).slice().sort((a, b) => a.t0 - b.t0);
      return `<div class="jyp${p.fragment ? " frag" : ""}${open ? " open" : ""}" data-pid="${p.person_id}">
        <div class="jyp-h">
          <span class="jydot" style="background:${jyColor(p.person_id)}"></span>
          <b>${p.person_id}</b>${p.fragment ? ` <i class="ftag">파편</i>` : ""}
          <i>${p.n_tracklets}조각 · ${p.obs}관측 · ${(p.t1 - p.t0).toFixed(1)}s · ${p.cams.length}캠</i>
          <span class="sp"></span>
          <i class="camlist">${cams}</i>
          <span class="jyx">${open ? "대표 프레임 접기 ▴" : "대표 프레임 펼치기 ▾"}</span>
        </div>
        <div class="jyp-strip">${segs.map((s) => jyStrip(s, open ? 8 : 1)).join("")}</div>
      </div>`;
    }).join("") + `</div>`;
  }

  function jyTrackletTbl() {
    return `<div class="jygrid">` + JY_TL.map((s) =>
      `<figure class="jyfig tl" title="${s.key.replace("rh_", "")} · 소속 ${s.pid}">
        ${jyThumb(s.key, 0)}
        <figcaption><span class="jydot" style="background:${jyColor(s.pid)}"></span>
          ${s.key.replace("rh_", "")}<br><i>${s.n}관측 · ${(s.t1 - s.t0).toFixed(1)}s</i></figcaption>
      </figure>`).join("") + `</div>`;
  }

  /** 재구성 결과에서 "재구성 전" 트랙렛 목록을 만든다 — 같은 데이터의 반대편 뷰라
   *  별도 요청 없이 전/후를 그대로 대조할 수 있다. */
  function jyBuildTracklets() {
    JY_TL = [];
    (JY && JY.persons || []).forEach((p) =>
      (p.segments || []).forEach((s) =>
        JY_TL.push({ ...s, pid: p.person_id, fragment: p.fragment })));
    JY_TL.sort((a, b) => a.t0 - b.t0);
  }

  async function jyLoad() {
    if (!JY_SID || JY_BUSY) return;
    JY_BUSY = true; jyPaint();
    try {
      // d_allow 는 ④ 리플레이의 [임계값 조정] 현재값을 넘긴다 — 사람별 EPFI 를
      // 서버가 그 값으로 재적분하므로, 안 넘기면 녹화 당시 값으로 계산돼
      // 화면의 다른 EPFI 와 숫자가 어긋난다.
      const rpD = parseFloat((document.getElementById("rpD") || {}).value);
      const r = await API.drillJourney(JY_SID,
        { floor: JY_FLOOR, viz: true, refine: JY_REFINE,
          ...(isNaN(rpD) ? {} : { d_allow: rpD }), ...(JY_PARAMS || {}) });
      const by = r.by_floor || {};
      JY = by[JY_FLOOR] || Object.values(by).find((x) => x && x.ok) || Object.values(by)[0] || null;
      if (JY && JY.ok && JY.floor_id) JY_FLOOR = JY.floor_id;   // 썸네일 URL 이 맞도록
    } catch (e) {
      JY = { ok: false, reason: "재구성 실패: " + e.message };
    } finally {
      JY_BUSY = false;
      jyBuildTracklets();
      jyPaint();
    }
  }

  /** ID 재구성 패널만 다시 그린다 — 종합 탭은 건드리지 않는다. */
  function jyPaint() {
    // 사람별 지표는 [종합] 탭에 산다 — 결과이지 재구성 진단이 아니다.
    const mt = document.getElementById("repPersonTbl");
    if (mt) {
      mt.innerHTML = JY_BUSY
        ? `<div class="mnote">${JY_REFINE ? "정밀 재구성 중 — crop 재임베딩(GPU)…" : "ReID 재구성 중…"}</div>`
        : (JY && JY.ok) ? jyMetricTbl(JY)
        : `<div class="mnote">${(JY && JY.reason) || "재구성 결과 없음"}</div>`;
      // EPFI 기준 토글은 이 표 안에 있다 — jyBind 는 [재구성] 탭 pane 만 훑으므로
      // 여기서 따로 건다.
      const bp = mt.querySelector("#jyBasisP"), bt = mt.querySelector("#jyBasisT");
      if (bp) bp.onclick = () => { JY_BASIS = "person"; jyPaint(); };
      if (bt) bt.onclick = () => { JY_BASIS = "track"; jyPaint(); };
    }
    const pane = document.querySelector('.reppane[data-rp="jy"]');
    if (!pane) return;
    pane.innerHTML = jySection();
    if (JY && JY.ok) {
      jyCfgFill(JY_PARAMS || JY.params);
      try { drawJourneyViz(JY); } catch (e) { /* 시각화 실패가 리포트를 막지 않게 */ }
    }
    jyBind(pane);
  }

  function jyCfgFill(src) {
    if (!src) return;
    Object.entries(JY_FIELDS).forEach(([el, k]) => {
      const n = document.getElementById(el);
      if (n && src[k] != null) n.value = src[k];
    });
    const rr = document.getElementById("jyRerank");
    if (rr && src.rerank != null) rr.checked = !!src.rerank;
    const eu = document.getElementById("jyExitUniq");
    if (eu && src.exit_unique != null) eu.checked = !!src.exit_unique;
    const rf = document.getElementById("jyRefine");
    if (rf) rf.checked = !!JY_REFINE;
  }

  function jyCfgRead() {
    const out = {};
    Object.entries(JY_FIELDS).forEach(([el, k]) => {
      const n = document.getElementById(el);
      if (n && n.value !== "") out[k] = Number(n.value);
    });
    const rr = document.getElementById("jyRerank");
    out.rerank = !!(rr && rr.checked);
    const eu = document.getElementById("jyExitUniq");
    out.exit_unique = !!(eu && eu.checked);
    JY_REFINE = !!document.getElementById("jyRefine")?.checked;
    return out;
  }

  /** 썸네일 확대 — 카드/격자는 overflow 컨테이너라 CSS transform 으로 키우면
   *  위아래가 잘린다. body 에 붙인 고정위치 패널에 큰 그림을 띄워 어떤 컨테이너의
   *  경계에도 걸리지 않게 한다. 화면 밖으로 나가면 반대편으로 뒤집는다. */
  function jyZoomEl() {
    let z = document.getElementById("jyZoom");
    if (!z) {
      z = document.createElement("div");
      z.id = "jyZoom"; z.className = "jyzoom hidden";
      z.innerHTML = `<img alt=""><div class="jyzoom-c"></div>`;
      document.body.appendChild(z);
    }
    return z;
  }

  function jyBindZoom(pane) {
    const z = jyZoomEl(), img = z.querySelector("img"), cap = z.querySelector(".jyzoom-c");
    const W = 196, H = 392, GAP = 14;
    pane.addEventListener("mouseover", (e) => {
      const el = e.target.closest(".jythumb");
      if (!el) return;
      img.src = el.src;
      const fig = el.closest(".jyfig");
      cap.textContent = (fig && fig.getAttribute("title")) || "";
      const r = el.getBoundingClientRect();
      // 오른쪽에 자리가 없으면 왼쪽, 세로는 화면 안으로 밀어 넣는다
      const left = (r.right + GAP + W <= innerWidth) ? r.right + GAP
                 : Math.max(8, r.left - GAP - W);
      const top = Math.max(8, Math.min(innerHeight - H - 30, r.top + r.height / 2 - H / 2));
      z.style.left = left + "px"; z.style.top = top + "px";
      z.classList.remove("hidden");
    });
    pane.addEventListener("mouseout", (e) => {
      if (e.target.closest(".jythumb")) z.classList.add("hidden");
    });
  }

  function jyBind(pane) {
    if (!pane.dataset.zoomBound) { jyBindZoom(pane); pane.dataset.zoomBound = "1"; }
    const on = (id, fn) => { const n = pane.querySelector("#" + id); if (n) n.onclick = fn; };
    on("jyCfgBtn", () => pane.querySelector("#jyCfg").classList.toggle("hidden"));
    on("jyModeTl", () => { JY_MODE = "tl"; jyPaint(); });
    on("jyModePr", () => { JY_MODE = "pr"; jyPaint(); });
    on("jyReset", () => { JY_PARAMS = null; jyLoad(); });
    on("jyApply", () => { JY_PARAMS = jyCfgRead(); jyLoad(); });
    pane.querySelectorAll(".jyp").forEach((el) => {
      el.onclick = () => {
        const pid = el.dataset.pid;
        JY_OPEN[pid] = !JY_OPEN[pid];
        jyPaint();
      };
    });
  }

  /** 산점도·행렬은 DOM 삽입 후에 그린다(canvas 라 innerHTML 로는 안 됨). */
  function drawJourneyViz(jy) {
    const v = jy && jy.ok && jy.viz;
    if (!v) return;
    const pids = [...new Set(v.person_of || [])].filter(Boolean);
    const col = (pid) => `hsl(${(Math.max(0, pids.indexOf(pid)) * 47) % 360},70%,58%)`;
    const sc = document.getElementById("jyScatter");
    if (sc && v.xy.length) {
      const g = sc.getContext("2d");
      g.clearRect(0, 0, sc.width, sc.height);
      const xs = v.xy.map((p) => p[0]), ys = v.xy.map((p) => p[1]);
      const x0 = Math.min(...xs), x1 = Math.max(...xs);
      const y0 = Math.min(...ys), y1 = Math.max(...ys);
      const pad = 14;
      const TX = (x) => pad + (x - x0) / ((x1 - x0) || 1) * (sc.width - pad * 2);
      const TY = (y) => pad + (y - y0) / ((y1 - y0) || 1) * (sc.height - pad * 2);
      v.xy.forEach((pt, i) => {
        g.fillStyle = col(v.person_of[i]);
        g.beginPath(); g.arc(TX(pt[0]), TY(pt[1]), 4, 0, 7); g.fill();
        g.strokeStyle = "rgba(0,0,0,.45)"; g.lineWidth = 1; g.stroke();
      });
    }
    const mx = document.getElementById("jyMatrix");
    if (mx && v.sim && v.sim.length) {
      const g = mx.getContext("2d");
      const n = v.sim.length, cell = mx.width / n;
      for (let i = 0; i < n; i++) {
        for (let j = 0; j < n; j++) {
          const s = Math.max(0, Math.min(1, v.sim[i][j]));
          // 같은 사람끼리는 색으로, 그 외는 회색조 — 블록 구조가 눈에 들어오게
          g.fillStyle = (v.person_of[i] && v.person_of[i] === v.person_of[j])
            ? `hsla(${(Math.max(0, pids.indexOf(v.person_of[i])) * 47) % 360},70%,58%,${0.25 + 0.75 * s})`
            : `rgba(220,220,220,${s * s})`;
          g.fillRect(j * cell, i * cell, Math.ceil(cell), Math.ceil(cell));
        }
      }
    }
  }

  function showDrillModal(roll) {
    if (!roll) return;
    const b = roll.building || {}, s = roll.summary || {};
    const fname = (f) => (typeof App !== "undefined" ? App.floorName(f) : f);
    // 리플레이는 세션 당시 도면·1초 타임라인을 같이 넘긴다(없으면 현재 설정·섹션 생략)
    REP_SITES = roll.sites || {};
    const tls = roll.timelines || {};
    // IDR — 구역별 유지(건물 단일평균 없음). 층 표기를 붙여 전 층 구역을 나열.
    const zones = [];
    Object.entries(b.idr_by_floor || {}).forEach(([f, zs]) =>
      (zs || []).forEach((z) => zones.push({ m: z, floor: f })));
    // 층별 결과를 층 표기와 함께 평탄화 — 해석 문장("혼잡이 가장 컸던 지점은 10F b2")용
    const exits = [], bns = [], persons = [], journeys = [];
    (roll.per_floor || []).forEach((pf) => {
      const r = pf.result || {};
      (r.exit_metrics || []).forEach((m) => exits.push({ m, floor: pf.floor_id }));
      (r.bottleneck_metrics || []).forEach((m) => bns.push({ m, floor: pf.floor_id }));
      (r.person_metrics || []).forEach((m) => persons.push(m));
      (r.journeys || []).forEach((j) => journeys.push({ j, floor: pf.floor_id }));
    });
    const anyGid = (roll.per_floor || []).some((pf) => pf.result && pf.result.global_id);
    const zStarted = zones.filter((z) => z.m.status === "started").length;
    const startTxt = Object.entries(s.floor_start_ts || {})
      .map(([f, ts]) => `${fname(f)} ${ts != null ? hhmmss(ts) : "—"}`).join(" · ") || "—";
    // 층별 상세 — 숫자 옆에 grade.js 등급 배지
    const perFloor = (roll.per_floor || []).map((pf) => {
      const r = pf.result || {};
      const passed = (r.exit_metrics || []).reduce((a, e) => a + (e.actual_count || 0), 0);
      const nz = (r.zone_metrics || []).length;
      const started = (r.zone_metrics || []).filter((z) => z.status === "started").length;
      return `<tr>
        <td>${fname(pf.floor_id)}</td>
        <td class="t-num">${r.sei != null ? fmt1(r.sei) : "—"} ${pill(gradeOf(r.sei))}</td>
        <td class="t-num">${r.epfi_avg != null ? fmt1(r.epfi_avg) : "—"} ${pill(gradeOf(r.epfi_avg))}</td>
        <td class="t-num">${fmt1(r.cbs_total || 0)} ${pill(gradeOf(r.cbs_total || 0, "cbs"))}</td>
        <td class="t-num">${passed}</td>
        <td class="t-num">${started}/${nz} ${nz ? pill(gradeOf(Grade.idrScore(started, nz, true))) : ""}</td>
      </tr>`;
    }).join("");
    // 층별 비상구 분석 + 타임라인 재료
    const tlItems = [];
    const exitSecs = (roll.per_floor || []).map((pf) => {
      const r = pf.result || {};
      const t0 = r.alarm_ts != null ? r.alarm_ts : roll.alarm_ts;
      const fa = exitAnalysis(r, pf.floor_id, tls[pf.floor_id], t0);
      const fst = (s.floor_start_ts || {})[pf.floor_id];
      tlItems.push({ label: fname(pf.floor_id), fa,
                     start: floorStartSec(r, fst != null && t0 != null ? fst - t0 : null) });
      return exitSection(fa, fname(pf.floor_id));
    }).join("");
    // 층별 대피 개시 순서 — 측정 시각만 나열한다(순위·합성 점수 없음)
    const st = tlItems.filter((it) => it.start != null).sort((a, c) => a.start - c.start);
    const noSt = tlItems.filter((it) => it.start == null && it.fa);
    const tlRead = [];
    if (st.length >= 2) {
      tlRead.push(`층별 대피 개시는 ${st.map((it) => `${it.label} ${mmss(it.start)}`).join(", ")} 순으로 확인되었습니다(경보 후 경과 시간).`);
    }
    if (noSt.length) tlRead.push(`${noSt.map((it) => it.label).join(", ")}에서는 대피 개시가 확인되지 않았습니다.`);
    tlItems.forEach((it) => {
      const ls = it.fa && it.fa.hasTl ? it.fa.rows.map((e) => e.last).filter((v) => v != null) : [];
      if (ls.length) tlRead.push(`${josa(it.label, "은/는")} 경보 후 ${kdur(Math.max(...ls))}에 마지막 인원이 비상구를 통과하였습니다.`);
    });
    const tlHtml = repTimeline(tlItems);
    $("resTitle").textContent = `건물 훈련 결과 리포트 — ${roll.session_id}`;
    const ov = { sei: b.sei, epfi: b.epfi_avg, cbs: b.cbs_total,
                 zStarted, zTot: zones.length, totOut: s.total_passed,
                 globalId: anyGid };
    $("resBody").innerHTML =
      repSummary(ov)
      + repVerdict(ov)
      + `<div class="repsec-h">개요</div>`
      + `<div class="reprows">`
        + repRow("경보 시각", fmtDT(roll.alarm_ts))
        + repRow("참여 층", (roll.floors || []).map(fname).join(", "))
        + repRow("총 통과 인원", `${s.total_passed != null ? s.total_passed : 0}명 · 출구 ${exits.length}곳`)
        + repRow("최대 혼잡 층", s.max_cbs_floor ? fname(s.max_cbs_floor) : "—")
        + repRow("층별 개시시각", startTxt)
      + `</div>`
      + `<div class="repsec-h">4대 지표 해석</div>`
      + repCards({ sei: b.sei, epfi: b.epfi_avg, cbs: b.cbs_total,
                   exits, persons, zones, bns, fname })
      + gradeLegend()
      + exitBars(exits, fname)
      + (exitSecs ? `<div class="repsec-h">비상구별 분석</div>` + exitSecs + exitNote(tlItems.map((it) => it.fa)) : "")
      + (tlHtml ? tlHtml + (tlRead.length ? `<ul class="repread">${tlRead.map((t) => `<li>${t}</li>`).join("")}</ul>` : "") : "")
      + journeysTable(journeys, fname)
      + `<div class="repsec-h">사람별 지표 <i class="mtag">global id 기준</i></div>`
      + `<div id="repPersonTbl"><div class="mnote">ReID 재구성 중…</div></div>`
      + `<div class="drill-perfloor">
        <div class="drill-perfloor-h">층별 상세</div>
        <table class="drill-tbl">
          <thead><tr><th>층</th><th>SEI</th><th>EPFI</th><th>CBS</th><th>통과</th><th>IDR 개시</th></tr></thead>
          <tbody>${perFloor}</tbody>
        </table>
      </div>`;
    // 여정 재구성은 성격이 달라(후처리 진단) 종합 리포트에 이어 붙이면 읽기가
    // 어렵다 — 탭으로 분리하고 기본은 [종합](기존 리포트 그대로).
    // 사람별 지표는 그 층의 트랙렛 지표를 접어서 만든다 — 층을 못 찾으면 전 층.
    const jyPf = (roll.per_floor || []).find((x) => x.floor_id === roll.jy_floor);
    mountRepTabs(roll.session_id, roll.jy_floor,
                 (jyPf && jyPf.result && jyPf.result.person_metrics) || persons);
    $("resultModal").classList.remove("hidden");
  }

  // ================================================== 외부 노출
  function updateUI() {
    setBtn();
    renderAlarmPanel();
    updateCards();
    if (onMapRender) onMapRender();
  }

  return {
    init, bootstrap, onState, placeAlarm, updateCards,
    openDrillReport: showDrillModal,   // ④ 리플레이 탭에서 드릴 롤업 리포트 재사용
    isPlacing: () => addingOrigin,
    pendingAlarmOrigins: () => pendingOrigins,
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
