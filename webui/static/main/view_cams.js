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

  // 사이트 전역 min_conf (카메라가 오버라이드 안 하면 이 값 상속). 기본 0.5.
  function siteMinConf() {
    const t = App.site && App.site.thresholds;
    return (t && t.min_conf != null) ? t.min_conf : 0.5;
  }

  // H 행렬 적용 (9원소 row-major, 카메라→맵)
  function applyH(H9, x, y) {
    const w = H9[6]*x + H9[7]*y + H9[8];
    if (Math.abs(w) < 1e-10) return null;
    return [(H9[0]*x + H9[1]*y + H9[2])/w, (H9[3]*x + H9[4]*y + H9[5])/w];
  }

  function invertH3x3(H9) {
    const [a,b,c,d,e,f,g,h,i] = H9;
    const det = a*(e*i-f*h) - b*(d*i-f*g) + c*(d*h-e*g);
    if (Math.abs(det) < 1e-12) return null;
    return [(e*i-f*h)/det,(c*h-b*i)/det,(b*f-c*e)/det,
            (f*g-d*i)/det,(a*i-c*g)/det,(c*d-a*f)/det,
            (d*h-e*g)/det,(b*g-a*h)/det,(a*e-b*d)/det];
  }

  function getCamH() {
    const cam = App.cameras.find((c) => c.cam_id === sel);
    return cam && cam.mapping ? cam.mapping.H : null;
  }

  // 현재 마우스 (맵 원본 px) — hover crosshair용
  let hoverCam = null;  // {x,y} 카메라 px
  let hoverMap = null;  // {x,y} 맵 px

  // 컨벡스 헐 (Graham scan)
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

  // ------------------------------------------------------------ 목록
  function badge(cls, txt) { return `<span class="badge ${cls}">${txt}</span>`; }

  function renderList() {
    const box = $("camList");
    box.innerHTML = "";
    // 목록 헤더에 사이트 전역 기본 conf 표시 (상속 기준값)
    const hdr = $("camListDefConf");
    if (hdr) hdr.textContent = `기본 conf ${siteMinConf()}`;
    if (!App.cameras.length) {
      box.innerHTML = `<div class="grow">등록된 카메라가 없습니다. 아래에서 추가하세요.</div>`;
    }
    const def = siteMinConf();
    App.cameras.forEach((c) => {
      const div = document.createElement("div");
      div.className = "camrow" + (c.cam_id === sel ? " sel" : "");
      const overridden = c.min_conf != null;
      const effVal = overridden ? c.min_conf : def;   // 입력창에 실효값을 채워둠(A안)
      div.innerHTML = `
        <div class="r1"><span class="dotc" style="background:${camColor(c.cam_id, App.cameras)}"></span>
          <span class="nm">${c.name || c.cam_id}</span>
          ${c.mapping ? badge("ok", "매핑 ✓") : badge("warn", "매핑 필요")}
          ${c.enabled ? badge("cy", "활성") : badge("", "비활성")}
          <button class="del" title="삭제">🗑</button></div>
        <div class="r2"><span>${c.cam_id}</span><span class="rtsp">${c.rtsp}</span>
          <label style="cursor:pointer"><input type="checkbox" class="en" ${c.enabled ? "checked" : ""} /> 활성</label></div>
        <div class="r3">
          <span class="cflab" title="이 카메라의 최소 검출 신뢰도. 값을 지우고 적용하면 기본값(${def}) 상속.">검출 신뢰도</span>
          <input type="number" class="cfin" min="0" max="1" step="0.05" value="${effVal}" placeholder="기본 ${def}" />
          <button class="tag-btn cfap" title="이 카메라에 적용">적용</button>
          <span class="cfst">${overridden ? "오버라이드" : "기본값 상속"}</span>
        </div>`;
      // 행 선택 (입력/버튼 클릭은 제외)
      div.onclick = (e) => {
        const t = e.target;
        if (t.tagName === "INPUT" || t.tagName === "BUTTON" ||
            t.classList.contains("cflab") || t.classList.contains("cfst")) return;
        select(c.cam_id);
      };
      div.querySelector(".del").onclick = async (e) => {
        e.stopPropagation();
        if (!confirm(`${c.name || c.cam_id} 카메라를 삭제할까요?`)) return;
        try {
          await API.deleteCamera(c.cam_id);
          if (sel === c.cam_id) sel = null;
          await App.reloadCameras(); renderList(); renderSel();
        } catch (err) { alert("삭제 실패: " + err.message); }
      };
      div.querySelector("input.en").onchange = async (e) => {
        try {
          await API.updateCamera(c.cam_id, { enabled: e.target.checked });
          await App.reloadCameras(); renderList();
        } catch (err) { alert("변경 실패: " + err.message); }
      };
      // conf 인라인 적용 — 값 있으면 오버라이드, 비우면 null(상속). 0~1 검증.
      div.querySelector(".cfap").onclick = (e) => {
        e.stopPropagation();
        applyMinConf(c.cam_id, div.querySelector(".cfin"));
      };
      div.querySelector(".cfin").onkeydown = (e) => {
        if (e.key === "Enter") { e.preventDefault(); applyMinConf(c.cam_id, e.target); }
      };
      box.appendChild(div);
    });
  }

  // 카메라 행 인라인 conf 적용 (버튼/Enter로만 저장 — 실시간 저장 안 함).
  async function applyMinConf(camId, inputEl) {
    const raw = inputEl.value.trim();
    let val = null;                                    // 비움 = 사이트값 상속
    if (raw !== "") {
      val = Number(raw);
      if (!isFinite(val) || val < 0 || val > 1) {
        alert("검출 신뢰도는 0~1 사이 값이거나 비워야 합니다 (비우면 기본값 상속).");
        return;
      }
    }
    inputEl.disabled = true;
    try {
      await API.updateCamera(camId, { min_conf: val });
      await App.reloadCameras();
      renderList();
    } catch (err) {
      alert("검출 신뢰도 저장 실패: " + err.message);
      inputEl.disabled = false;
    }
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
    // min_conf는 카메라 목록 행에서 인라인 편집(renderList) — 여기선 매핑만.
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
  function drawCrosshair(g, px, py, col, label) {
    const { ctx, TX, TY } = g;
    const cx = TX(px), cy = TY(py);
    ctx.save();
    ctx.strokeStyle = col; ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(cx-14, cy); ctx.lineTo(cx+14, cy); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(cx, cy-14); ctx.lineTo(cx, cy+14); ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.arc(cx, cy, 5, 0, Math.PI*2);
    ctx.fillStyle = col; ctx.globalAlpha = 0.85; ctx.fill();
    if (label) {
      ctx.globalAlpha = 1; ctx.fillStyle = col;
      ctx.font = "11px Pretendard,sans-serif";
      ctx.fillText(label, cx+8, cy-7);
    }
    ctx.restore();
  }

  function camOverlay(g) {
    const { ctx, TX, TY } = g;
    // 실제 탐지 영역을 백엔드 projector 규칙과 동일하게 하나만 표시:
    //   valid_roi 지정 시 그 다각형(점 순서 그대로, 오목 가능),
    //   미지정 시 대응점 볼록껍질(그게 실제 탐지 영역이므로).
    // ROI 편집 모드에선 편집 중인 roi에 꼭짓점 번호도 함께 표시.
    const area = roi.length >= 3 ? roi
               : (mode !== "roi" && cctvPts.length >= 3 ? convexHull(cctvPts) : null);
    if (area) {
      mcPath(g, area, true);
      ctx.fillStyle = "rgba(48,220,251,.11)"; ctx.fill();
      ctx.strokeStyle = MC_COLORS.roi; ctx.lineWidth = mode === "roi" ? 2 : 1.5;
      ctx.setLineDash([5, 4]); ctx.stroke(); ctx.setLineDash([]);
      if (mode === "roi") mcNumbered(g, roi, MC_COLORS.roi);
    }
    mcNumbered(g, cctvPts, null, pairColor);
    // hover crosshair: 맵에서 마우스 올렸을 때 역투영 위치 표시
    if (hoverMap) {
      const H9 = getCamH();
      if (H9) {
        const Hinv = invertH3x3(H9);
        if (Hinv) {
          const p = applyH(Hinv, hoverMap.x, hoverMap.y);
          if (p) drawCrosshair(g, p[0], p[1], "#ff6ef7", `맵(${hoverMap.x.toFixed(0)},${hoverMap.y.toFixed(0)})`);
        }
      }
    }
  }

  function mapOverlay(g) {
    drawSiteElements(g, App.site, { faint: true });
    mcNumbered(g, mapPts, null, pairColor);
    // hover crosshair: 카메라에서 마우스 올렸을 때 순방향 투영 위치 표시
    if (hoverCam) {
      const H9 = getCamH();
      if (H9) {
        const p = applyH(H9, hoverCam.x, hoverCam.y);
        if (p) drawCrosshair(g, p[0], p[1], "#ff6ef7", `cam(${hoverCam.x.toFixed(0)},${hoverCam.y.toFixed(0)})`);
      }
    }
  }

  // ------------------------------------------------------------ lifecycle
  function init() {
    if (inited) return;
    inited = true;
    camMc = new MapCanvas($("camFrameCv"), { onClick: onCamClick, draw: camOverlay });
    mapMc = new MapCanvas($("camMapCv"), { onClick: onMapClick, draw: mapOverlay });
    // hover crosshair — 한쪽 캔버스 마우스 → 반대 캔버스에 투영점 표시
    $("camFrameCv").addEventListener("mousemove", (e) => {
      if (!getCamH()) return;
      const p = camMc && camMc.toMap(e);
      hoverCam = p ? { x: p.x, y: p.y } : null;
      hoverMap = null;
      if (mapMc) mapMc.render();
    });
    $("camFrameCv").addEventListener("mouseleave", () => { hoverCam = null; if (mapMc) mapMc.render(); });
    $("camMapCv").addEventListener("mousemove", (e) => {
      if (!getCamH()) return;
      const p = mapMc && mapMc.toMap(e);
      hoverMap = p ? { x: p.x, y: p.y } : null;
      hoverCam = null;
      if (camMc) camMc.render();
    });
    $("camMapCv").addEventListener("mouseleave", () => { hoverMap = null; if (camMc) camMc.render(); });
    document.querySelectorAll("#camTools .tag-btn").forEach((b) =>
      b.onclick = () => setMode(b.dataset.mode));
    $("camAdd").onclick = addCamera;
    $("pairUndo").onclick = undo;
    $("pairClear").onclick = clearAll;
    $("mappingSave").onclick = saveMapping;
    // min_conf는 카메라 목록 행 인라인 편집(renderList의 applyMinConf)으로 이동.
    $("hullRoi").onclick = () => {
      if (cctvPts.length < 3) { hint("대응점 3개 이상 필요합니다.", true); return; }
      roi = convexHull(cctvPts);
      setMode("roi");
      hint(`커버리지 ROI 자동 설정 완료 (${roi.length}점) — 확인 후 매핑 저장.`);
      renderSel();
    };
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
