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

  // ------------------------------------------------------------ 도구
  function setTool(t) {
    tool = t;
    document.querySelectorAll("#mapTools .tag-btn").forEach((b) =>
      b.classList.toggle("on", b.dataset.tool === t));
    draft = t === "pan" ? null : { pts: [], inside: null };
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
    };
    el.textContent = H[tool] || "";
  }

  // ------------------------------------------------------------ 드로잉
  function onClick(p) {
    if (!draft) return;
    const site = App.site;
    if (tool === "scale") {
      if (!site.map) { hint("먼저 맵 이미지를 업로드하세요.", true); return; }
      if (draft.pts.length < 2) draft.pts.push([p.x, p.y]);
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
  function overlay(g) {
    drawSiteElements(g, App.site, { showScale: true });
    if (!draft || !draft.pts.length) return;
    const { ctx } = g;
    const col = MC_COLORS[tool] || "#fff";
    mcPath(g, draft.pts, tool === "zone" || tool === "bottleneck");
    ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.setLineDash([5, 4]);
    ctx.stroke(); ctx.setLineDash([]);
    mcNumbered(g, draft.pts, col);
    if (draft.inside) mcNumbered(g, [draft.inside], "#3FB950");
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
    fill("listBottlenecks", "cntBottlenecks", s.bottlenecks, MC_COLORS.bottleneck,
         (e) => `ρ ${e.rho_crit}`);
    fill("listExits", "cntExits", s.exits, MC_COLORS.exit, () => "통과선");
    $("mapMeta").textContent = s.map ? `map.png · ${s.map.w}×${s.map.h}px` : "맵 없음 — 업로드하세요";
    $("scaleMeta").textContent = s.map && (s.map.scale || s.map.m_per_px != null)
      ? `축척: ${fmtScale()}` : "축척 미지정";
    // thresholds
    const t = s.thresholds || {};
    $("thV").value = t.v_th; $("thA").value = t.a_th; $("thR").value = t.r_th;
    $("thDt").value = t.dt_hold; $("thD").value = t.d_allow;
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
    };
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

  async function upload(file) {
    hint("맵 업로드 중…");
    try {
      await API.uploadMap(file);
      await App.reloadSite();                        // MapSpec 반영 + 이미지 로드
      mc.setImage(App.mapImg, App.site.map.w, App.site.map.h);
      hint("맵 업로드 완료 — 축척 2점을 지정하세요.");
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
      onDblClick: () => finishDraft(),
      draw: overlay,
    });
    document.querySelectorAll("#mapTools .tag-btn").forEach((b) =>
      b.onclick = () => setTool(b.dataset.tool));
    $("drawDone").onclick = finishDraft;
    $("drawCancel").onclick = cancelDraft;
    $("siteSave").onclick = save;
    $("mapUpload").onchange = (e) => { if (e.target.files[0]) upload(e.target.files[0]); };
    $("scaleMeters").onchange = () => {              // 이미 지정된 축척의 실거리 갱신
      const m = parseFloat($("scaleMeters").value);
      if (App.site.map && App.site.map.scale && m > 0) {
        App.site.map.scale.meters = m; refresh();
      }
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
    refresh();
  }

  return { enter, leave: () => {}, refresh };
})();
