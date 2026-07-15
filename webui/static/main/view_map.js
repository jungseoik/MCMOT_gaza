/* 화면 1: 맵 설정 — 업로드·축척 2점·드로잉(경로/구역/병목/출입구)·저장.
 * 모든 좌표는 맵 원본 px (schema.py 규약). */
"use strict";

var Views = window.Views || (window.Views = {});

Views.map = (() => {
  const $ = (id) => document.getElementById(id);
  let mc = null;
  let tool = "pan";
  let draft = null;              // {pts:[[x,y],...], inside:[x,y]|null}
  let inited = false;
  let scaleSnapOn = false;       // 축척 2점 수평·수직 스냅 토글

  // ------------------------------------------------------------ 도구
  function setTool(t) {
    tool = t;
    document.querySelectorAll("#mapTools .tag-btn").forEach((b) =>
      b.classList.toggle("on", b.dataset.tool === t));
    draft = (t === "pan" || t === "graph" || t === "alarm_origin") ? null : { pts: [], inside: null };
    graphSel = null;
    if (mc) { mc.freehand = (t === "route"); mc.render(); }
    hint();
  }

  function hint(msg, warn) {
    const el = $("mapHint");
    if (msg !== undefined) { el.textContent = msg; el.classList.toggle("warn", !!warn); return; }
    el.classList.remove("warn");
    const H = {
      pan: "휠: 줌 · 드래그: 팬 · 도구를 선택해 요소를 그리세요.",
      scale: "축척: 거리를 아는 두 지점을 클릭하고 실거리(m)를 입력하세요.",
      route: "피난경로: 클릭으로 꼭짓점 추가, 드래그로 자유곡선. 더블클릭 또는 [완료]로 종료 (2점 이상).",
      zone: "구역: 꼭짓점을 클릭으로 추가, 더블클릭 또는 [완료]로 닫기 (3점 이상).",
      bottleneck: "병목: 꼭짓점 클릭 + 임계밀도 입력, 더블클릭 또는 [완료]로 닫기 (3점 이상).",
      exit: "출입구: 통과선 2점 클릭 → 세 번째 클릭이 '안쪽' 지점 (자동 완료).",
      graph: "공간그래프(IDR): 빈 곳 클릭=노드 추가 · 노드 클릭 2회=엣지 연결 · 노드 더블클릭=삭제. 복도 교차점·문 위치를 잇는 '걷는 거리' 그래프.",
      alarm_origin: "경보 발생원(IDR): 클릭=경보원 추가 · 더블클릭=삭제. 연기감지기·경보벨 위치를 지정하세요 (N개 가능).",
    };
    el.textContent = H[tool] || "";
  }

  // ------------------------------------------------------------ 공간그래프
  let graphSel = null;                              // 엣지 연결용 선택 노드 id

  function nearNode(p) {
    const g = App.site.graph || { nodes: [] };
    const r = 12 / (mc ? mc.s : 1);                 // 화면 12px 스냅 반경
    return g.nodes.find((n) => Math.hypot(n.xy[0] - p.x, n.xy[1] - p.y) <= r) || null;
  }

  function graphClick(p) {
    const s = App.site;
    if (!s.graph) s.graph = { nodes: [], edges: [] };
    const hit = nearNode(p);
    if (!hit) {                                     // 빈 곳 → 노드 추가
      s.graph.nodes.push({ id: nextId(s.graph.nodes, "n"), xy: [p.x, p.y] });
      graphSel = null;
    } else if (graphSel === null) {                 // 1번째 노드 선택
      graphSel = hit.id;
      hint(`노드 ${hit.id} 선택 — 연결할 노드를 클릭하세요 (같은 노드 재클릭=해제).`);
    } else if (graphSel === hit.id) {
      graphSel = null; hint();
    } else {                                        // 2번째 노드 → 엣지 토글
      const key = (a, b) => (a < b ? [a, b] : [b, a]);
      const [a, b] = key(graphSel, hit.id);
      const i = s.graph.edges.findIndex((e) => key(e[0], e[1])[0] === a && key(e[0], e[1])[1] === b);
      if (i >= 0) { s.graph.edges.splice(i, 1); hint(`엣지 ${a}–${b} 제거`); }
      else { s.graph.edges.push([a, b]); hint(`엣지 ${a}–${b} 연결 — 계속 연결하거나 빈 곳을 클릭하세요.`); }
      graphSel = hit.id;                            // 연쇄 연결 편의
    }
    refresh();
  }

  function graphDblClick(p) {                       // 노드 삭제(연결 엣지 포함)
    const s = App.site;
    const hit = s.graph && nearNode(p);
    if (!hit) return false;
    s.graph.nodes = s.graph.nodes.filter((n) => n.id !== hit.id);
    s.graph.edges = s.graph.edges.filter((e) => e[0] !== hit.id && e[1] !== hit.id);
    if (graphSel === hit.id) graphSel = null;
    refresh();
    return true;
  }

  // ------------------------------------------------------------ 드로잉
  function alarmOriginClick(p) {
    const s = App.site;
    if (!s.alarm_origins) s.alarm_origins = [];
    const r = 12 / (mc ? mc.s : 1);
    const hit = s.alarm_origins.find((o) => Math.hypot(o.xy[0] - p.x, o.xy[1] - p.y) <= r);
    if (!hit) {
      const idx = s.alarm_origins.length + 1;
      s.alarm_origins.push({ id: `ao${idx}`, name: `경보원 ${idx}`, xy: [p.x, p.y] });
    }
    refresh();
  }

  function alarmOriginDblClick(p) {
    const s = App.site;
    if (!s.alarm_origins) return false;
    const r = 12 / (mc ? mc.s : 1);
    const idx = s.alarm_origins.findIndex((o) => Math.hypot(o.xy[0] - p.x, o.xy[1] - p.y) <= r);
    if (idx < 0) return false;
    s.alarm_origins.splice(idx, 1);
    refresh();
    return true;
  }

  function onClick(p) {
    if (tool === "graph") { graphClick(p); return; }
    if (tool === "alarm_origin") { alarmOriginClick(p); return; }
    if (!draft) return;
    const site = App.site;
    if (tool === "scale") {
      if (!site.map) { hint("먼저 맵 이미지를 업로드하세요.", true); return; }
      // 두 번째 점 클릭 시 스냅 적용
      let px = p.x, py = p.y;
      if (scaleSnapOn && draft.pts.length === 1) {
        const [x0, y0] = draft.pts[0];
        const dx = Math.abs(px - x0), dy = Math.abs(py - y0);
        if (dx > dy) py = y0;   // 수평 강제
        else px = x0;            // 수직 강제
      }
      if (draft.pts.length < 2) draft.pts.push([px, py]);
      if (draft.pts.length === 2) applyScale();
    } else if (tool === "exit") {
      if (draft.pts.length < 2) {
        draft.pts.push([p.x, p.y]);
        if (draft.pts.length === 2) hint("이제 '안쪽'(건물 내부 방향) 지점을 클릭하세요.");
      } else if (!draft.inside) {
        draft.inside = [p.x, p.y];
        finishDraft();
      }
    } else {
      draft.pts.push([p.x, p.y]);
    }
    refresh();
  }

  function onDragDraw(p, first) {                    // route 자유곡선 샘플링
    if (!draft || tool !== "route") return;
    const last = draft.pts[draft.pts.length - 1];
    if (first || !last || Math.hypot(p.x - last[0], p.y - last[1]) > 6 / mc.s) {
      draft.pts.push([p.x, p.y]);
    }
  }

  function dedupe(pts) {
    const out = [];
    pts.forEach((p) => {
      const l = out[out.length - 1];
      if (!l || Math.hypot(p[0] - l[0], p[1] - l[1]) > 1) out.push(p);
    });
    return out;
  }

  function nextId(list, prefix) {
    const used = new Set(list.map((e) => e.id));
    let i = 1;
    while (used.has(prefix + i)) i++;
    return prefix + i;
  }

  function applyScale() {
    const m = parseFloat($("scaleMeters").value);
    if (!(m > 0)) { hint("실거리(m)를 올바르게 입력하세요.", true); draft.pts = []; return; }
    App.site.map.scale = { p1: draft.pts[0], p2: draft.pts[1], meters: m };
    App.site.map.m_per_px = null;                    // 수동 축척이 placeholder를 대체
    setTool("pan");
    hint(`축척 설정됨 — 저장하려면 [사이트 저장]. (${fmtScale()})`);
  }

  function finishDraft() {
    if (!draft) return;
    const s = App.site;
    const name = $("elName").value.trim();
    const pts = dedupe(draft.pts);
    if (tool === "route") {
      if (pts.length < 2) { hint("경로는 2점 이상이어야 합니다.", true); return; }
      s.routes.push({ id: nextId(s.routes, "r"), name, points: pts });
    } else if (tool === "zone") {
      if (pts.length < 3) { hint("구역 polygon은 3점 이상이어야 합니다.", true); return; }
      s.zones.push({ id: nextId(s.zones, "z"), name, polygon: pts });
    } else if (tool === "bottleneck") {
      if (pts.length < 3) { hint("병목 polygon은 3점 이상이어야 합니다.", true); return; }
      const rho = parseFloat($("rhoCrit").value) || 2.0;
      s.bottlenecks.push({ id: nextId(s.bottlenecks, "b"), name, polygon: pts,
                           rho_crit: rho, weight: 1.0 });
    } else if (tool === "exit") {
      if (pts.length < 2 || !draft.inside) { hint("통과선 2점 + 안쪽 1점이 필요합니다.", true); return; }
      s.exits.push({ id: nextId(s.exits, "e"), name, line: [pts[0], pts[1]],
                     inside: draft.inside, design_capacity: null });
    } else { return; }
    $("elName").value = "";
    draft = { pts: [], inside: null };
    hint("추가됨 — 계속 그리거나 [사이트 저장]으로 저장하세요.");
    refresh();
  }

  function cancelDraft() { if (draft) { draft = { pts: [], inside: null }; refresh(); hint(); } }

  // ------------------------------------------------------------ 렌더
  function drawAlarmOrigins(g) {
    const aos = App.site && App.site.alarm_origins;
    if (!aos || !aos.length) return;
    const { ctx, TX, TY } = g;
    const r = 9;
    aos.forEach((ao, i) => {
      const x = TX(ao.xy[0]), y = TY(ao.xy[1]);
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255,80,0,0.85)";
      ctx.fill();
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.fillStyle = "#fff";
      ctx.font = `bold ${Math.round(r * 1.1)}px sans-serif`;
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(i + 1, x, y);
    });
  }

  function overlay(g) {
    drawSiteElements(g, App.site, { showScale: true });
    drawGraph(g, App.site.graph, { sel: tool === "graph" ? graphSel : null });
    drawAlarmOrigins(g);
    if (!draft || !draft.pts.length) return;
    const { ctx } = g;
    const col = MC_COLORS[tool] || "#fff";
    mcPath(g, draft.pts, tool === "zone" || tool === "bottleneck");
    ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.setLineDash([5, 4]);
    ctx.stroke(); ctx.setLineDash([]);
    mcNumbered(g, draft.pts, col);
    if (draft.inside) mcNumbered(g, [draft.inside], "#3FB950");
  }

  function mPerPx() {
    const m = App.site && App.site.map;
    if (!m) return null;
    if (m.scale) {
      const d = Math.hypot(m.scale.p2[0] - m.scale.p1[0], m.scale.p2[1] - m.scale.p1[1]);
      return d > 0 ? m.scale.meters / d : null;
    }
    return m.m_per_px != null ? m.m_per_px : null;
  }

  function fmtScale() {
    const m = App.site && App.site.map;
    if (!m) return "";
    if (m.scale) {
      const d = Math.hypot(m.scale.p2[0] - m.scale.p1[0], m.scale.p2[1] - m.scale.p1[1]);
      return d > 0 ? `${(m.scale.meters / d).toFixed(4)} m/px` : "?";
    }
    if (m.m_per_px != null) return `${m.m_per_px} m/px${m.m_per_px === 1.0 ? " (임시값 — 축척 2점을 지정하세요)" : " (메타 자동)"}`;
    return "";
  }

  function elItem(label, meta, color, onDel) {
    const div = document.createElement("div");
    div.className = "elitem";
    div.innerHTML = `<span class="swatch" style="background:${color}"></span>
      <span class="nm">${label}</span><span class="meta">${meta}</span>
      <button class="del" title="삭제">🗑</button>`;
    div.querySelector(".del").onclick = onDel;
    return div;
  }

  function refreshLists() {
    const s = App.site;
    const fill = (elId, cntId, list, color, metaFn) => {
      const box = $(elId);
      box.innerHTML = "";
      $(cntId).textContent = list.length;
      list.forEach((e, i) => box.appendChild(
        elItem(e.name || e.id, metaFn(e), color,
               () => { list.splice(i, 1); refresh(); })));
    };
    fill("listRoutes", "cntRoutes", s.routes, MC_COLORS.route, (e) => `${e.points.length}점`);
    fill("listZones", "cntZones", s.zones, MC_COLORS.zone, (e) => `${e.polygon.length}점`);
    // 병목 — rho_crit·weight 인라인 편집
    (function fillBns() {
      const box = $("listBottlenecks");
      box.innerHTML = "";
      $("cntBottlenecks").textContent = s.bottlenecks.length;
      s.bottlenecks.forEach((bn, i) => {
        const div = document.createElement("div");
        div.className = "elitem elitem-bn";
        div.innerHTML =
          `<span class="swatch" style="background:${MC_COLORS.bottleneck}"></span>` +
          `<span class="nm">${bn.name || bn.id}</span>` +
          `<label class="bfield" title="임계밀도 (명/m²)">ρcrit ` +
            `<input type="number" class="bni" min="0.1" step="0.1" value="${bn.rho_crit}"> 명/m²</label>` +
          `<label class="bfield" title="가중치 w_k">w ` +
            `<input type="number" class="bni" min="0.01" step="0.1" value="${bn.weight}"></label>` +
          `<button class="del" title="삭제">🗑</button>`;
        const [rhoIn, wIn] = div.querySelectorAll("input");
        rhoIn.oninput = () => { const v = parseFloat(rhoIn.value); if (v > 0) s.bottlenecks[i].rho_crit = v; };
        wIn.oninput   = () => { const v = parseFloat(wIn.value);   if (v > 0) s.bottlenecks[i].weight   = v; };
        div.querySelector(".del").onclick = () => { s.bottlenecks.splice(i, 1); refresh(); };
        box.appendChild(div);
      });
    })();
    // 출입구 — W_eff(m) · 계산 C_j 표시
    (function fillExits() {
      const mpp = mPerPx();
      const qd = parseFloat($("thQd").value) || 60;
      const box = $("listExits"); box.innerHTML = "";
      $("cntExits").textContent = s.exits.length;
      s.exits.forEach((ex, i) => {
        let meta = "통과선";
        if (mpp && ex.line && ex.line.length === 2) {
          const dx = ex.line[1][0] - ex.line[0][0], dy = ex.line[1][1] - ex.line[0][1];
          const w = Math.sqrt(dx * dx + dy * dy) * mpp;
          meta = `W ${w.toFixed(2)}m · C ${Math.max(1, Math.round(w * qd))}명/분`;
        }
        box.appendChild(elItem(ex.name || ex.id, meta, MC_COLORS.exit,
          () => { s.exits.splice(i, 1); refresh(); }));
      });
    })();
    // 경보 발생원
    (function fillAlarmOrigins() {
      const box = $("listAlarmOrigins"); box.innerHTML = "";
      const aos = s.alarm_origins || [];
      $("cntAlarmOrigins").textContent = aos.length;
      aos.forEach((ao, i) => {
        box.appendChild(elItem(
          ao.name || ao.id,
          `(${Math.round(ao.xy[0])}, ${Math.round(ao.xy[1])})`,
          "#ff5000",
          () => { aos.splice(i, 1); refresh(); }
        ));
      });
      $("alarmOriginMeta").textContent = aos.length
        ? `${aos.length}개 경보원 — IDR D(zone, origin) 격자 BFS 평균 후 평균 IDR`
        : "경보원 없음 — [경보원] 도구로 맵에 클릭하여 추가하세요";
    })();
    // 공간그래프 — 노드·엣지 요약 1행 + 전체 지우기
    const gbox = $("listGraph");
    const gr = s.graph || { nodes: [], edges: [] };
    gbox.innerHTML = "";
    $("cntGraph").textContent = gr.nodes.length;
    if (gr.nodes.length) {
      gbox.appendChild(elItem("공간그래프", `노드 ${gr.nodes.length} · 엣지 ${gr.edges.length}`,
        MC_COLORS.graph, () => { s.graph = { nodes: [], edges: [] }; graphSel = null; refresh(); }));
    }
    $("graphMeta").textContent = gr.nodes.length
      ? "IDR 최단거리는 이 그래프 위에서 계산 (미지정 시 직선거리 폴백)"
      : "그래프 없음 — IDR은 직선거리 폴백으로 계산";
    $("mapMeta").textContent = s.map ? `map.png · ${s.map.w}×${s.map.h}px` : "맵 없음 — 업로드하세요";
    $("scaleMeta").textContent = s.map && (s.map.scale || s.map.m_per_px != null)
      ? `축척: ${fmtScale()}` : "축척 미지정";
    // thresholds
    const t = s.thresholds || {};
    $("thV").value = t.v_th; $("thA").value = t.a_th; $("thR").value = t.r_th;
    $("thDt").value = t.dt_hold; $("thD").value = t.d_allow;
    $("thC").value = t.min_conf != null ? t.min_conf : 0.35;
    $("thQd").value = t.q_design != null ? t.q_design : 60;
  }

  function refresh() { refreshLists(); if (mc) mc.render(); }

  // ------------------------------------------------------------ 저장·업로드
  async function save() {
    const s = App.site;
    s.thresholds = {
      v_th: parseFloat($("thV").value) || 0.5,
      a_th: parseFloat($("thA").value) || 0.7,
      r_th: parseFloat($("thR").value) || 0.5,
      dt_hold: parseFloat($("thDt").value) || 3.0,
      d_allow: parseFloat($("thD").value) || 2.0,
      min_conf: (() => { const v = parseFloat($("thC").value); return isNaN(v) ? 0.35 : v; })(),
      q_design: parseFloat($("thQd").value) || 60.0,
    };
    // 격자 셀 크기
    const cellM = parseFloat($("gridCellSize").value);
    if (cellM > 0) s.grid = { ...(s.grid || {}), cell_size_m: cellM };
    // alarm_origins는 이미 s.alarm_origins 직접 수정 중이므로 별도 처리 불필요
    // 출입구 design_capacity 자동 계산: 선 길이(px) × m_per_px × q_design
    const mpp = mPerPx();
    if (mpp && s.exits) {
      const qd = s.thresholds.q_design;
      s.exits.forEach((ex) => {
        if (!ex.line || ex.line.length < 2) return;
        const dx = ex.line[1][0] - ex.line[0][0], dy = ex.line[1][1] - ex.line[0][1];
        const w_eff_m = Math.sqrt(dx * dx + dy * dy) * mpp;
        ex.design_capacity = Math.max(1, Math.round(w_eff_m * qd));
      });
    }
    try {
      App.site = await API.putSite(s);
      App.updateChip();
      $("mapSaveMsg").textContent = `저장됨 · v${App.site.version}`;
      setTimeout(() => { $("mapSaveMsg").textContent = ""; }, 4000);
      refresh();
    } catch (e) {
      $("mapSaveMsg").textContent = "저장 실패: " + e.message;
    }
  }

  async function upload(files) {
    // 이미지 1장 (+선택: cad-convert *_scale.meta.json → 축척 자동)
    const list = Array.from(files);
    const img = list.find((f) => f.type.startsWith("image/"));
    const metaFile = list.find((f) => f.name.endsWith(".json"));
    if (!img) { hint("맵 이미지 파일을 선택하세요.", true); return; }
    hint("맵 업로드 중…");
    try {
      let meta = null;
      if (metaFile) {
        meta = JSON.parse(await metaFile.text());
        if (meta.m_per_px == null) { hint("메타 JSON에 m_per_px가 없습니다.", true); return; }
      }
      await API.uploadMap(img, meta);
      await App.reloadSite();                        // MapSpec 반영 + 이미지 로드
      mc.setImage(App.mapImg, App.site.map.w, App.site.map.h);
      hint(meta ? `맵 업로드 완료 — 축척 자동 설정 (${meta.m_per_px} m/px, CAD 메타).`
                : "맵 업로드 완료 — 축척 2점을 지정하세요.");
      refresh();
    } catch (e) { hint("업로드 실패: " + e.message, true); }
  }

  // ------------------------------------------------------------ lifecycle
  function init() {
    if (inited) return;
    inited = true;
    mc = new MapCanvas($("mapSetupCv"), {
      onClick, onDragDraw,
      onDragEnd: () => {},
      onDblClick: (p) => {
        if (tool === "graph") { graphDblClick(p); return; }
        if (tool === "alarm_origin") { alarmOriginDblClick(p); return; }
        finishDraft();
      },
      draw: overlay,
    });
    document.querySelectorAll("#mapTools .tag-btn").forEach((b) =>
      b.onclick = () => setTool(b.dataset.tool));
    $("drawDone").onclick = finishDraft;
    $("drawCancel").onclick = cancelDraft;
    // 우측 패널: [설정] ↔ [지표 설명] 토글
    const msTab = (help) => {
      $("mapSetPanel").classList.toggle("hidden", help);
      $("mapHelpPanel").classList.toggle("hidden", !help);
      $("msTabSet").classList.toggle("on", !help);
      $("msTabHelp").classList.toggle("on", help);
    };
    $("msTabSet").onclick = () => msTab(false);
    $("msTabHelp").onclick = () => msTab(true);
    $("siteSave").onclick = save;
    $("mapUpload").onchange = (e) => { if (e.target.files.length) upload(e.target.files); };
    $("scaleMeters").onchange = () => {              // 이미 지정된 축척의 실거리 갱신
      const m = parseFloat($("scaleMeters").value);
      if (App.site.map && App.site.map.scale && m > 0) {
        App.site.map.scale.meters = m; refresh();
      }
    };
    $("scaleSnap").onclick = () => {
      scaleSnapOn = !scaleSnapOn;
      $("scaleSnap").textContent = scaleSnapOn ? "스냅 ON" : "스냅 OFF";
      $("scaleSnap").classList.toggle("on", scaleSnapOn);
    };
    window.addEventListener("keydown", (e) => {
      if ($("viewMap").classList.contains("hidden")) return;
      if (e.target.tagName === "INPUT") return;
      if (e.key === "Enter") finishDraft();
      if (e.key === "Escape") cancelDraft();
    });
    setTool("pan");
  }

  function enter() {
    init();
    if (App.mapImg && App.site.map) mc.setImage(App.mapImg, App.site.map.w, App.site.map.h);
    else mc.setImage(null, 1000, 600);
    // 사이트 설정 → gridCellSize 입력값 동기화
    const g = App.site && App.site.grid;
    if (g && g.cell_size_m) $("gridCellSize").value = g.cell_size_m;
    refresh();
  }

  return { enter, leave: () => {}, refresh };
})();
