/* 화면 3: 운영 뷰 — SSE(/api/map/stream) 구독 → 맵 canvas 렌더 + 지표 패널.
 * 카메라 영상 표출 없음 — 클릭 시 스냅샷 1장 팝업. SSE 끊기면 폴링 폴백. */
"use strict";

var Views = window.Views || (window.Views = {});

Views.live = (() => {
  const $ = (id) => document.getElementById(id);
  let inited = false, active = false;
  let mc = null, es = null, watchdog = null;
  let state = null, lastMsg = 0;
  let showGraph = false;                 // IDR 공간그래프 표시 토글
  let showHulls = false;                 // 카메라 매핑 커버리지 다각형 표시
  let selGid = null;                     // 객체 목록에서 선택된 gid (맵 하이라이트)
  let objSort = "dev";                   // 객체 정렬: dev(이탈)|speed|dwell

  // ---- 클라이언트 사이드 위치 보간 (선형 보간, T_n-1 → T_n)
  let prevObjects = {};      // {gid: {x,y}} — T_n-1 위치
  let currObjects = {};      // {gid: {x,y}} — T_n 위치 (최신 SSE)
  let interpStart = 0;       // T_n 도착 시각 (performance.now())
  let interpDuration = 1000; // 측정된 SSE 간격 (ms), 수신 시마다 갱신
  let lastSseTime = 0;       // 이전 SSE 도착 시각
  let rafId = null;          // requestAnimationFrame handle
  const renderFps = () => Math.min(30, Math.max(1, parseInt($("fpsInput").value) || 20));

  // ------------------------------------------------------------ 수신
  function connect() {
    disconnect();
    setConn("SSE 연결 중…", "");
    es = new EventSource(API.streamUrl());
    es.addEventListener("state", (ev) => {
      lastMsg = Date.now();
      let newState;
      try { newState = JSON.parse(ev.data); } catch (e) { return; }

      // 보간 타이밍 갱신
      const now = performance.now();
      if (lastSseTime > 0)
        interpDuration = Math.max(200, Math.min(3000, now - lastSseTime));
      lastSseTime = now;
      interpStart = now;

      // T_n-1 ← T_n, T_n ← 새 수신
      prevObjects = {...currObjects};
      currObjects = {};
      (newState.objects || []).forEach((o) => { currObjects[o.gid] = {x: o.x, y: o.y}; });

      state = newState;
      setConn(`LIVE · ${renderFps()}fps 보간`, "ok");
      if (window.Session) Session.onState(state);   // 대시보드는 T_n 즉시 업데이트
      updatePanels(); renderCams(); renderObjects();
      // ★ mc.render()는 RAF 루프가 담당
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

  // ------------------------------------------------------------ 커버리지 헐
  function convexHull(pts) {
    if (pts.length <= 2) return pts.slice();
    const s = [...pts].sort((a, b) => a[0] !== b[0] ? a[0] - b[0] : a[1] - b[1]);
    const cross = (O, A, B) => (A[0]-O[0])*(B[1]-O[1]) - (A[1]-O[1])*(B[0]-O[0]);
    const lo = [], hi = [];
    for (const p of s) {
      while (lo.length >= 2 && cross(lo[lo.length-2], lo[lo.length-1], p) <= 0) lo.pop();
      lo.push(p);
    }
    for (let i = s.length-1; i >= 0; i--) {
      const p = s[i];
      while (hi.length >= 2 && cross(hi[hi.length-2], hi[hi.length-1], p) <= 0) hi.pop();
      hi.push(p);
    }
    hi.pop(); lo.pop();
    return lo.concat(hi);
  }

  function drawCameraHulls(g) {
    const { ctx, TX, TY } = g;
    App.cameras.forEach((cam) => {
      const m = cam.mapping;
      if (!m || !m.map_pts || m.map_pts.length < 4) return;
      const hull = convexHull(m.map_pts);
      if (hull.length < 3) return;
      const col = camColor(cam.cam_id, App.cameras);
      ctx.save();
      // 채우기 — 옅은 배경
      ctx.beginPath();
      hull.forEach(([x, y], i) => i === 0 ? ctx.moveTo(TX(x), TY(y)) : ctx.lineTo(TX(x), TY(y)));
      ctx.closePath();
      ctx.fillStyle = col;
      ctx.globalAlpha = 0.06;
      ctx.fill();
      // 외곽선 — 점선
      ctx.globalAlpha = 0.7;
      ctx.strokeStyle = col;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([7, 5]);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.restore();
      // 대응점 마커 (맵 측) — 작은 다이아몬드
      ctx.save();
      m.map_pts.forEach(([x, y], i) => {
        const cx = TX(x), cy = TY(y), r = 4;
        ctx.fillStyle = col;
        ctx.globalAlpha = 0.85;
        ctx.beginPath();
        ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r, cy);
        ctx.lineTo(cx, cy + r); ctx.lineTo(cx - r, cy);
        ctx.closePath();
        ctx.fill();
        ctx.strokeStyle = "rgba(0,0,0,.5)"; ctx.lineWidth = 0.8; ctx.globalAlpha = 1;
        ctx.stroke();
        if (g.s > 0.6) {
          ctx.fillStyle = "#fff"; ctx.globalAlpha = 0.9;
          ctx.font = "9px Pretendard,sans-serif";
          ctx.fillText(i + 1, cx + 5, cy - 4);
        }
      });
      ctx.restore();
    });
  }

  // ------------------------------------------------------------ 렌더
  function overlay(g) {
    drawSiteElements(g, App.site, { state, showScale: false });
    if (showGraph) drawGraph(g, App.site.graph, { faint: true });
    if (showHulls) drawCameraHulls(g);               // 카메라별 매핑 커버리지
    drawAlarm(g);                                    // 🔔 경보 위치 마커
    if (!state) return;
    const { ctx, TX, TY } = g;
    const alpha = Math.min(1, interpDuration > 0
      ? (performance.now() - interpStart) / interpDuration : 1);
    state.objects.forEach((o) => {
      const prev = prevObjects[o.gid];
      const rx = prev ? prev.x + (o.x - prev.x) * alpha : o.x;
      const ry = prev ? prev.y + (o.y - prev.y) * alpha : o.y;
      const x = TX(rx), y = TY(ry);
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
      if (o.gid === selGid) {                        // 목록 선택 객체 하이라이트 링
        ctx.strokeStyle = "#fff"; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(x, y, 10, 0, 7); ctx.stroke();
        ctx.strokeStyle = col; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(x, y, 13, 0, 7); ctx.stroke();
      }
      if (g.s > 0.7) {                               // 줌인 시 gid 라벨
        ctx.font = "10px Pretendard, sans-serif";
        ctx.fillStyle = "rgba(0,0,0,.7)";
        ctx.fillText(o.gid, x + 7, y + 4);
      }
    });
  }

  function drawAlarm(g) {
    if (!window.Session) return;
    const isActive = Session.isActive();
    const origins = isActive
      ? Session.alarmOrigins()
      : Session.pendingAlarmOrigins();
    const { ctx, TX, TY } = g;

    if (origins && origins.length) {
      const stroke = isActive ? "#ff5b5b" : "rgba(255,91,91,.65)";
      origins.forEach((o, i) => {
        const x = TX(o[0]), y = TY(o[1]);
        ctx.strokeStyle = stroke;
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(x, y, 10, 0, 7); ctx.stroke();
        ctx.beginPath(); ctx.arc(x, y, 3, 0, 7);
        ctx.fillStyle = stroke; ctx.fill();
        ctx.font = "13px Pretendard, sans-serif";
        ctx.fillText(origins.length > 1 ? `🔔${i + 1}` : "🔔", x + 12, y - 8);
      });
    }

    // 추가 모드 안내 오버레이
    if (!isActive && Session.isPlacing()) {
      const W = g.mc.cw, H = g.mc.ch;
      ctx.save();
      ctx.strokeStyle = "#ff5b5b";
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(2, 2, W - 4, H - 4);
      ctx.setLineDash([]);
      ctx.font = "bold 13px Pretendard, sans-serif";
      ctx.fillStyle = "#ff7b7b";
      ctx.textAlign = "center";
      ctx.fillText("맵 클릭 → 경보 발생원 추가", W / 2, 22);
      ctx.textAlign = "left";
      ctx.restore();
    }
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

  // ------------------------------------------------------------ 객체 목록 (v1.5)
  const SORTS = { dev: "이탈↓", speed: "속도↓", dwell: "체류↓" };
  const alignDot = (a) => a == null ? "—"
    : `<i class="adot" style="background:${a >= 0.7 ? "#3FB950" : (a >= 0.2 ? "#F5A623" : "#FF5B5B")}"></i>`;

  function renderObjects() {
    if (!state) return;
    const inSess = !!state.session;
    $("objCnt").textContent = state.objects.length;
    $("objHead").innerHTML = inSess
      ? `<span>객체</span><span>㎧</span><span>정렬</span><span>EPFI</span><span>이탈m</span>`
      : `<span>객체</span><span>㎧</span><span>정렬</span><span>체류s</span><span>구역</span>`;
    const key = { dev: (o) => o.dev_m != null ? o.dev_m : -1,
                  speed: (o) => o.speed_mps != null ? o.speed_mps : -1,
                  dwell: (o) => o.dwell_sec != null ? o.dwell_sec : -1 }[objSort];
    const rows = [...state.objects].sort((a, b) => key(b) - key(a));
    const fmt = (v, d) => v == null ? "—" : v.toFixed(d);
    $("objList").innerHTML = rows.map((o) => {
      const col = camColor(o.cam_id, App.cameras);
      const badge = o.exited ? ` <span class="objbadge out">↦${o.exited}</span>`
        : (o.evac_ok ? ` <span class="objbadge ok">피난중</span>` : "");
      const tail = inSess
        ? `<span class="t-num">${o.epfi_live != null ? Math.round(o.epfi_live) : "—"}</span>
           <span class="t-num">${fmt(o.dev_m, 1)}</span>`
        : `<span class="t-num">${fmt(o.dwell_sec, 0)}</span>
           <span>${o.zone_id || "—"}</span>`;
      return `<div class="objrow${o.gid === selGid ? " sel" : ""}" data-gid="${o.gid}">
        <span class="onm"><i class="dotc" style="background:${col}"></i>${o.gid}${badge}</span>
        <span class="t-num">${fmt(o.speed_mps, 1)}</span>
        <span>${alignDot(o.align)}</span>${tail}</div>`;
    }).join("");
    $("objList").querySelectorAll(".objrow").forEach((el) => {
      el.onclick = () => {
        selGid = selGid === el.dataset.gid ? null : el.dataset.gid;
        renderObjects();
        if (mc) mc.render();
      };
    });
  }

  function update() {
    updatePanels();
    renderCams();
    renderObjects();
    // ★ mc.render()는 RAF 루프 담당 — 폴링 폴백 시에도 RAF가 처리
  }

  // ---- RAF 보간 렌더 루프
  function startRenderLoop() {
    stopRenderLoop();
    let lastFrame = 0;
    function loop(ts) {
      if (!active) { rafId = null; return; }
      rafId = requestAnimationFrame(loop);
      if (ts - lastFrame < 1000 / renderFps()) return;
      lastFrame = ts;
      if (mc) mc.render();
    }
    rafId = requestAnimationFrame(loop);
  }

  function stopRenderLoop() {
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
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
    $("graphToggle").onclick = () => {
      showGraph = !showGraph;
      $("graphToggle").classList.toggle("on", showGraph);
      if (mc) mc.render();
    };
    $("hullToggle").onclick = () => {
      showHulls = !showHulls;
      $("hullToggle").classList.toggle("on", showHulls);
      if (mc) mc.render();
    };
    // fpsInput — 값 변경 즉시 반영 (RAF 루프가 다음 프레임에 적용)
    $("fpsInput").oninput = () => setConn(`LIVE · ${renderFps()}fps 보간`, "ok");
    $("objSort").onclick = () => {                 // 객체 목록 정렬 전환
      const keys = Object.keys(SORTS);
      objSort = keys[(keys.indexOf(objSort) + 1) % keys.length];
      $("objSort").textContent = SORTS[objSort];
      renderObjects();
    };
    $("snapClose").onclick = () => $("snapModal").classList.add("hidden");
    $("snapModal").onclick = (e) => { if (e.target === $("snapModal")) $("snapModal").classList.add("hidden"); };
  }

  function enter() {
    init();
    active = true;
    if (App.mapImg && App.site.map) mc.setImage(App.mapImg, App.site.map.w, App.site.map.h);
    else mc.setImage(null, 1000, 600);
    connect();
    startRenderLoop();
  }

  function leave() {
    active = false;
    disconnect();
    stopRenderLoop();
  }

  return { enter, leave };
})();
