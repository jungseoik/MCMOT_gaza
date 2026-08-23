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
  let showAllCov = false;
  // 출입구 화면 통과선 편집 — {exit_id, pts:[2점], inside} (선택 기능)
  let exLineExit = null, exLinePts = [], exLineInside = null;
  let exKind = "line", exZonePts = [];      // "line" | "zone" (영역 다각형)        // 전 카메라 대응점·커버영역 표시
  let selFloor = "default";             // 선택 카메라가 매핑될 층 id
  const mapImages = {};                 // floor_id -> Image (층별 맵 캐시)

  const multiFloor = () => ((App.site && App.site.floors) || []).length > 1;
  const selFloorObj = () => ((App.site && App.site.floors) || [])
    .find((f) => f.id === selFloor) || (App.site && App.floor) || null;

  const pairColor = (i) => `hsl(${(i * 47) % 360},80%,60%)`;

  // 사이트 전역 min_conf (카메라가 오버라이드 안 하면 이 값 상속). 기본 0.5.
  function siteMinConf() {
    const t = App.site && App.site.thresholds;
    return (t && t.min_conf != null) ? t.min_conf : 0.35;  // 스키마 기본과 통일
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

  // ------------------------------------------------------------ 층(floor)
  function floorOptions(selected) {
    return ((App.site && App.site.floors) || []).map((f) =>
      `<option value="${f.id}"${f.id === selected ? " selected" : ""}>${f.name || f.id}</option>`
    ).join("");
  }

  function renderFloorDropdowns() {
    const multi = multiFloor();
    const nc = $("newCamFloor"), ncl = $("newCamFloorLab");
    if (nc) { nc.innerHTML = floorOptions(nc.value || App.currentFloor); }
    if (ncl) ncl.classList.toggle("hidden", !multi);
    const bc = $("bulkFloor"), bcl = $("bulkFloorLab");
    if (bc) { bc.innerHTML = floorOptions(bc.value || App.currentFloor); }
    if (bcl) bcl.classList.toggle("hidden", !multi);
    const mf = $("mapFloorSel"), mfl = $("mapFloorLab");
    if (mf) mf.innerHTML = floorOptions(selFloor);
    if (mfl) mfl.classList.toggle("hidden", !(multi && sel));
  }

  // 선택 카메라가 매핑될 층의 맵 이미지를 mapMc에 로드 (층별 캐시)
  async function loadSelFloorMap() {
    const fl = selFloorObj();
    if (!fl || !fl.map) { mapMc.setImage(null, 1000, 600); return; }
    if (selFloor === App.currentFloor && App.mapImg) {
      mapMc.setImage(App.mapImg, fl.map.w, fl.map.h); return;
    }
    if (mapImages[selFloor]) { mapMc.setImage(mapImages[selFloor], fl.map.w, fl.map.h); return; }
    try {
      const img = new Image();
      await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = API.mapImageUrl(selFloor); });
      mapImages[selFloor] = img;
      mapMc.setImage(img, fl.map.w, fl.map.h);
    } catch (e) { mapMc.setImage(null, fl.map.w || 1000, fl.map.h || 600); }
  }

  // ------------------------------------------------------------ 목록
  function badge(cls, txt) { return `<span class="badge ${cls}">${txt}</span>`; }

  /** RTSP는 앞부분이 전부 같다(rtsp://127.0.0.1:8554/…) — 구분되는 건 끝의
   *  스트림 이름이므로 그쪽을 보여준다. 전체 주소는 title 로. */
  function rtspTail(url) {
    if (!url) return "";
    const m = String(url).split("?")[0].replace(/\/+$/, "");
    const seg = m.slice(m.lastIndexOf("/") + 1);
    return seg || m;
  }

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
    // 층별 소제목 — 12대쯤 되면 목록만 봐선 어느 층 것인지 분간이 안 된다.
    // 층이 하나뿐이면(단일 도면) 넣지 않는다.
    let lastFloor = null;
    App.cameras.forEach((c) => {
      const fid = c.floor_id || "default";
      if (multiFloor() && fid !== lastFloor) {
        lastFloor = fid;
        const n = App.cameras.filter((x) => (x.floor_id || "default") === fid).length;
        const h = document.createElement("div");
        h.className = "camgrp";
        h.textContent = `${App.floorName(fid)} · ${n}대`;
        box.appendChild(h);
      }
      const div = document.createElement("div");
      div.className = "camrow" + (c.cam_id === sel ? " sel" : "");
      const overridden = c.min_conf != null;
      const effVal = overridden ? c.min_conf : def;   // 입력창에 실효값을 채워둠(A안)
      // 층은 그룹 소제목에 이미 있으므로 행마다 배지로 또 달지 않는다.
      // 이름이 먼저다 — 예전엔 배지 4개가 폭을 다 먹어 이름이 "1..."로 눌렸다.
      // 층·활성 배지는 아랫줄로 내리고, 활성은 체크박스와 중복이라 배지를 뺐다.
      div.innerHTML = `
        <div class="r1"><span class="dotc" style="background:${camColor(c.cam_id, App.cameras)}"></span>
          <span class="nm" title="${c.name || c.cam_id}">${c.name || c.cam_id}</span>
          ${c.mapping ? badge("ok", "매핑 ✓") : badge("warn", "매핑 필요")}
          <button class="del" title="삭제">🗑</button></div>
        <div class="r2"><span class="cid">${c.cam_id}</span>
          <span class="rtsp" title="${c.rtsp}">${rtspTail(c.rtsp)}</span>
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
    renderEnableMapped();
  }

  /** 매핑은 끝났는데 비활성인 카메라 — 있을 때만 일괄 활성화 버튼을 보여준다. */
  const mappedDisabled = () =>
    App.cameras.filter((c) => c.mapping && !c.enabled);

  function renderEnableMapped() {
    const btn = $("camEnableMapped");
    if (!btn) return;
    const n = mappedDisabled().length;
    btn.classList.toggle("hidden", n === 0);
    btn.textContent = `▶ 매핑 완료 ${n}대 전부 활성화`;
  }

  async function enableMapped() {
    const targets = mappedDisabled();
    if (!targets.length) return;
    const btn = $("camEnableMapped");
    btn.disabled = true;
    btn.textContent = `${targets.length}대 활성화 중… (워커 재시작 1회)`;
    try {
      await API.updateCameras({
        cameras: targets.map((c) => ({ cam_id: c.cam_id, enabled: true })),
      });
      await App.reloadCameras();
      renderList();
      $("camAddMsg").textContent = `${targets.length}대 활성화 완료.`;
    } catch (e) {
      alert("활성화 실패: " + e.message);
    } finally {
      btn.disabled = false;
      renderEnableMapped();
    }
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
      // 층은 POST에 함께 실어 보낸다 — 예전처럼 PUT으로 따로 지정하면
      // deepstream 워커가 한 번 더 재시작돼 등록 비용이 두 배가 된다.
      const fl = ($("newCamFloor") && $("newCamFloor").value) || "default";
      const body = { name, rtsp };
      if (fl && fl !== "default") body.floor_id = fl;
      const cam = await API.addCamera(body);
      $("newCamName").value = ""; $("newCamRtsp").value = "";
      await App.reloadCameras();
      $("camAddMsg").textContent = `${cam.cam_id} 등록됨 — 연결 테스트 중…`;
      renderList();
      await select(cam.cam_id);                       // select가 test 수행
      $("camAddMsg").textContent = `${cam.cam_id} 등록·테스트 완료. 대응점을 지정하세요.`;
    } catch (e) { $("camAddMsg").textContent = "실패: " + e.message; }
  }

  // ------------------------------------------------- 일괄 등록 (모달)
  let bulkRows = [];        // [{name, rtsp, st}] — st: null|{ok,width,height}

  /** 텍스트 여러 줄 → 행 목록. 한 줄에 한 대, `이름,rtsp` 또는 `rtsp`만.
   *  빈 줄과 #으로 시작하는 줄은 건너뛴다. rtsp 주소에 콤마가 들어갈 수 있으므로
   *  첫 콤마만 구분자로 쓰고, 콤마 앞이 URL이면 이름 없는 줄로 본다. */
  function parseBulkLines(text) {
    return text.split(/\r?\n/).reduce((acc, raw) => {
      const line = raw.trim();
      if (!line || line.startsWith("#")) return acc;
      const c = line.indexOf(",");
      let name = "", rtsp = line;
      if (c >= 0 && !line.slice(0, c).includes("://")) {
        name = line.slice(0, c).trim();
        rtsp = line.slice(c + 1).trim();
      }
      acc.push({ name, rtsp, st: null });
      return acc;
    }, []);
  }

  const rowValid = (r) => /^rtsps?:\/\//i.test(r.rtsp);

  function renderBulkTable() {
    const tb = $("bulkTbody");
    tb.innerHTML = "";
    bulkRows.forEach((r, i) => {
      const ok = rowValid(r);
      const tr = document.createElement("tr");
      if (!ok) tr.className = "bad";
      let st = '<span class="st-wait">—</span>';
      if (!ok) st = '<span class="st-err">주소 형식 오류</span>';
      else if (r.st === "testing") st = '<span class="st-wait">검사 중…</span>';
      else if (r.st && r.st.ok) st = `<span class="st-ok">✓ ${r.st.width}×${r.st.height}</span>`;
      else if (r.st) st = '<span class="st-err">✗ 연결 실패</span>';
      tr.innerHTML = `<td class="idx">${i + 1}</td>
        <td><input type="text" data-i="${i}" value="${(r.name || "").replace(/"/g, "&quot;")}"
          placeholder="(이름 없음)" /></td>
        <td class="url" title="${r.rtsp.replace(/"/g, "&quot;")}">${r.rtsp}</td>
        <td class="st">${st}</td>
        <td><button class="rowdel" data-del="${i}" title="이 줄 제외">✕</button></td>`;
      tb.appendChild(tr);
    });
    tb.querySelectorAll("input[data-i]").forEach((el) => {
      el.oninput = () => { bulkRows[+el.dataset.i].name = el.value; };
    });
    tb.querySelectorAll("button[data-del]").forEach((el) => {
      el.onclick = () => { bulkRows.splice(+el.dataset.del, 1); renderBulkTable(); };
    });

    const valid = bulkRows.filter(rowValid).length;
    const bad = bulkRows.length - valid;
    $("bulkCount").textContent = `총 ${bulkRows.length}대`
      + (bad ? ` · 유효 ${valid} · 오류 ${bad}` : "");
    $("bulkSubmit").textContent = valid ? `${valid}대 등록` : "등록";
    $("bulkSubmit").disabled = !valid;
    $("bulkStep2").classList.toggle("hidden", !bulkRows.length);
  }

  function openBulk() {
    bulkRows = [];
    $("bulkText").value = "";
    $("bulkMsg").textContent = "";
    $("bulkDisabled").checked = false;
    $("bulkStep2").classList.add("hidden");
    renderFloorDropdowns();
    $("bulkModal").classList.remove("hidden");
    $("bulkText").focus();
  }

  const closeBulk = () => $("bulkModal").classList.add("hidden");

  /** 등록 전 연결 검사 — 동시 4개까지만. NVR 세션 부담을 줄이고,
   *  주소가 틀린 채널을 등록 전에 걸러낸다. */
  async function testBulk() {
    const targets = bulkRows.filter(rowValid);
    if (!targets.length) return;
    $("bulkTest").disabled = $("bulkSubmit").disabled = true;
    targets.forEach((r) => { r.st = "testing"; });
    renderBulkTable();

    let done = 0, next = 0;
    const worker = async () => {
      while (next < targets.length) {
        const r = targets[next++];
        try { r.st = await API.probeRtsp({ rtsp: r.rtsp }); }
        catch (e) { r.st = { ok: false, width: 0, height: 0 }; }
        done++;
        $("bulkMsg").textContent = `연결 검사 ${done}/${targets.length}`;
        renderBulkTable();
      }
    };
    await Promise.all(Array.from({ length: Math.min(4, targets.length) }, worker));

    const okN = targets.filter((r) => r.st && r.st.ok).length;
    $("bulkMsg").textContent = `연결 성공 ${okN} / 실패 ${targets.length - okN}`;
    $("bulkTest").disabled = false;
    renderBulkTable();
  }

  async function submitBulk() {
    const cams = bulkRows.filter(rowValid).map((r) => ({ name: r.name, rtsp: r.rtsp }));
    if (!cams.length) return;
    // 층은 등록 요청에 함께 실어 보낸다 — PUT으로 따로 지정하면 워커가 한 번 더 재시작된다.
    const fl = ($("bulkFloor") && $("bulkFloor").value) || "";
    if (fl && fl !== "default") cams.forEach((c) => { c.floor_id = fl; });
    if ($("bulkDisabled").checked) cams.forEach((c) => { c.enabled = false; });

    $("bulkMsg").textContent = `${cams.length}대 등록 중…`
      + ($("bulkDisabled").checked ? "" : " (워커 재시작 1회 — 기존 채널이 잠시 끊깁니다)");
    $("bulkSubmit").disabled = $("bulkTest").disabled = true;
    try {
      const added = await API.addCameras({ cameras: cams });
      await App.reloadCameras();
      renderList();
      closeBulk();
      $("camAddMsg").textContent = `${added.length}대 등록 완료 `
        + `(${added[0].cam_id}~${added[added.length - 1].cam_id}). 카메라별로 대응점을 지정하세요.`;
    } catch (e) {
      $("bulkMsg").textContent = "실패: " + e.message + " (아무것도 등록되지 않았습니다)";
    } finally {
      $("bulkSubmit").disabled = $("bulkTest").disabled = false;
    }
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
    // 이 카메라가 매핑될 층 (map_pts는 이 층의 맵 px 기준)
    selFloor = cam.floor_id || "default";
    renderFloorDropdowns();
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
    await loadSelFloorMap();                           // 선택 카메라 층의 맵 표시
    renderSel();
  }

  function renderSel() { if (camMc) camMc.render(); if (mapMc) mapMc.render(); }

  function mPerPx() {
    const fl = selFloorObj();
    const m = fl && fl.map;
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

  /** 이 층 출입구 목록을 드롭다운에 채우고, 선택 출입구의 기존 화면 선을 불러온다. */
  function renderExLineUI() {
    const wrap = $("exLineWrap"), selEl = $("exLineSel");
    if (!wrap || !selEl) return;
    wrap.classList.toggle("hidden", mode !== "exline");
    if (mode !== "exline") return;
    const exits = (selFloorObj()?.exits) || [];
    selEl.innerHTML = exits.length
      ? exits.map((e) => {
          const own = e.count_cam ? ` · 담당 ${e.count_cam}` : "";
          return `<option value="${e.id}">${e.name || e.id}${own}</option>`;
        }).join("")
      : '<option value="">(이 층에 출입구 없음)</option>';
    if (!exLineExit || !exits.some((e) => e.id === exLineExit)) {
      exLineExit = exits[0]?.id || null;
    }
    selEl.value = exLineExit || "";
    loadExLine();
  }

  /** 선택 출입구의 저장된 화면 통과선을 편집 상태로 가져온다. */
  function loadExLine() {
    const ex = (selFloorObj()?.exits || []).find((e) => e.id === exLineExit);
    exZonePts = [];
    if (ex && ex.count_cam === sel && ex.cam_zone && ex.cam_zone.length >= 3) {
      exKind = "zone";
      exZonePts = ex.cam_zone.map((p) => p.slice());
      $("exDwell").value = ex.cam_zone_dwell || 2;
      exLinePts = []; exLineInside = null;
      syncExKind();
      hint(`${ex.name || ex.id} — 이 카메라가 영역으로 담당 중.`);
    } else if (ex && ex.count_cam === sel && ex.cam_line && ex.cam_inside) {
      exKind = "line"; syncExKind();
      exLinePts = ex.cam_line.map((p) => p.slice());
      exLineInside = ex.cam_inside.slice();
      hint(`${ex.name || ex.id} — 이 카메라가 담당 중. 다시 그리려면 [되돌리기] 후 클릭.`);
    } else {
      exLinePts = []; exLineInside = null;
      if (ex && ex.count_cam && ex.count_cam !== sel) {
        hint(`${ex.name || ex.id} 은 현재 ${ex.count_cam} 이 담당합니다 — `
           + `여기서 새로 그리면 담당이 이 카메라로 바뀝니다.`, true);
      }
    }
    renderSel();
  }

  /** 화면 통과선을 site 에 저장. line=null 이면 해제(맵 카운트로 복귀). */
  async function saveExLine(payload) {
    const exits = (selFloorObj()?.exits) || [];
    const ex = exits.find((e) => e.id === exLineExit);
    if (!ex) { hint("출입구를 찾을 수 없습니다.", true); return; }
    // 선·영역은 배타 — 한 출입구는 한 방식으로만 집계한다
    ex.count_cam = ex.cam_line = ex.cam_inside = ex.cam_zone = null;
    if (payload && payload.zone) {
      ex.count_cam = sel; ex.cam_zone = payload.zone;
      ex.cam_zone_dwell = payload.dwell || 2;
    } else if (payload) {
      ex.count_cam = sel; ex.cam_line = payload.pts; ex.cam_inside = payload.inside;
    }
    try {
      App.syncFloor();                       // 별칭된 top-level → 층 객체 반영
      await API.putSite(App.site);
      await App.reloadSite();
      renderExLineUI();
      hint(payload
        ? `${ex.name || ex.id} — 이제 ${sel} 화면 `
          + `${payload.zone ? "영역" : "통과선"}으로 집계합니다 `
          + `(맵 선은 표시·폭 계산에만 사용).`
        : `${ex.name || ex.id} — 맵 통과선 집계로 되돌렸습니다.`);
    } catch (e) { hint("저장 실패: " + e.message, true); }
  }

  /** 선/영역 토글 표시 동기화. */
  function syncExKind() {
    document.querySelectorAll("#exKind .tag-btn").forEach((b) =>
      b.classList.toggle("on", b.dataset.kind === exKind));
    const w = $("exDwellWrap");
    if (w) w.style.display = (exKind === "zone") ? "" : "none";
  }

  // 모드별로 아랫줄에 띄울 편집 액션 (①맵 설정 툴바와 같은 규칙 — 지금 쓰는 것만)
  const MODE_OPTS = {
    pair:   ["pairUndo", "pairClear", "hullRoi"],
    roi:    ["pairUndo", "pairClear", "hullRoi"],
    exline: ["exLineWrap", "pairUndo"],
    pan:    [],
  };

  function syncModeOpts() {
    const on = MODE_OPTS[mode] || [];
    // exLineWrap 의 표시는 renderExLineUI 소관 — 여기선 나머지만 만진다.
    ["pairUndo", "pairClear", "hullRoi"].forEach((id) =>
      $(id).classList.toggle("hidden", !on.includes(id)));
    $("camOptNone").classList.toggle("hidden", mode !== "pan");
  }

  function setMode(m) {
    mode = m;
    if (typeof syncExKind === "function") syncExKind();
    if (typeof renderExLineUI === "function") renderExLineUI();
    document.querySelectorAll("#camTools .tag-btn").forEach((b) =>
      b.classList.toggle("on", b.dataset.mode === m));
    syncModeOpts();
    hint();
    renderSel();
  }

  function onCamClick(p) {
    if (!sel) return;
    if (mode === "exline") {
      if (!exLineExit) { hint("먼저 어느 출입구인지 고르세요.", true); return; }
      if (exKind === "zone") {
        exZonePts.push([p.x, p.y]);
        hint(exZonePts.length < 3
          ? `영역 ${exZonePts.length}/3 — 문(나가는 공간)을 감싸게 찍으세요.`
          : `영역 ${exZonePts.length}점 — 더 찍어 다듬거나 [매핑 저장].`);
        renderSel(); return;
      }
      if (exLinePts.length < 2) exLinePts.push([p.x, p.y]);
      else exLineInside = [p.x, p.y];       // 3번째 클릭 = '안쪽'
      hint(exLinePts.length < 2
        ? `통과선 ${exLinePts.length}/2 — 문지방을 가로지르게 두 점을 찍으세요.`
        : (exLineInside ? "선 2점 + 안쪽 지정 완료 — [매핑 저장]으로 반영됩니다."
                        : "이제 '안쪽'(건물 안) 방향에 한 번 더 클릭하세요."));
      renderSel(); return;
    }
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
    if (mode === "exline") {
      if (exKind === "zone") exZonePts.pop();
      else if (exLineInside) exLineInside = null;
      else exLinePts.pop();
      renderSel(); return;
    }
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
    if (mode === "exline") {                 // 화면 집계(선 또는 영역) 저장
      if (exKind === "zone") {
        if (exZonePts.length < 3) {
          hint("영역은 3점 이상이어야 합니다.", true); return;
        }
        await saveExLine({ zone: exZonePts.map((p) => p.slice()),
                           dwell: parseInt($("exDwell").value, 10) || 2 });
        return;
      }
      if (exLinePts.length !== 2 || !exLineInside) {
        hint("통과선 2점 + 안쪽 1점을 모두 찍어야 저장됩니다.", true); return;
      }
      await saveExLine({ pts: exLinePts.map((p) => p.slice()),
                         inside: exLineInside.slice() });
      return;
    }
    const n = Math.min(cctvPts.length, mapPts.length);
    if (n < 4 || cctvPts.length !== mapPts.length) {
      hint(`대응점이 최소 4쌍 필요합니다 (현재 카메라 ${cctvPts.length} · 맵 ${mapPts.length}).`, true);
      return;
    }
    if (roi.length > 0 && roi.length < 3) { hint("유효 ROI는 3점 이상이거나 비워야 합니다.", true); return; }
    try {
      hint("매핑 저장 중…");
      const saved = await API.putMapping(sel, { cctv_pts: cctvPts, map_pts: mapPts,
                                                valid_roi: roi.length >= 3 ? roi : null,
                                                floor_id: selFloor });
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
  /** 회전 버튼 표기 — 0°면 "원래대로" 버튼을 숨긴다. */
  function updateRotLabel() {
    const r = mapMc ? mapMc.rot : 0;
    const b = $("mapRotBtn"), rs = $("mapRotReset");
    if (b) { b.textContent = `도면 회전 ${r}°`; b.classList.toggle("on", !!r); }
    if (rs) rs.classList.toggle("hidden", !r);
  }

  /** 화면 통과선 편집 표시 — 선 2점 + 안쪽 화살표. */
  function drawExLine(g) {
    const { ctx } = g;
    const COL = "#FF9F1C", IN = "#30DCFB";
    if (exKind === "zone") {
      if (exZonePts.length >= 2) {
        mcPath(g, exZonePts, exZonePts.length >= 3);
        if (exZonePts.length >= 3) {
          ctx.fillStyle = "rgba(255,159,28,.22)"; ctx.fill();
        }
        ctx.strokeStyle = COL; ctx.lineWidth = 2.5;
        ctx.setLineDash([6, 4]); ctx.stroke(); ctx.setLineDash([]);
      }
      mcNumbered(g, exZonePts, COL);
      return;
    }
    if (exLinePts.length) {
      if (exLinePts.length === 2) {
        const a = PT(g, exLinePts[0][0], exLinePts[0][1]);
        const b = PT(g, exLinePts[1][0], exLinePts[1][1]);
        ctx.strokeStyle = COL; ctx.lineWidth = 4;
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        if (exLineInside) {                       // 안쪽 방향 화살표
          const i = PT(g, exLineInside[0], exLineInside[1]);
          const m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
          const ang = Math.atan2(i[1] - m[1], i[0] - m[0]);
          ctx.strokeStyle = IN; ctx.lineWidth = 3;
          const ex = m[0] + 34 * Math.cos(ang), ey = m[1] + 34 * Math.sin(ang);
          ctx.beginPath(); ctx.moveTo(m[0], m[1]); ctx.lineTo(ex, ey); ctx.stroke();
          mcArrowHead(ctx, ex, ey, ang, 10, IN);
          ctx.font = "bold 12px Pretendard, sans-serif"; ctx.fillStyle = IN;
          ctx.fillText("안쪽", ex + 6, ey + 4);
        }
      }
      mcNumbered(g, exLinePts, COL);
    }
    // 맵 통과선을 역투영해 참고선으로 (H 를 쓰므로 '추정' — 확정값 아님)
    const ex = (selFloorObj()?.exits || []).find((e) => e.id === exLineExit);
    const H9 = getCamH();
    if (ex && H9) {
      const inv = invH(H9);
      if (inv) {
        const q = ex.line.map((p) => applyH(inv, p[0], p[1])).filter(Boolean);
        if (q.length === 2) {
          const a = PT(g, q[0][0], q[0][1]), b = PT(g, q[1][0], q[1][1]);
          ctx.strokeStyle = "rgba(255,74,68,.55)"; ctx.lineWidth = 2;
          ctx.setLineDash([8, 6]);
          ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
          ctx.setLineDash([]);
          ctx.font = "11px Pretendard, sans-serif"; ctx.fillStyle = "rgba(255,74,68,.8)";
          ctx.fillText(`${ex.name || ex.id} (맵 선 추정 위치)`, a[0] + 6, a[1] - 6);
        }
      }
    }
  }

  /** 3x3 역행렬 — 맵 선을 화면으로 되돌릴 때만 쓴다. */
  function invH(h) {
    const m = [[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], h[8]]];
    const d = m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    if (!d || !isFinite(d)) return null;
    const c = (r, cc) => {
      const s = [[0, 1, 2].filter((i) => i !== r), [0, 1, 2].filter((i) => i !== cc)];
      const a = m[s[0][0]][s[1][0]] * m[s[0][1]][s[1][1]]
              - m[s[0][0]][s[1][1]] * m[s[0][1]][s[1][0]];
      return ((r + cc) % 2 ? -a : a) / d;
    };
    return [c(0,0), c(1,0), c(2,0), c(0,1), c(1,1), c(2,1), c(0,2), c(1,2), c(2,2)];
  }

  function drawCrosshair(g, px, py, col, label) {
    const { ctx } = g;
    const [cx, cy] = PT(g, px, py);      // 회전 반영
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
    const { ctx } = g;
    if (mode === "exline") {
      drawExLine(g);
      return;                        // 대응점·ROI 와 색이 겹치지 않게 단독 표시
    }
    // 각 모드는 자기 데이터만 표시 — 점과 영역이 항상 같은 좌표라 어긋나지 않게.
    if (mode === "roi") {
      // 유효 ROI 편집 — 탐지영역(점 순서 그대로, 오목 가능)만.
      if (roi.length) {
        mcPath(g, roi, true);
        ctx.fillStyle = "rgba(48,220,251,.12)"; ctx.fill();
        ctx.strokeStyle = MC_COLORS.roi; ctx.lineWidth = 2;
        ctx.setLineDash([5, 4]); ctx.stroke(); ctx.setLineDash([]);
        mcNumbered(g, roi, MC_COLORS.roi);
      }
    } else {
      // 대응점 편집 — 대응점의 커버리지(볼록껍질, 참고용)만. valid_roi는 안 겹침.
      if (cctvPts.length >= 3) {
        mcPath(g, convexHull(cctvPts), true);
        ctx.fillStyle = "rgba(255,200,0,.07)"; ctx.fill();
        ctx.strokeStyle = "rgba(255,200,0,.6)"; ctx.lineWidth = 1.5;
        ctx.setLineDash([6, 4]); ctx.stroke(); ctx.setLineDash([]);
      }
      mcNumbered(g, cctvPts, null, pairColor);
    }
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

  /** 이 층 전체 카메라의 대응점·커버영역을 맵에 한꺼번에 그린다.
   *  카메라를 하나씩 눌러보지 않고도 어디가 겹치고 어디가 비었는지 보이게. */
  function drawAllCoverage(g) {
    const { ctx } = g;
    const others = App.cameras.filter(
      (c) => c.mapping && (c.floor_id || "default") === selFloor);
    ctx.save();
    others.forEach((c) => {
      const isSel = c.cam_id === sel;
      const col = camColor(c.cam_id, App.cameras);
      const pts = c.mapping.map_pts;
      // 커버영역 = 대응점의 컨벡스 헐 (실제 투영 게이트와 같은 규칙)
      const hull = convexHull(pts);
      if (hull.length >= 3) {
        mcPath(g, hull, true);
        ctx.fillStyle = col; ctx.globalAlpha = isSel ? 0.22 : 0.10; ctx.fill();
        ctx.globalAlpha = isSel ? 1 : 0.55;
        ctx.strokeStyle = col; ctx.lineWidth = isSel ? 2.5 : 1.5;
        ctx.setLineDash(isSel ? [] : [6, 4]); ctx.stroke(); ctx.setLineDash([]);
      }
      ctx.globalAlpha = isSel ? 1 : 0.7;
      pts.forEach((pt) => {
        const [x, y] = PT(g, pt[0], pt[1]);
        ctx.fillStyle = col;
        ctx.beginPath(); ctx.arc(x, y, isSel ? 4.5 : 3, 0, 7); ctx.fill();
        ctx.strokeStyle = "#111"; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(x, y, isSel ? 4.5 : 3, 0, 7); ctx.stroke();
      });
      // 이름표 — 커버영역 중심
      const cx = pts.reduce((a, q) => a + q[0], 0) / pts.length;
      const cy = pts.reduce((a, q) => a + q[1], 0) / pts.length;
      const [lx, ly] = PT(g, cx, cy);
      const txt = `${c.name || c.cam_id} (${pts.length})`;
      ctx.font = `${isSel ? "bold " : ""}11px Pretendard, sans-serif`;
      const w = ctx.measureText(txt).width + 10;
      ctx.globalAlpha = isSel ? 0.9 : 0.6;
      ctx.fillStyle = "rgba(17,17,17,.8)";
      ctx.fillRect(lx - w / 2, ly - 8, w, 16);
      ctx.globalAlpha = 1;
      ctx.fillStyle = col;
      ctx.fillText(txt, lx - w / 2 + 5, ly + 4);
    });
    ctx.restore();
  }

  /** 컨벡스 헐 (Andrew monotone chain) — 커버영역 표시용. */
  function convexHull(pts) {
    if (pts.length < 3) return pts.slice();
    const p = pts.map((q) => [q[0], q[1]]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const cross = (o, a, b) =>
      (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const half = (arr) => {
      const h = [];
      for (const q of arr) {
        while (h.length >= 2 && cross(h[h.length - 2], h[h.length - 1], q) <= 0) h.pop();
        h.push(q);
      }
      h.pop();
      return h;
    };
    return half(p).concat(half(p.slice().reverse()));
  }

  function mapOverlay(g) {
    // 전체 커버리지를 켜면 구역·병목·경로는 숨긴다 — 색이 겹쳐 카메라
    // 커버영역이 안 보이기 때문. 대응점 작업 중엔 커버영역만 보이는 게 낫다.
    if (!showAllCov) drawSiteElements(g, selFloorObj() || App.site, { faint: true });
    if (showAllCov) drawAllCoverage(g);
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
    $("camBulkOpen").onclick = openBulk;
    $("camEnableMapped").onclick = enableMapped;
    $("bulkClose").onclick = closeBulk;
    $("bulkModal").onclick = (e) => { if (e.target === $("bulkModal")) closeBulk(); };
    $("bulkParse").onclick = () => {
      bulkRows = parseBulkLines($("bulkText").value);
      $("bulkMsg").textContent = bulkRows.length ? "" : "인식된 줄이 없습니다.";
      renderBulkTable();
    };
    $("bulkTest").onclick = testBulk;
    $("bulkSubmit").onclick = submitBulk;
    // 매핑 층 변경 — 선택 카메라를 다른 층에 매핑 (map_pts 좌표계가 바뀌므로 초기화)
    $("mapFloorSel").onchange = async () => {
      selFloor = $("mapFloorSel").value;
      if (mapPts.length) {
        mapPts = [];
        hint(`매핑 층 변경: ${App.floorName(selFloor)} — 맵 대응점을 다시 지정하세요 (좌표계 변경).`, true);
      } else {
        hint(`매핑 층: ${App.floorName(selFloor)}.`);
      }
      await loadSelFloorMap();
      renderSel();
    };
    $("pairUndo").onclick = undo;
    // Ctrl+Z — ① 맵 설정과 동일 규칙(이 화면이 열려 있고 입력 포커스가 아닐 때)
    window.addEventListener("keydown", (e) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z") return;
      if (App.view !== "cams" || !sel) return;
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
      e.preventDefault();
      undo();
    });
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
    // 도면 회전 — 표시 전용. 저장되는 map_pts 는 언제나 원본 맵 px 이므로
    // 돌려놓고 찍어도 매핑값은 그대로다(toMap 이 역회전).
    $("exLineSel").onchange = (e) => { exLineExit = e.target.value || null; loadExLine(); };
    document.querySelectorAll("#exKind .tag-btn").forEach((b) => {
      b.onclick = () => { exKind = b.dataset.kind; syncExKind(); renderSel();
        hint(exKind === "zone"
          ? "문(나가는 공간)을 감싸도록 3점 이상 찍으세요 — 그 영역에 들어오면 집계됩니다."
          : "문지방을 가로지르게 2점 + 안쪽 1점을 찍으세요."); };
    });
    $("exLineClear").onclick = async () => {
      if (!exLineExit) return;
      if (!confirm("이 출입구의 화면 통과선을 해제하고 맵 통과선 카운트로 되돌릴까요?")) return;
      exLinePts = []; exLineInside = null;
      await saveExLine(null);
    };
    $("covAllBtn").onclick = () => {
      showAllCov = !showAllCov;
      $("covAllBtn").classList.toggle("on", showAllCov);
      const n = App.cameras.filter(
        (c) => c.mapping && (c.floor_id || "default") === selFloor).length;
      hint(showAllCov
        ? `이 층 매핑된 카메라 ${n}대의 대응점·커버영역 — 겹치는 곳과 비는 곳을 `
          + `확인하세요. (색이 겹치지 않게 구역·병목·경로는 잠시 숨김)`
        : "구역·병목·경로 표시를 되돌렸습니다.");
      if (mapMc) mapMc.render();
    };
    $("mapRotBtn").onclick = () => {
      if (!mapMc) return;
      mapMc.setRot((mapMc.rot + 90) % 360);
      updateRotLabel();
    };
    $("mapRotReset").onclick = () => {
      if (!mapMc || !mapMc.rot) return;
      mapMc.setRot(0);
      updateRotLabel();
    };
    // ── RTSP 미리보기 — 등록 전/후 임의 주소를 추론까지 돌려 눈으로 확인
    let pvTimer = null, pvImgTimer = null;
    const pvSet = (id, v) => { $(id).textContent = v; };
    async function pvTick() {
      let s;
      try { s = await (await fetch("/api/preview/status")).json(); }
      catch (e) { return; }
      pvSet("pv0", s.stage || (s.running ? "동작 중" : "정지"));
      pvSet("pv1", s.w ? `${s.w}x${s.h}` : "—");
      pvSet("pv2", s.src_fps ? s.src_fps.toFixed(0) : "—");
      pvSet("pv3", s.frames ? s.fps.toFixed(2) : "—");
      pvSet("pv4", s.frames ? s.det.toFixed(2) : "—");
      pvSet("pv5", s.frames ? s.tracks : "—");
      pvSet("pv6", s.first_latency != null ? s.first_latency + "s" : "—");
      // 미리보기는 운영과 같은 프로파일로 돈다 — 어느 모델로 잡은 결과인지 표기
      pvSet("pv7", s.profile || "—");
      $("pvErr").textContent = s.error || "";
      if (!s.running) pvHalt(false);
    }
    function pvHalt(alsoServer) {
      clearInterval(pvTimer); clearInterval(pvImgTimer);
      pvTimer = pvImgTimer = null;
      $("pvStart").disabled = false; $("pvStop").disabled = true;
      if (alsoServer) fetch("/api/preview/stop", { method: "POST" }).catch(() => {});
    }
    async function pvStart() {
      const rtsp = $("pvUrl").value.trim();
      if (!rtsp) return;
      $("pvStart").disabled = true; $("pvErr").textContent = "";
      pvSet("pv0", "엔진 로드·연결 중…");
      try {
        const r = await fetch("/api/preview/start", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rtsp }) });
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail || r.status);
        $("pvStop").disabled = false;
        // 프레임은 폴링으로 교체 (MJPEG 대신 — 연결이 남지 않아 정리가 확실)
        pvImgTimer = setInterval(() => {
          $("pvImg").src = "/api/preview/frame?t=" + Date.now();
        }, 350);
        pvTimer = setInterval(pvTick, 800);
        pvTick();
      } catch (e) {
        $("pvErr").textContent = "시작 실패: " + e.message;
        $("pvStart").disabled = false;
      }
    }
    $("camPreview").onclick = () => {
      // 선택된 카메라가 있으면 그 주소를 채워둔다(등록 후 점검용)
      const c = App.cameras.find((x) => x.cam_id === sel);
      if (c && !$("pvUrl").value) $("pvUrl").value = c.rtsp;
      $("pvModal").classList.remove("hidden");
    };
    $("pvClose").onclick = () => {
      pvHalt(true);                      // 닫으면 반드시 워커도 정지
      $("pvImg").removeAttribute("src");
      $("pvModal").classList.add("hidden");
    };
    $("pvModal").onclick = (e) => { if (e.target === $("pvModal")) $("pvClose").onclick(); };
    $("pvStart").onclick = pvStart;
    $("pvStop").onclick = () => { pvHalt(true); pvSet("pv0", "정지됨"); };
    $("pvUrl").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); pvStart(); } });

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
    if (!sel) selFloor = App.currentFloor;             // 미선택 시 현재 층 기준
    renderFloorDropdowns();
    renderList();
    if (sel) select(sel);
    else {
      $("selCamLabel").textContent = "카메라를 선택하세요";
      loadSelFloorMap();
      hint();
    }
  }

  return { enter, leave: () => {}, renderList,
           // 테스트용 접근자 — 회전/좌표 왕복 검증에 쓴다(런타임 동작엔 무관)
           __mc: () => mapMc, __mapPts: () => mapPts,
           __clearPairs: () => { mapPts = []; cctvPts = [];
                                 if (mapMc) mapMc.render(); if (camMc) camMc.render(); } };
})();
