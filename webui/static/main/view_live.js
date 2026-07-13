/* 화면 3: 운영 뷰 — SSE(/api/map/stream) 구독 → 맵 canvas 렌더 + 지표 패널.
 * 카메라 영상 표출 없음 — 클릭 시 스냅샷 1장 팝업. SSE 끊기면 폴링 폴백. */
"use strict";

var Views = window.Views || (window.Views = {});

Views.live = (() => {
  const $ = (id) => document.getElementById(id);
  let inited = false, active = false;
  let mc = null, es = null, watchdog = null;
  let state = null, lastMsg = 0;

  // ------------------------------------------------------------ 수신
  function connect() {
    disconnect();
    setConn("SSE 연결 중…", "");
    es = new EventSource(API.streamUrl());
    es.addEventListener("state", (ev) => {
      lastMsg = Date.now();
      setConn("LIVE (SSE 1초)", "ok");
      try { state = JSON.parse(ev.data); } catch (e) { return; }
      if (window.Session) Session.onState(state);   // 세션 카드 갱신 (v1.2)
      update();
    });
    es.onerror = () => setConn("SSE 재연결 중…", "err");
    // 워치독: 4초 이상 무수신이면 폴링 폴백으로 1회 채움
    watchdog = setInterval(async () => {
      if (!active || Date.now() - lastMsg < 4000) return;
      try {
        state = await API.getMapState();
        setConn("폴링 폴백", "err");
        update();
      } catch (e) { setConn("서버 응답 없음", "err"); }
    }, 3000);
  }

  function disconnect() {
    if (es) { es.close(); es = null; }
    if (watchdog) { clearInterval(watchdog); watchdog = null; }
  }

  function setConn(txt, cls) {
    const el = $("liveConn");
    el.textContent = txt;
    el.className = "conn " + cls;
  }

  // ------------------------------------------------------------ 렌더
  function overlay(g) {
    drawSiteElements(g, App.site, { state, showScale: false });
    drawAlarm(g);                                    // 🔔 경보 위치 마커
    if (!state) return;
    const { ctx, TX, TY } = g;
    state.objects.forEach((o) => {
      const x = TX(o.x), y = TY(o.y);
      const col = camColor(o.cam_id, App.cameras);
      if (o.vx || o.vy) {                            // 방향 벡터 (화면 px 고정 길이)
        const L = 16, ex = x + o.vx * L, ey = y + o.vy * L;
        ctx.strokeStyle = col; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(ex, ey); ctx.stroke();
        mcArrowHead(ctx, ex, ey, Math.atan2(o.vy, o.vx), 6, col);
      }
      ctx.fillStyle = col;
      ctx.beginPath(); ctx.arc(x, y, 4.5, 0, 7); ctx.fill();
      ctx.strokeStyle = "rgba(0,0,0,.55)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(x, y, 4.5, 0, 7); ctx.stroke();
      if (g.s > 0.7) {                               // 줌인 시 gid 라벨
        ctx.font = "10px Pretendard, sans-serif";
        ctx.fillStyle = "rgba(0,0,0,.7)";
        ctx.fillText(o.gid, x + 7, y + 4);
      }
    });
  }

  function drawAlarm(g) {
    const o = window.Session && Session.alarmOrigin();
    if (!o) return;
    const { ctx, TX, TY } = g;
    const x = TX(o[0]), y = TY(o[1]);
    ctx.strokeStyle = Session.isActive() ? "#ff5b5b" : "rgba(255,91,91,.45)";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, 10, 0, 7); ctx.stroke();
    ctx.beginPath(); ctx.arc(x, y, 3, 0, 7);
    ctx.fillStyle = ctx.strokeStyle; ctx.fill();
    ctx.font = "13px Pretendard, sans-serif";
    ctx.fillText("🔔", x + 12, y - 8);
  }

  function row(label, value, over) {
    return `<div class="metricrow${over ? " over" : ""}"><span>${label}</span><b class="t-num">${value}</b></div>`;
  }

  function nameOf(list, id) {
    const e = (list || []).find((x) => x.id === id);
    return (e && e.name) || id;
  }

  function updatePanels() {
    if (!state) return;
    $("mObjects").textContent = state.objects.length;
    $("mVersion").textContent = "v" + state.site_version;
    $("mTs").textContent = new Date(state.ts * 1000).toTimeString().slice(0, 8);

    const s = App.site;
    $("mZones").innerHTML = state.zones.length
      ? state.zones.map((z) => row(nameOf(s.zones, z.id),
          `${z.count}명${z.density != null ? ` · ${z.density}/m²` : ""}`)).join("")
      : `<div class="grow">구역 없음 — 맵 설정에서 추가</div>`;
    $("mBottlenecks").innerHTML = state.bottlenecks.length
      ? state.bottlenecks.map((b) => row(nameOf(s.bottlenecks, b.id) + (b.over ? " ⚠ 초과" : ""),
          `${b.count}명${b.density != null ? ` · ${b.density}/m²` : ""}`, b.over)).join("")
      : `<div class="grow">병목 없음</div>`;
    $("mExits").innerHTML = state.exits.length
      ? state.exits.map((e) => row(nameOf(s.exits, e.id),
          `IN ${e.in_count} · OUT ${e.out_count}`)).join("")
      : `<div class="grow">출입구 없음</div>`;
  }

  function renderCams() {
    if (!state) return;
    const box = $("liveCamList");
    box.innerHTML = "";
    if (!state.cameras.length) {
      box.innerHTML = `<div class="grow">카메라 없음 — ② 카메라 등록에서 추가</div>`;
      return;
    }
    const STATUS = {
      running: ["ok", "동작"], reconnecting: ["warn", "재접속"],
      disconnected: ["err", "끊김"], disabled: ["", "비활성"],
    };
    state.cameras.forEach((cs) => {
      const cfg = App.cameras.find((c) => c.cam_id === cs.cam_id) || {};
      const [cls, txt] = STATUS[cs.status] || ["", cs.status];
      const div = document.createElement("div");
      div.className = "camrow";
      div.title = "클릭: 스냅샷 보기";
      div.innerHTML = `
        <div class="r1"><span class="dotc" style="background:${camColor(cs.cam_id, App.cameras)}"></span>
          <span class="nm">${cfg.name || cs.cam_id}</span><span class="badge ${cls}">${txt}</span></div>
        <div class="r2"><span>${cs.cam_id}</span>
          <span class="t-num">${cs.fps_in.toFixed(1)} fps</span>
          <span class="t-num">drop ${cs.drops}</span></div>`;
      div.onclick = () => openSnapshot(cs.cam_id, cfg.name);
      box.appendChild(div);
    });
  }

  function openSnapshot(camId, name) {
    $("snapTitle").textContent = `${name || camId} — 스냅샷 (${new Date().toTimeString().slice(0, 8)})`;
    $("snapImg").src = API.snapshotUrl(camId);
    $("snapModal").classList.remove("hidden");
  }

  function update() {
    updatePanels();
    renderCams();
    if (mc) mc.render();
  }

  // ------------------------------------------------------------ lifecycle
  function init() {
    if (inited) return;
    inited = true;
    mc = new MapCanvas($("liveCv"), {
      draw: overlay,
      onClick: (p) => { if (window.Session) Session.placeAlarm(p); },  // 경보 위치 지정
    });
    if (window.Session) {
      Session.init({ onMapRender: () => { if (mc) mc.render(); } });
      Session.bootstrap();
    }
    $("snapClose").onclick = () => $("snapModal").classList.add("hidden");
    $("snapModal").onclick = (e) => { if (e.target === $("snapModal")) $("snapModal").classList.add("hidden"); };
  }

  function enter() {
    init();
    active = true;
    if (App.mapImg && App.site.map) mc.setImage(App.mapImg, App.site.map.w, App.site.map.h);
    else mc.setImage(null, 1000, 600);
    connect();
  }

  function leave() { active = false; disconnect(); }

  return { enter, leave };
})();
