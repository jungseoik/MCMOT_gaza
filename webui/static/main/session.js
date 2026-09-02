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
    $("resClose").onclick = () => $("resultModal").classList.add("hidden");
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
    const W = cv.parentElement.clientWidth || 300;
    const H = 180;
    cv.width = W; cv.height = H;
    const ctx = cv.getContext("2d");

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
  // ==================== 결과 리포트 헬퍼 (해석형, v1.13) ====================
  // "지표 이름과 숫자만 있는 결과"는 훈련 담당자가 해석할 수 없다 — 지표마다
  // ①무엇을 재는지 ②이번 값을 어떻게 읽는지 문장을 자동으로 붙인다.
  // 등급(우수/보통/미흡)은 표시용 참고 기준(80·60, CBS 0.5·10) — 요구사항에 규정 없음.
  function gradeOf(v, kind) {
    if (v == null) return null;
    if (kind === "cbs") return v <= 0.5 ? ["원활", "g-good"] : v <= 10 ? ["주의", "g-mid"] : ["혼잡", "g-bad"];
    return v >= 80 ? ["우수", "g-good"] : v >= 60 ? ["보통", "g-mid"] : ["미흡", "g-bad"];
  }
  const pill = (g) => g ? `<span class="gpill ${g[1]}">${g[0]}</span>`
                        : `<span class="gpill g-na">표본부족</span>`;
  const repRow = (k, v) => `<div class="resrow"><span>${k}</span><b class="t-num">${v}</b></div>`;
  const fmtDT = (ts) => ts ? new Date(ts * 1000).toLocaleString("ko-KR", { hour12: false }) : "—";

  const REP_WHAT = {
    epfi: "재실자들이 권장 피난경로를 얼마나 충실히 따라갔는가. 100 = 전원 경로 위로 이동, 낮을수록 경로 이탈이 많았다는 뜻.",
    sei:  "출구들이 설계 의도대로 골고루 쓰였는가. 100 = 실제 이용 분포가 설계 분포와 일치, 낮을수록 특정 출구로 쏠림.",
    cbs:  "병목(문·계단 앞)에서 임계밀도를 넘긴 혼잡이 시간에 걸쳐 얼마나 쌓였는가. 0 = 정체 없음, 클수록 오래·심하게 막힘.",
    idr:  "경보 후 각 구역이 피난을 시작했는가, 얼마나 빨리 반응했는가(개시 구역 수·반응 지연).",
  };

  function repCard(name, sub, value, grade, what, read) {
    return `<div class="repcard">
      <div class="repcard-h"><b>${name}</b><span>${sub}</span>${pill(grade)}</div>
      <div class="repcard-v t-num">${value}</div>
      <div class="repcard-what">${what}</div>
      <div class="repcard-read">${read}</div>
    </div>`;
  }

  // 해석 문장 — 아래 함수들의 인자는 {m: 지표, floor: 층id|null} 목록 (드릴은 층 표기 포함)
  function readEpfi(avg, persons) {
    if (avg == null) return "표본 부족 — 경로에 배정된 이동 객체가 없어 산출하지 못했습니다.";
    const ps = persons.filter((p) => p.epfi != null);
    const low = ps.filter((p) => p.epfi < 60).length;
    const devs = ps.map((p) => p.mean_deviation_m).filter((v) => v != null);
    const md = devs.length ? devs.reduce((a, v) => a + v, 0) / devs.length : null;
    let t = `추적 ${ps.length}명 평균 <b>${fmt1(avg)}점</b>`
          + (md != null ? ` · 평균 경로이탈 ${fmt1(md)}m` : "") + ".";
    t += low ? ` 이 중 ${low}명은 60점 미만 — 권장 경로를 크게 벗어나 이동했습니다.`
             : " 대부분 권장 경로를 따라 이동했습니다.";
    return t;
  }
  function readSei(sei, exits, fname) {
    if (sei == null) return "표본 부족 — 출구를 통과한 인원이 없어 산출하지 못했습니다.";
    const es = exits.filter((e) => e.m.actual_share != null && e.m.design_share != null);
    if (!es.length) return `<b>${fmt1(sei)}점</b>.`;
    let w = es[0];
    es.forEach((e) => {
      if (Math.abs(e.m.actual_share - e.m.design_share)
          > Math.abs(w.m.actual_share - w.m.design_share)) w = e;
    });
    const d = (w.m.actual_share - w.m.design_share) * 100;
    const nm = (w.floor ? fname(w.floor) + " " : "") + w.m.exit_id;
    return Math.abs(d) < 5
      ? "실제 출구 이용이 설계 분포와 거의 일치했습니다."
      : `<b>${nm}</b> 출구가 설계보다 ${Math.abs(d).toFixed(0)}%p ${d > 0 ? "많이" : "적게"} 사용됨 — 안내·유도 개선 검토 지점.`;
  }
  function readCbs(total, bns, fname) {
    if (!bns.length) return "병목이 설정되지 않아 관측 대상이 없습니다.";
    if ((total || 0) <= 0.01) return "훈련 내내 모든 병목이 임계밀도 아래 — 정체 없이 통과했습니다.";
    let w = bns[0];
    bns.forEach((b) => { if ((b.m.cbs || 0) > (w.m.cbs || 0)) w = b; });
    const nm = (w.floor ? fname(w.floor) + " " : "") + w.m.bottleneck_id;
    return `가장 막힌 곳은 <b>${nm}</b> — 임계 초과 ${Math.round(w.m.over_threshold_sec)}초, `
         + `최대 ${fmt1(w.m.peak_density)}명/㎡ (위험도 ${w.m.risk_level}).`;
  }
  function readIdr(zones, fname) {
    if (!zones.length) return "구역이 설정되지 않았습니다.";
    const zn = (z) => (z.floor ? fname(z.floor) + " " : "") + z.m.zone_id;
    const st = zones.filter((z) => z.m.status === "started");
    const miss = zones.filter((z) => z.m.status !== "started");
    let t = `<b>${st.length}/${zones.length}</b> 구역 피난 개시.`;
    const dl = st.filter((z) => z.m.response_delay_sec != null);
    if (dl.length) {
      let w = dl[0];
      dl.forEach((z) => { if (z.m.response_delay_sec > w.m.response_delay_sec) w = z; });
      t += ` 가장 늦은 <b>${zn(w)}</b> — 경보 후 ${Math.round(w.m.response_delay_sec)}초에 개시.`;
    }
    if (miss.length) t += ` 미개시: ${miss.map(zn).join(", ")} (판정조건 미충족 또는 인원 없음).`;
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
    return `<div class="repsum">${seg.join(" · ")}</div>`;
  }

  /** 4대 지표 카드(설명+해석) 묶음. */
  function repCards(o) {
    const fn = o.fname || ((f) => f);
    const started = o.zones.filter((z) => z.m.status === "started").length;
    const idrG = !o.zones.length ? null
      : started === o.zones.length ? ["우수", "g-good"]
      : started > 0 ? ["보통", "g-mid"] : ["미흡", "g-bad"];
    return `<div class="repgrid">`
      + repCard("EPFI", "경로 충실도 (0~100)", o.epfi != null ? fmt1(o.epfi) : "—",
                gradeOf(o.epfi), REP_WHAT.epfi, readEpfi(o.epfi, o.persons))
      + repCard("SEI", "출구 활용 효율 (0~100)", o.sei != null ? fmt1(o.sei) : "—",
                gradeOf(o.sei), REP_WHAT.sei, readSei(o.sei, o.exits, fn))
      + repCard("CBS", "병목 혼잡 누적 (0=최선)", fmt1(o.cbs || 0),
                gradeOf(o.cbs || 0, "cbs"), REP_WHAT.cbs, readCbs(o.cbs, o.bns, fn))
      + repCard("IDR", "구역 반응 개시", `${started}/${o.zones.length}`,
                idrG, REP_WHAT.idr, readIdr(o.zones, fn))
      + `</div>`;
  }

  /** 출구별 실제(막대) vs 설계(주황 눈금) 분포 — SEI의 근거 시각화. */
  function exitBars(exits, fname) {
    const fn = fname || ((f) => f);
    const es = exits.filter((e) => e.m.actual_share != null);
    if (!es.length) return "";
    return `<div class="repsec-h">출구별 이용 분포 — 막대=실제, <span style="color:#F5A623">▏주황 눈금</span>=설계 기대</div>`
      + es.map((e) => {
          const a = e.m.actual_share * 100, d = (e.m.design_share || 0) * 100;
          const nm = (e.floor ? fn(e.floor) + " " : "") + e.m.exit_id;
          return `<div class="ebar"><span>${nm}</span>
            <div class="ebar-t"><i class="a" style="width:${a.toFixed(1)}%"></i><i class="d" style="left:${d.toFixed(1)}%"></i></div>
            <span class="ebar-v">${e.m.actual_count}명 · ${a.toFixed(0)}% (설계 ${d.toFixed(0)}%)</span></div>`;
        }).join("");
  }

  const REP_NOTE = `<div class="repnote">※ 등급(우수·보통·미흡)은 이해를 돕기 위한 표시 기준
    (SEI·EPFI 80/60점, CBS 0.5/10)이며 공식 판정 기준이 아닙니다. SEI·EPFI는 0~100으로 높을수록,
    CBS는 0에 가까울수록 좋고, IDR은 구역별 개시 여부와 반응 지연(초)으로 읽습니다.</div>`;

  function showResultModal() {
    if (!result) return;
    const r = result;
    const dur = r.ended_at ? (r.ended_at - r.alarm_ts) : 0;
    const zs = (r.zone_metrics || []).map((m) => ({ m, floor: null }));
    const es = (r.exit_metrics || []).map((m) => ({ m, floor: null }));
    const bs = (r.bottleneck_metrics || []).map((m) => ({ m, floor: null }));
    const started = zs.filter((z) => z.m.status === "started").length;
    const totOut = es.reduce((s, e) => s + (e.m.actual_count || 0), 0);
    const aos = (r.alarm_origins && r.alarm_origins.length) ? r.alarm_origins : [r.alarm_origin];
    $("resTitle").textContent = `평가 세션 결과 — ${r.session_id}`;
    $("resBody").innerHTML =
      repSummary({ sei: r.sei, epfi: r.epfi_avg, cbs: r.cbs_total,
                   zStarted: started, zTot: zs.length, totOut })
      + `<div class="repsec-h">개요</div>`
      + `<div class="reprows">`
        + repRow("경보 시각", fmtDT(r.alarm_ts))
        + repRow("평가 시간", `${Math.floor(dur / 60)}분 ${Math.floor(dur % 60)}초 (종료 ${r.ended_at ? hhmmss(r.ended_at) : "—"})`)
        + repRow("경보 발생원", aos.map((o, i) => `#${i + 1}(${Math.round(o[0])},${Math.round(o[1])})`).join(" · "))
        + repRow("출구 총 통과", `${totOut}명 · 출구 ${es.length}곳`)
        + repRow("추적 객체", `${(r.person_metrics || []).length}개`)
        + repRow("설정 버전", `calibration v${r.calibration_version} · config v${r.config_version}`)
      + `</div>`
      + `<div class="repsec-h">4대 지표 해석</div>`
      + repCards({ sei: r.sei, epfi: r.epfi_avg, cbs: r.cbs_total,
                   exits: es, persons: r.person_metrics || [], zones: zs, bns: bs })
      + exitBars(es)
      + REP_NOTE;
    $("resultModal").classList.remove("hidden");
  }

  /** 건물 드릴 롤업 리포트 (ADR 06 §3) — 종합 요약 + 지표 해석 카드 + 층별 상세. */
  function showDrillModal(roll) {
    if (!roll) return;
    const b = roll.building || {}, s = roll.summary || {};
    const fname = (f) => (typeof App !== "undefined" ? App.floorName(f) : f);
    // IDR — 구역별 유지(건물 단일평균 없음). 층 표기를 붙여 전 층 구역을 나열.
    const zones = [];
    Object.entries(b.idr_by_floor || {}).forEach(([f, zs]) =>
      (zs || []).forEach((z) => zones.push({ m: z, floor: f })));
    // 층별 결과를 층 표기와 함께 평탄화 — 해석 문장("가장 막힌 곳은 10F b2")용
    const exits = [], bns = [], persons = [];
    (roll.per_floor || []).forEach((pf) => {
      const r = pf.result || {};
      (r.exit_metrics || []).forEach((m) => exits.push({ m, floor: pf.floor_id }));
      (r.bottleneck_metrics || []).forEach((m) => bns.push({ m, floor: pf.floor_id }));
      (r.person_metrics || []).forEach((m) => persons.push(m));
    });
    const zStarted = zones.filter((z) => z.m.status === "started").length;
    const startTxt = Object.entries(s.floor_start_ts || {})
      .map(([f, ts]) => `${fname(f)} ${ts != null ? hhmmss(ts) : "—"}`).join(" · ") || "—";
    const perFloor = (roll.per_floor || []).map((pf) => {
      const r = pf.result || {};
      const passed = (r.exit_metrics || []).reduce((a, e) => a + (e.actual_count || 0), 0);
      const started = (r.zone_metrics || []).filter((z) => z.status === "started").length;
      return `<tr>
        <td>${fname(pf.floor_id)}</td>
        <td class="t-num">${r.sei != null ? fmt1(r.sei) : "—"}</td>
        <td class="t-num">${r.epfi_avg != null ? fmt1(r.epfi_avg) : "—"}</td>
        <td class="t-num">${fmt1(r.cbs_total || 0)}</td>
        <td class="t-num">${passed}</td>
        <td class="t-num">${started}/${(r.zone_metrics || []).length}</td>
      </tr>`;
    }).join("");
    $("resTitle").textContent = `건물 훈련 결과 리포트 — ${roll.session_id}`;
    $("resBody").innerHTML =
      repSummary({ sei: b.sei, epfi: b.epfi_avg, cbs: b.cbs_total,
                   zStarted, zTot: zones.length, totOut: s.total_passed })
      + `<div class="repsec-h">개요</div>`
      + `<div class="reprows">`
        + repRow("경보 시각", fmtDT(roll.alarm_ts))
        + repRow("참여 층", (roll.floors || []).map(fname).join(", "))
        + repRow("총 통과 인원", `${s.total_passed != null ? s.total_passed : 0}명 · 출구 ${exits.length}곳`)
        + repRow("최대 혼잡 층", s.max_cbs_floor ? fname(s.max_cbs_floor) : "—")
        + repRow("층별 개시시각", startTxt)
        + repRow("추적 객체", `${persons.length}개 (전 층)`)
      + `</div>`
      + `<div class="repsec-h">4대 지표 해석</div>`
      + repCards({ sei: b.sei, epfi: b.epfi_avg, cbs: b.cbs_total,
                   exits, persons, zones, bns, fname })
      + exitBars(exits, fname)
      + `<div class="drill-perfloor">
        <div class="drill-perfloor-h">층별 상세</div>
        <table class="drill-tbl">
          <thead><tr><th>층</th><th>SEI</th><th>EPFI</th><th>CBS</th><th>통과</th><th>IDR개시</th></tr></thead>
          <tbody>${perFloor}</tbody>
        </table>
      </div>`
      + REP_NOTE;
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
