/* 화면 2: 카메라 등록·매핑 — RTSP 등록 → 연결 테스트(첫 프레임) →
 * 좌(카메라 프레임)·우(맵) 대응점 4+쌍 + 유효 ROI → PUT mapping.
 * cctv_pts·valid_roi: 카메라 프레임 px / map_pts: 맵 원본 px. */
"use strict";

var Views = window.Views || (window.Views = {});

Views.cams = (() => {
  const $ = (id) => document.getElementById(id);
  let inited = false;
  let camMc = null, mapMc = null;
  let sel = null;                       // 선택된 cam_id
  let mode = "pair";                    // pair | roi | pan
  const frames = {};                    // cam_id -> {img, w, h}
  let cctvPts = [], mapPts = [], roi = [];

  const pairColor = (i) => `hsl(${(i * 47) % 360},80%,60%)`;

  // ------------------------------------------------------------ 목록
  function badge(cls, txt) { return `<span class="badge ${cls}">${txt}</span>`; }

  function renderList() {
    const box = $("camList");
    box.innerHTML = "";
    if (!App.cameras.length) {
      box.innerHTML = `<div class="grow">등록된 카메라가 없습니다. 아래에서 추가하세요.</div>`;
    }
    App.cameras.forEach((c) => {
      const div = document.createElement("div");
      div.className = "camrow" + (c.cam_id === sel ? " sel" : "");
      div.innerHTML = `
        <div class="r1"><span class="dotc" style="background:${camColor(c.cam_id, App.cameras)}"></span>
          <span class="nm">${c.name || c.cam_id}</span>
          ${c.mapping ? badge("ok", "매핑 ✓") : badge("warn", "매핑 필요")}
          ${c.enabled ? badge("cy", "활성") : badge("", "비활성")}
          <button class="del" title="삭제">🗑</button></div>
        <div class="r2"><span>${c.cam_id}</span><span class="rtsp">${c.rtsp}</span>
          <label style="cursor:pointer"><input type="checkbox" ${c.enabled ? "checked" : ""} /> 활성</label></div>`;
      div.onclick = (e) => { if (e.target.tagName !== "INPUT" && !e.target.classList.contains("del")) select(c.cam_id); };
      div.querySelector(".del").onclick = async (e) => {
        e.stopPropagation();
        if (!confirm(`${c.name || c.cam_id} 카메라를 삭제할까요?`)) return;
        try {
          await API.deleteCamera(c.cam_id);
          if (sel === c.cam_id) sel = null;
          await App.reloadCameras(); renderList(); renderSel();
        } catch (err) { alert("삭제 실패: " + err.message); }
      };
      div.querySelector("input[type=checkbox]").onchange = async (e) => {
        try {
          await API.updateCamera(c.cam_id, { enabled: e.target.checked });
          await App.reloadCameras(); renderList();
        } catch (err) { alert("변경 실패: " + err.message); }
      };
      box.appendChild(div);
    });
  }

  // ------------------------------------------------------------ 추가·테스트
  async function addCamera() {
    const name = $("newCamName").value.trim();
    const rtsp = $("newCamRtsp").value.trim();
    if (!rtsp) { $("camAddMsg").textContent = "RTSP 주소를 입력하세요."; return; }
    $("camAddMsg").textContent = "등록 중…";
    try {
      const cam = await API.addCamera({ name, rtsp });
      $("newCamName").value = ""; $("newCamRtsp").value = "";
      await App.reloadCameras();
      $("camAddMsg").textContent = `${cam.cam_id} 등록됨 — 연결 테스트 중…`;
      renderList();
      await select(cam.cam_id);                       // select가 test 수행
      $("camAddMsg").textContent = `${cam.cam_id} 등록·테스트 완료. 대응점을 지정하세요.`;
    } catch (e) { $("camAddMsg").textContent = "실패: " + e.message; }
  }

  async function testCamera(camId) {
    const r = await API.testCamera(camId);
    if (!r.ok) throw new Error("연결 실패");
    const img = new Image();
    await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = r.snapshot_b64; });
    frames[camId] = { img, w: r.width, h: r.height };
    return frames[camId];
  }

  // ------------------------------------------------------------ 선택·매핑
  async function select(camId) {
    sel = camId;
    const cam = App.cameras.find((c) => c.cam_id === camId);
    renderList();
    if (!cam) { renderSel(); return; }
    $("selCamLabel").textContent = `${cam.name || cam.cam_id} (${cam.cam_id})`;
    // 기존 매핑 복원
    cctvPts = cam.mapping ? cam.mapping.cctv_pts.map((p) => p.slice()) : [];
    mapPts = cam.mapping ? cam.mapping.map_pts.map((p) => p.slice()) : [];
    roi = cam.valid_roi ? cam.valid_roi.map((p) => p.slice()) : [];
    setMode("pair");
    try {
      if (!frames[camId]) { hint("연결 테스트 중…"); await testCamera(camId); }
      const f = frames[camId];
      camMc.setImage(f.img, f.w, f.h);
      hint();
    } catch (e) { hint("연결 테스트 실패: " + e.message, true); camMc.setImage(null, 640, 360); }
    if (App.mapImg && App.site.map) mapMc.setImage(App.mapImg, App.site.map.w, App.site.map.h);
    else mapMc.setImage(null, 1000, 600);
    renderSel();
  }

  function renderSel() { if (camMc) camMc.render(); if (mapMc) mapMc.render(); }

  function mPerPx() {
    const m = App.site && App.site.map;
    if (!m) return null;
    if (m.m_per_px != null) return m.m_per_px;
    if (m.scale) {
      const d = Math.hypot(m.scale.p2[0] - m.scale.p1[0], m.scale.p2[1] - m.scale.p1[1]);
      return d > 0 ? m.scale.meters / d : null;
    }
    return null;
  }

  function hint(msg, warn) {
    const el = $("camHint");
    if (msg !== undefined) { el.textContent = msg; el.classList.toggle("warn", !!warn); return; }
    el.classList.remove("warn");
    if (!sel) { el.textContent = "좌측에서 카메라를 선택하거나 추가하세요."; return; }
    if (mode === "roi") {
      el.textContent = `유효 ROI: 카메라 프레임에 polygon 꼭짓점을 클릭 (현재 ${roi.length}점 · 3점 이상, 비우면 전체 유효).`;
    } else if (mode === "pan") {
      el.textContent = "이동 모드 — 휠: 줌 · 드래그: 팬.";
    } else {
      const n = Math.min(cctvPts.length, mapPts.length);
      const next = cctvPts.length > mapPts.length ? "우측 맵" : "좌측 카메라 프레임";
      el.textContent = `대응점 ${n}쌍 지정됨 (최소 4쌍) — 다음: ${next}에 ${n + 1}번 점 클릭.`;
    }
  }

  function setMode(m) {
    mode = m;
    document.querySelectorAll("#camTools .tag-btn").forEach((b) =>
      b.classList.toggle("on", b.dataset.mode === m));
    hint();
    renderSel();
  }

  function onCamClick(p) {
    if (!sel) return;
    if (mode === "roi") { roi.push([p.x, p.y]); }
    else if (mode === "pair") {
      if (cctvPts.length > mapPts.length) { hint("우측 맵에 먼저 대응점을 찍으세요.", true); return; }
      cctvPts.push([p.x, p.y]);
    } else return;
    hint(); renderSel();
  }

  function onMapClick(p) {
    if (!sel || mode !== "pair") return;
    if (cctvPts.length <= mapPts.length) { hint("좌측 카메라 프레임에 먼저 점을 찍으세요.", true); return; }
    mapPts.push([p.x, p.y]);
    hint(); renderSel();
  }

  function undo() {
    if (mode === "roi") roi.pop();
    else if (cctvPts.length > mapPts.length) cctvPts.pop();
    else mapPts.pop();
    hint(); renderSel();
  }

  function clearAll() {
    if (mode === "roi") roi = [];
    else { cctvPts = []; mapPts = []; }
    hint(); renderSel();
  }

  async function saveMapping() {
    if (!sel) return;
    const n = Math.min(cctvPts.length, mapPts.length);
    if (n < 4 || cctvPts.length !== mapPts.length) {
      hint(`대응점이 최소 4쌍 필요합니다 (현재 카메라 ${cctvPts.length} · 맵 ${mapPts.length}).`, true);
      return;
    }
    if (roi.length > 0 && roi.length < 3) { hint("유효 ROI는 3점 이상이거나 비워야 합니다.", true); return; }
    try {
      hint("매핑 저장 중…");
      const saved = await API.putMapping(sel, { cctv_pts: cctvPts, map_pts: mapPts,
                                                valid_roi: roi.length >= 3 ? roi : null });
      await App.reloadCameras();
      renderList();
      // 대응점별 재투영 오차(m) 품질 표시 — 큰 점은 바닥 아님/오지정 의심 (v1.5)
      const errs = saved.mapping && saved.mapping.reproj_err_px;
      const mpp = mPerPx();
      if (errs && errs.length && mpp) {
        const em = errs.map((e) => e * mpp);
        const worst = Math.max(...em);
        const detail = em.map((e, i) => `#${i + 1} ${e.toFixed(2)}m`).join(" · ");
        hint(`저장됨 (${n}쌍${roi.length >= 3 ? " + ROI" : ""}) — 점별 오차: ${detail}` +
             (worst > 0.5 ? ` · ⚠ 최대 ${worst.toFixed(2)}m — 오차 큰 점은 바닥 아닌 지점일 수 있음, 빼고 재지정 권장` : " ✓"),
             worst > 0.5);
      } else {
        hint(`저장됨 — 호모그래피 H 산출 완료 (${n}쌍${roi.length >= 3 ? " + ROI" : ""}).`);
      }
    } catch (e) { hint("저장 실패: " + e.message, true); }
  }

  // ------------------------------------------------------------ overlays
  function camOverlay(g) {
    const { ctx } = g;
    if (roi.length) {
      mcPath(g, roi, true);
      ctx.fillStyle = "rgba(48,220,251,.12)"; ctx.fill();
      ctx.strokeStyle = MC_COLORS.roi; ctx.lineWidth = 2; ctx.setLineDash([5, 4]);
      ctx.stroke(); ctx.setLineDash([]);
      if (mode === "roi") mcNumbered(g, roi, MC_COLORS.roi);
    }
    mcNumbered(g, cctvPts, null, pairColor);
  }

  function mapOverlay(g) {
    drawSiteElements(g, App.site, { faint: true });
    mcNumbered(g, mapPts, null, pairColor);
  }

  // ------------------------------------------------------------ lifecycle
  function init() {
    if (inited) return;
    inited = true;
    camMc = new MapCanvas($("camFrameCv"), { onClick: onCamClick, draw: camOverlay });
    mapMc = new MapCanvas($("camMapCv"), { onClick: onMapClick, draw: mapOverlay });
    document.querySelectorAll("#camTools .tag-btn").forEach((b) =>
      b.onclick = () => setMode(b.dataset.mode));
    $("camAdd").onclick = addCamera;
    $("pairUndo").onclick = undo;
    $("pairClear").onclick = clearAll;
    $("mappingSave").onclick = saveMapping;
    $("camRetest").onclick = async () => {
      if (!sel) return;
      try {
        hint("연결 테스트 중…");
        const f = await testCamera(sel);
        camMc.setImage(f.img, f.w, f.h);
        hint(`연결 성공 — ${f.w}×${f.h}.`);
      } catch (e) { hint("연결 테스트 실패: " + e.message, true); }
    };
  }

  function enter() {
    init();
    renderList();
    if (sel) select(sel);
    else {
      $("selCamLabel").textContent = "카메라를 선택하세요";
      if (App.mapImg && App.site.map) mapMc.setImage(App.mapImg, App.site.map.w, App.site.map.h);
      hint();
    }
  }

  return { enter, leave: () => {}, renderList };
})();
