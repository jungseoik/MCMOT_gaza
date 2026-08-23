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
  let gridOn = false;            // 미터 격자·스케일바 표시 (표시 전용, 저장 안 됨)

  // ------------------------------------------------------------ 도구
  // 도구별로 둘째 줄에 띄울 입력 (없는 도구는 안내문만)
  const TOOL_OPTS = {
    scale:      ["fldScale"],
    route:      ["fldName"],
    zone:       ["fldName"],
    bottleneck: ["fldName", "fldRho"],
    bnsector:   ["fldName", "fldRho"],
    exit:       ["fldName"],
  };

  function syncToolOpts() {
    const on = TOOL_OPTS[tool] || [];
    ["fldName", "fldScale", "fldRho"].forEach((id) =>
      $(id).classList.toggle("hidden", !on.includes(id)));
    $("optNone").classList.toggle("hidden", on.length > 0);
    // 완료/취소는 그리는 중에만 의미가 있다 (이동·그래프 도구엔 없음)
    const drawing = !(tool === "pan" || tool === "graph");
    $("drawDone").classList.toggle("hidden", !drawing);
    $("drawCancel").classList.toggle("hidden", !drawing);
  }

  function setTool(t) {
    tool = t;
    document.querySelectorAll("#mapTools .tag-btn").forEach((b) =>
      b.classList.toggle("on", b.dataset.tool === t));
    draft = (t === "pan" || t === "graph") ? null : { pts: [], inside: null };
    graphSel = null; hoverPt = null;
    syncToolOpts();
    if (mc) { mc.freehand = (t === "route"); mc.render(); }
    hint();
  }

  function hint(msg, warn) {
    const el = $("mapHint");
    if (msg !== undefined) { el.textContent = msg; el.classList.toggle("warn", !!warn); return; }
    el.classList.remove("warn");
    const H = {
      pan: "휠: 줌 · 드래그: 팬 · 도구를 선택해 요소를 그리세요.",
      scale: "축척: CAD 도면은 자동으로 잡힙니다 — 자동값이 없거나 틀릴 때만 거리를 아는 두 지점을 클릭하고 실거리(m)를 입력하세요.",
      route: "피난경로: 클릭으로 꼭짓점 추가, 드래그로 자유곡선. 더블클릭 또는 [완료]로 종료 (2점 이상).",
      zone: "구역: 꼭짓점을 클릭으로 추가, 더블클릭 또는 [완료]로 닫기 (3점 이상).",
      bottleneck: "병목: 꼭짓점 클릭 + 임계밀도 입력, 더블클릭 또는 [완료]로 닫기 (3점 이상).",
      bnsector: "병목 부채꼴: ① 문·계단 위치(꼭짓점) ② 반경·시작방향 ③ 끝방향 — 3번째 클릭에 생성. 반경·각도는 목록에서 다시 조절합니다.",
      exit: "출입구: 통과선 2점 클릭 → 세 번째 클릭이 '안쪽' 지점 (자동 완료).",
      graph: "공간그래프(IDR): 빈 곳 클릭=노드 추가 · 노드 클릭 2회=엣지 연결 · 노드 더블클릭=삭제. 복도 교차점·문 위치를 잇는 '걷는 거리' 그래프.",
    };
    el.textContent = H[tool] || "";
  }

  // ------------------------------------------------------------ 실행취소
  // CAD 편집기(:8910)와 같은 방식 — 되돌릴 수 있는 동작만 op 로그에 쌓는다.
  // 요소 추가/삭제와 통째로 바뀌는 것(그래프·축척)만 담고, 인라인 숫자 편집은
  // 담지 않는다(키 입력마다 스택이 쌓여 되돌리기가 쓸모없어진다).
  const UNDO_MAX = 60;
  let undoStack = [];

  const LISTS = {
    route:      () => App.site.routes,
    zone:       () => App.site.zones,
    bottleneck: () => App.site.bottlenecks,
    exit:       () => App.site.exits,
  };
  const KIND_LABEL = { route: "피난경로", zone: "구역", bottleneck: "병목", exit: "출입구" };

  function pushOp(op) {
    undoStack.push(op);
    if (undoStack.length > UNDO_MAX) undoStack.shift();
    syncUndoBtn();
  }
  const pushAdd = (kind) => pushOp({ t: kind, i: LISTS[kind]().length - 1, add: true });
  const pushDel = (kind, i, v) => pushOp({ t: kind, i, v, add: false });
  const snap = (o) => JSON.parse(JSON.stringify(o == null ? null : o));
  const pushSnap = (t, v) => pushOp({ t, v: snap(v) });

  function syncUndoBtn() {
    const b = $("drawUndo");
    if (!b) return;
    const n = undoStack.length + (draft && draft.pts.length ? 1 : 0);
    b.disabled = n === 0;
  }

  function undo() {
    // 그리는 중이면 찍던 점부터 하나씩 — 요소를 통째로 날리는 것보다 자연스럽다
    if (draft && draft.pts.length) {
      if (draft.inside) draft.inside = null;
      else draft.pts.pop();
      hint(`점 취소 — ${draft.pts.length}점 남음`);
      refresh(); syncUndoBtn();
      return;
    }
    const op = undoStack.pop();
    if (!op) { hint("되돌릴 작업이 없습니다."); return; }
    if (op.t === "graph") {
      App.site.graph = op.v || { nodes: [], edges: [] };
      graphSel = null;
      hint("공간그래프 되돌림");
    } else if (op.t === "scale") {
      if (App.site.map) {
        App.site.map.scale = op.v ? op.v.scale : null;
        App.site.map.m_per_px = op.v ? op.v.m_per_px : null;
      }
      hint("축척 되돌림");
    } else {
      const arr = LISTS[op.t] && LISTS[op.t]();
      if (!arr) return;
      if (op.add) { arr.splice(op.i, 1); hint(`${KIND_LABEL[op.t]} 추가 되돌림`); }
      else { arr.splice(op.i, 0, op.v); hint(`${KIND_LABEL[op.t]} 삭제 되돌림`); }
    }
    refresh(); syncUndoBtn();
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
    pushSnap("graph", s.graph);                     // 노드·엣지 변경 전 상태
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
    if (JSON.stringify(s.graph) !== before) pushOp({ t: "graph", v: JSON.parse(before) });
    refresh();
  }

  function graphDblClick(p) {                       // 노드 삭제(연결 엣지 포함)
    const s = App.site;
    const hit = s.graph && nearNode(p);
    if (!hit) return false;
    pushSnap("graph", s.graph);
    s.graph.nodes = s.graph.nodes.filter((n) => n.id !== hit.id);
    s.graph.edges = s.graph.edges.filter((e) => e[0] !== hit.id && e[1] !== hit.id);
    if (graphSel === hit.id) graphSel = null;
    refresh();
    return true;
  }

  // ------------------------------------------------------- 출구 설계용량 C_j
  // C_j = W_eff × q_design. 규칙은 백엔드 schema.py ExitLine 과 같아야 한다 —
  // 저장하면 백엔드가 같은 식으로 다시 계산해 덮는다(진실은 백엔드).
  function autoWidthM(ex) {
    const mpp = mPerPx();
    if (!mpp || !ex.line || ex.line.length < 2) return null;
    const d = Math.hypot(ex.line[1][0] - ex.line[0][0],
                         ex.line[1][1] - ex.line[0][1]);
    return d > 0 ? d * mpp : null;
  }

  function effWidthM(ex) {
    return (ex.width_m != null && ex.width_m > 0) ? ex.width_m : autoWidthM(ex);
  }

  function effQ(ex) {
    if (ex.q_design != null && ex.q_design > 0) return ex.q_design;
    const t = App.site && App.site.thresholds;
    return (t && t.q_design) || 60;
  }

  function capacityOf(ex) {
    const w = effWidthM(ex), q = effQ(ex);
    return (w > 0 && q > 0) ? Math.max(1, Math.round(w * q)) : null;
  }

  // ------------------------------------------------------------ 부채꼴 영역
  // 병목은 실제로 문·계단 앞에서 부채꼴로 생긴다. 자유 다각형으로 찍으면
  // 매번 모양이 달라지고 나중에 반경·각도를 못 고치므로, 파라미터를 저장하고
  // polygon은 거기서 생성한다. 생성식은 백엔드 system/config/shapes.py 와
  // 동일해야 한다 — 저장 시 백엔드가 같은 식으로 다시 만들어 덮는다.
  const SECTOR_SEG = 24;
  let hoverPt = null;                               // 부채꼴 미리보기용 커서

  function sectorPoly(c, r, a0, sweep, seg, ri) {
    seg = Math.max(3, Math.min(180, seg || SECTOR_SEG));
    ri = ri || 0;
    const arc = [];
    for (let i = 0; i <= seg; i++) {
      const a = a0 + sweep * i / seg;
      arc.push([c[0] + r * Math.cos(a), c[1] + r * Math.sin(a)]);
    }
    if (ri > 0) {
      for (let i = seg; i >= 0; i--) {
        const a = a0 + sweep * i / seg;
        arc.push([c[0] + ri * Math.cos(a), c[1] + ri * Math.sin(a)]);
      }
      return arc;
    }
    return [[c[0], c[1]]].concat(arc);
  }

  // 세 번째 점 방향까지의 스윕각 — 짧은 쪽(|sweep| ≤ π)으로 잡는다.
  function sweepTo(c, a0, p) {
    const a1 = Math.atan2(p[1] - c[1], p[0] - c[0]);
    let sw = a1 - a0;
    while (sw > Math.PI) sw -= 2 * Math.PI;
    while (sw < -Math.PI) sw += 2 * Math.PI;
    return sw;
  }

  function draftSector(endPt) {
    if (!draft || draft.pts.length < 2) return null;
    const c = draft.pts[0], p1 = draft.pts[1];
    const r = Math.hypot(p1[0] - c[0], p1[1] - c[1]);
    if (r <= 0) return null;
    const a0 = Math.atan2(p1[1] - c[1], p1[0] - c[0]);
    const sw = endPt ? sweepTo(c, a0, endPt) : 0;
    return { center: c, radius: r, a0: a0, sweep: sw, segments: SECTOR_SEG,
             radius_in: 0 };
  }

  function shapePoly(sh) {
    return sectorPoly(sh.center, sh.radius, sh.a0, sh.sweep, sh.segments,
                      sh.radius_in);
  }

  function onHover(p) {                             // 부채꼴 그리는 중에만 재렌더
    if (tool !== "bnsector" || !draft || draft.pts.length !== 2) {
      if (hoverPt) { hoverPt = null; return true; }
      return false;
    }
    hoverPt = [p.x, p.y];
    return true;
  }

  // ------------------------------------------------------------ 드로잉
  function onClick(p) {
    if (tool === "graph") { graphClick(p); return; }
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
    } else if (tool === "bnsector") {
      if (draft.pts.length < 2) {
        draft.pts.push([p.x, p.y]);
        hint(draft.pts.length === 1
          ? "부채꼴 반경·시작방향 지점을 클릭하세요."
          : "이제 끝방향 지점을 클릭하면 생성됩니다 (반대편으로 벌어집니다).");
      } else {
        draft.pts.push([p.x, p.y]);
        finishDraft();
      }
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
    const mm = App.site.map;
    const m = parseFloat($("scaleMeters").value);
    if (!(m > 0)) { hint("실거리(m)를 올바르게 입력하세요.", true); draft.pts = []; return; }
    const d = Math.hypot(draft.pts[1][0] - draft.pts[0][0],
                         draft.pts[1][1] - draft.pts[0][1]);
    const manual = d > 0 ? m / d : null;
    // CAD 실측값을 덮어쓰는 건 되돌리기 어렵다(다시 얻으려면 CAD 재적용).
    // 두 값을 나란히 보여주고 확인받는다.
    if (mm.m_per_px != null && manual) {
      const diff = (manual / mm.m_per_px - 1) * 100;
      const ok = confirm(
        `이 층은 CAD 도면에서 축척이 자동으로 잡혀 있습니다.\n\n`
        + `  CAD 자동   ${mm.m_per_px.toFixed(5)} m/px`
        + `${mm.source ? `  (${mm.source}${mm.unit ? ` · ${mm.unit}` : ""})` : ""}\n`
        + `  직접 지정  ${manual.toFixed(5)} m/px   (${diff >= 0 ? "+" : ""}${diff.toFixed(1)}%)\n\n`
        + `직접 지정한 값으로 바꾸면 CAD 자동값은 버려집니다.\n`
        + `(되돌리려면 CAD 도면을 다시 적용해야 합니다)\n\n계속할까요?`);
      if (!ok) { draft.pts = []; setTool("pan"); hint("축척을 그대로 두었습니다."); return; }
    }
    pushSnap("scale", { scale: mm.scale, m_per_px: mm.m_per_px });
    mm.scale = { p1: draft.pts[0], p2: draft.pts[1], meters: m };
    mm.m_per_px = null;                              // 수동 축척이 자동값을 대체
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
      pushAdd("route");
    } else if (tool === "zone") {
      if (pts.length < 3) { hint("구역 polygon은 3점 이상이어야 합니다.", true); return; }
      s.zones.push({ id: nextId(s.zones, "z"), name, polygon: pts });
      pushAdd("zone");
    } else if (tool === "bottleneck") {
      if (pts.length < 3) { hint("병목 polygon은 3점 이상이어야 합니다.", true); return; }
      const rho = parseFloat($("rhoCrit").value) || 2.0;
      s.bottlenecks.push({ id: nextId(s.bottlenecks, "b"), name, polygon: pts,
                           rho_crit: rho, weight: 1.0 });
      pushAdd("bottleneck");
    } else if (tool === "bnsector") {
      const sh = draftSector(draft.pts[2]);
      if (!sh || !sh.sweep) { hint("부채꼴은 3점(꼭짓점·반경·끝방향)이 필요합니다.", true); return; }
      const rho = parseFloat($("rhoCrit").value) || 2.0;
      s.bottlenecks.push({
        id: nextId(s.bottlenecks, "b"), name, polygon: shapePoly(sh),
        rho_crit: rho, weight: 1.0, group: "",
        shape: { kind: "sector", center: sh.center, radius: sh.radius,
                 radius_in: 0, a0: sh.a0, sweep: sh.sweep, segments: sh.segments },
      });
      pushAdd("bottleneck");
    } else if (tool === "exit") {
      if (pts.length < 2 || !draft.inside) { hint("통과선 2점 + 안쪽 1점이 필요합니다.", true); return; }
      s.exits.push({ id: nextId(s.exits, "e"), name, line: [pts[0], pts[1]],
                     inside: draft.inside, design_capacity: null });
      pushAdd("exit");
    } else { return; }
    $("elName").value = "";
    draft = { pts: [], inside: null };
    hint("추가됨 — 계속 그리거나 [사이트 저장]으로 저장하세요.");
    refresh();
  }

  function cancelDraft() { if (draft) { draft = { pts: [], inside: null }; refresh(); hint(); } }

  // ------------------------------------------------------------ 렌더
  function overlay(g) {
    const mpp = gridOn ? mPerPx() : null;
    if (mpp) drawScaleGrid(g, mpp);               // 요소보다 아래(배경 쪽)
    drawSiteElements(g, App.site, { showScale: true });
    drawGraph(g, App.site.graph, { sel: tool === "graph" ? graphSel : null });
    drawDraft(g);
    if (mpp) drawScaleBar(g, mpp);                // 스케일바는 항상 맨 위
  }

  function drawDraft(g) {
    if (!draft || !draft.pts.length) return;
    const { ctx } = g;
    const col = MC_COLORS[tool] || "#fff";
    if (tool === "bnsector") {                      // 부채꼴 미리보기
      const sh = draftSector(draft.pts[2] || hoverPt);
      ctx.strokeStyle = MC_COLORS.bottleneck; ctx.lineWidth = 2;
      ctx.setLineDash([5, 4]);
      if (sh && sh.sweep) {
        mcPath(g, shapePoly(sh), true);
        ctx.stroke();
      } else if (draft.pts.length >= 2) {           // 반경선만
        mcPath(g, draft.pts.slice(0, 2), false);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      mcNumbered(g, draft.pts, MC_COLORS.bottleneck);
      return;
    }
    mcPath(g, draft.pts, tool === "zone" || tool === "bottleneck");
    ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.setLineDash([5, 4]);
    ctx.stroke(); ctx.setLineDash([]);
    mcNumbered(g, draft.pts, col);
    if (draft.inside) mcNumbered(g, [draft.inside], "#3FB950");
  }

  // 축척 우선순위는 백엔드 MapSpec.resolve_m_per_px() 와 같아야 한다 —
  // CAD 실측(m_per_px)이 먼저, 수동 2점은 그것이 없을 때의 최후 수단.
  // (예전엔 여기만 2점을 우선해서, 지표는 CAD 축척으로 계산되는데 화면은
  //  옛 2점 축척을 보여주는 불일치가 났다.)
  function scaleFromPts(sc) {
    if (!sc) return null;
    const d = Math.hypot(sc.p2[0] - sc.p1[0], sc.p2[1] - sc.p1[1]);
    return d > 0 ? sc.meters / d : null;
  }

  function mPerPx() {
    const m = App.site && App.site.map;
    if (!m) return null;
    if (m.m_per_px != null) return m.m_per_px;
    return scaleFromPts(m.scale);
  }

  function fmtScale() {
    const m = App.site && App.site.map;
    if (!m) return "";
    if (m.m_per_px != null) {
      if (m.m_per_px === 1.0) return "1 m/px (임시값 — 도면 단위 불명, 축척 2점을 지정하세요)";
      const width_m = (m.w * m.m_per_px).toFixed(1);
      return `${m.m_per_px.toFixed(5)} m/px · CAD 도면에서 자동 (가로 ${width_m}m)`;
    }
    const v = scaleFromPts(m.scale);
    return v ? `${v.toFixed(5)} m/px · 직접 지정한 2점` : "";
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
    fill("listRoutes", "cntRoutes", s.routes, MC_COLORS.route, (e) => `${e.points.length}점`, "route");
    fill("listZones", "cntZones", s.zones, MC_COLORS.zone, (e) => `${e.polygon.length}점`, "zone");
    // 병목 — rho_crit·weight·그룹 인라인 편집 (+부채꼴이면 반경·각도)
    (function fillBns() {
      const box = $("listBottlenecks");
      const mpp = mPerPx();
      box.innerHTML = "";
      $("cntBottlenecks").textContent = s.bottlenecks.length;
      s.bottlenecks.forEach((bn, i) => {
        const div = document.createElement("div");
        div.className = "elitem elitem-bn";
        const sec = bn.shape && bn.shape.kind === "sector" ? bn.shape : null;
        // 부채꼴은 반경(축척 있으면 m, 없으면 px)·각도(°)를 그대로 고친다 —
        // polygon은 그 값에서 다시 생성되므로 다시 찍을 필요가 없다.
        const rDisp = sec ? (mpp ? sec.radius * mpp : sec.radius) : 0;
        div.innerHTML =
          `<span class="swatch" style="background:${MC_COLORS.bottleneck}"></span>` +
          `<span class="nm">${bn.name || bn.id}${sec ? " <i class=\"mtag\">부채꼴</i>" : ""}</span>` +
          `<label class="bfield" title="임계밀도 (명/m²)">ρcrit ` +
            `<input type="number" class="bni r" min="0.1" step="0.1" value="${bn.rho_crit}"> 명/m²</label>` +
          `<label class="bfield" title="가중치 w_k">w ` +
            `<input type="number" class="bni w" min="0.01" step="0.1" value="${bn.weight}"></label>` +
          (sec
            ? `<label class="bfield" title="부채꼴 반경">r ` +
                `<input type="number" class="bni rad" min="0.1" step="${mpp ? "0.1" : "5"}" ` +
                `value="${rDisp.toFixed(mpp ? 2 : 0)}"> ${mpp ? "m" : "px"}</label>` +
              `<label class="bfield" title="부채꼴 벌어진 각도">각 ` +
                `<input type="number" class="bni ang" min="1" max="360" step="5" ` +
                `value="${Math.round(Math.abs(sec.sweep) * 180 / Math.PI)}"> °</label>`
            : "") +
          `<label class="bfield grp" title="CBS 집계 그룹 — 같은 라벨끼리 합계·평균을 따로 봅니다">그룹 ` +
            `<input type="text" class="bni gp" placeholder="(미분류)" value="${bn.group || ""}"></label>` +
          `<button class="del" title="삭제">🗑</button>`;
        const q = (c) => div.querySelector("input." + c);
        q("r").oninput = () => { const v = parseFloat(q("r").value); if (v > 0) s.bottlenecks[i].rho_crit = v; };
        q("w").oninput = () => { const v = parseFloat(q("w").value); if (v > 0) s.bottlenecks[i].weight = v; };
        q("gp").oninput = () => { s.bottlenecks[i].group = q("gp").value.trim(); };
        if (sec) {
          const regen = () => {
            const rv = parseFloat(q("rad").value), av = parseFloat(q("ang").value);
            if (!(rv > 0) || !(av > 0)) return;
            const sh = s.bottlenecks[i].shape;
            sh.radius = mpp ? rv / mpp : rv;
            sh.sweep = Math.sign(sh.sweep || 1) * Math.min(av, 360) * Math.PI / 180;
            s.bottlenecks[i].polygon = shapePoly(sh);
            if (mc) mc.render();
          };
          q("rad").oninput = regen; q("ang").oninput = regen;
        }
        div.querySelector(".del").onclick = () => {
          pushDel("bottleneck", i, snap(s.bottlenecks[i]));
          s.bottlenecks.splice(i, 1); refresh();
        };
        box.appendChild(div);
      });
    })();
    // 출입구 — 유효폭 W(도면 자동 / 수동)·문별 q_design 인라인 편집 + C_j 표시.
    // C_j = W × q 는 파생값이라 직접 못 고친다. 사람이 조절하는 건 폭과 기준이다.
    (function fillExits() {
      const box = $("listExits"); box.innerHTML = "";
      $("cntExits").textContent = s.exits.length;
      const qdef = (s.thresholds && s.thresholds.q_design) || 60;
      s.exits.forEach((ex, i) => {
        const div = document.createElement("div");
        div.className = "elitem elitem-ex";
        const aw = autoWidthM(ex);                 // 도면 축척 기준 폭
        // 입력칸에는 **지금 실제로 쓰이는 값**을 넣는다. 비워두고 placeholder로
        // 자동값을 보여주면, 스피너(▲)를 누를 때 빈 값=0에서 시작해 min부터
        // 올라간다(4.98m 문에서 0.05가 찍힘). 상속값인지는 회색으로 구분한다.
        const meta = (e) => {
          const a = autoWidthM(e), man = e.width_m != null && e.width_m > 0;
          const cj = capacityOf(e);
          const src = man ? `수동 (도면 ${a != null ? a.toFixed(2) + "m" : "—"})`
                          : (a != null ? "도면 자동" : "폭 불명 — 직접 입력 필요");
          return `C ${cj != null ? cj + "명/분" : "—"} · ${src}`
               + `${(e.count_cam && (e.cam_line || e.cam_zone))
                    ? ` · ${e.count_cam} 화면 카운트` : " · 맵 카운트"}`;
        };
        const w = effWidthM(ex), q = effQ(ex);
        const wMan = ex.width_m != null && ex.width_m > 0;
        const qMan = ex.q_design != null && ex.q_design > 0;
        div.innerHTML =
          `<span class="swatch" style="background:${MC_COLORS.exit}"></span>` +
          `<span class="nm">${ex.name || ex.id}</span>` +
          `<label class="bfield" title="유효폭 W_eff — 회색이면 도면 축척 자동값. 고치면 그 값이 쓰입니다">W ` +
            `<input type="number" class="bni wm${wMan ? "" : " auto"}" min="0.05" step="0.05" ` +
            `value="${w != null ? w.toFixed(2) : ""}"> m</label>` +
          `<button class="tag-btn rst wrst" title="도면 자동값${aw != null ? ` (${aw.toFixed(2)}m)` : ""}으로 되돌리기"` +
            `${wMan ? "" : " disabled"}>↺</button>` +
          `<label class="bfield" title="이 문의 단위폭당 설계 통과기준 — 회색이면 사이트 기본값">q ` +
            `<input type="number" class="bni qd${qMan ? "" : " auto"}" min="1" step="1" ` +
            `value="${q}"> 인/분/m</label>` +
          `<button class="tag-btn rst qrst" title="사이트 기본값 (${qdef})으로 되돌리기"` +
            `${qMan ? "" : " disabled"}>↺</button>` +
          `<span class="exmeta">${meta(ex)}</span>` +
          `<button class="del" title="삭제">🗑</button>`;
        const wIn = div.querySelector("input.wm"), qIn = div.querySelector("input.qd");
        const wRst = div.querySelector(".wrst"), qRst = div.querySelector(".qrst");
        // 스피너를 연속으로 누르려면 행을 다시 그리면 안 된다(포커스가 날아간다)
        // → 목록 전체 refresh 대신 이 행의 표시만 갱신한다.
        const sync = () => {
          const e = s.exits[i];
          const wm = e.width_m != null && e.width_m > 0;
          const qm = e.q_design != null && e.q_design > 0;
          wIn.classList.toggle("auto", !wm);
          qIn.classList.toggle("auto", !qm);
          wRst.disabled = !wm; qRst.disabled = !qm;
          div.querySelector(".exmeta").textContent = meta(e);
        };
        wIn.oninput = () => {
          const v = parseFloat(wIn.value);
          s.exits[i].width_m = v > 0 ? v : null;
          sync();
        };
        qIn.oninput = () => {
          const v = parseFloat(qIn.value);
          s.exits[i].q_design = v > 0 ? v : null;
          sync();
        };
        wRst.onclick = () => {
          s.exits[i].width_m = null;
          const a = autoWidthM(s.exits[i]);
          wIn.value = a != null ? a.toFixed(2) : "";
          sync();
        };
        qRst.onclick = () => {
          s.exits[i].q_design = null;
          qIn.value = effQ(s.exits[i]);
          sync();
        };
        div.querySelector(".del").onclick = () => {
          pushDel("exit", i, snap(s.exits[i]));
          s.exits.splice(i, 1); refresh();
        };
        box.appendChild(div);
      });
    })();
    // 공간그래프 — 노드·엣지 요약 1행 + 전체 지우기
    const gbox = $("listGraph");
    const gr = s.graph || { nodes: [], edges: [] };
    gbox.innerHTML = "";
    $("cntGraph").textContent = gr.nodes.length;
    if (gr.nodes.length) {
      gbox.appendChild(elItem("공간그래프", `노드 ${gr.nodes.length} · 엣지 ${gr.edges.length}`,
        MC_COLORS.graph, () => {
          pushSnap("graph", s.graph);
          s.graph = { nodes: [], edges: [] }; graphSel = null; refresh();
        }));
    }
    $("graphMeta").textContent = gr.nodes.length
      ? "IDR 최단거리는 이 그래프 위에서 계산 (미지정 시 직선거리 폴백)"
      : "그래프 없음 — IDR은 직선거리 폴백으로 계산";
    // 파일명은 층마다 다르다(default=map.png, 그 외 map_<층>.png) — 고정 문자열
    // "map.png"를 쓰면 모든 층이 같은 이미지처럼 보인다.
    $("mapMeta").textContent = s.map ? `${s.map.image} · ${s.map.w}×${s.map.h}px`
                                     : "맵 없음 — 업로드하세요";
    $("scaleMeta").textContent = s.map && (s.map.scale || s.map.m_per_px != null)
      ? `축척: ${fmtScale()}` : "축척 미지정";
    // 출처 — 자동 축척이 어디서 나온 값인지 (없으면 줄 자체를 숨긴다)
    const prov = s.map && s.map.source
      ? `${s.map.source}${s.map.unit ? ` · 도면 단위 ${s.map.unit}` : ""}` : "";
    $("mapProv").textContent = prov;
    $("mapProv").classList.toggle("hidden", !prov);
    // thresholds
    const t = s.thresholds || {};
    $("thV").value = t.v_th; $("thA").value = t.a_th; $("thR").value = t.r_th;
    $("thDt").value = t.dt_hold; $("thD").value = t.d_allow;
    $("thC").value = t.min_conf != null ? t.min_conf : 0.35;
    $("thQd").value = t.q_design != null ? t.q_design : 60;
  }

  function refresh() { refreshLists(); renderFloorPanel(); syncUndoBtn(); if (mc) mc.render(); }

  // ------------------------------------------------------------ 추론 모델
  // 검출기·ReID 조합(백엔드 model_zoo.py의 '프로파일')을 여기서 고른다.
  // 전환하면 추론 계층만 재기동한다 — 사이트 설정·세션 녹화본은 그대로.
  let inferBusy = false;

  async function loadInfer(keepNote) {
    const box = $("inferList"), note = $("inferNote");
    box.innerHTML = "";
    let d;
    try { d = await (await fetch("/api/infer/profiles")).json(); }
    catch (e) { note.textContent = "추론 프로파일을 불러오지 못했습니다"; return; }
    // 지금 돌고 있는 것 한 줄 — 카드에도 표시되지만 "무엇이 적용 중인지"를
    // 스크롤 없이 바로 읽히게 한다.
    const cur = (d.profiles || []).find((p) => p.selected);
    $("inferCur").innerHTML = cur ? `지금 적용 중 — <b>${cur.label}</b>` : "—";
    (d.profiles || []).forEach((p) => {
      const el = document.createElement("div");
      el.className = "elitem infer" + (p.selected ? " on" : "") + (p.ready ? "" : " off");
      el.innerHTML = `<span class="dot"></span>
        <div class="grow"><b>${p.label}</b>
          <div class="sub">${p.detector}<br/>${p.reid}<br/>${p.tracker}</div>
          ${p.ready ? "" : `<div class="sub warn">엔진 없음: ${p.missing.join(", ")}</div>`}
        </div>`;
      el.title = p.note || "";
      if (!p.selected && p.ready) el.onclick = () => applyInfer(p);
      box.appendChild(el);
    });
    note.textContent = keepNote || `인제스트 백엔드: ${d.backend}`;
  }

  async function applyInfer(p) {
    if (inferBusy) return;
    if (!confirm(`추론 모델을 "${p.label}" 로 바꿉니다.\n\n`
      + "추론 계층이 재기동되며 진행 중인 추적 상태(트랙 ID)는 초기화됩니다.\n"
      + "평가 세션이 진행 중이면 전환되지 않습니다. 계속할까요?")) return;
    inferBusy = true;
    let msg = "";
    const note = $("inferNote");
    note.textContent = "전환 중 — 엔진 로드에 수십 초 걸릴 수 있습니다…";
    try {
      const r = await fetch("/api/infer/profile", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: p.id }),
      });
      const j = await r.json().catch(() => ({}));
      msg = r.ok ? `✔ 적용됨 — ${j.label} (${j.restarted})`
                 : `전환 실패 — ${j.detail || r.status}`;
    } catch (e) {
      msg = "전환 실패 — " + e;
    } finally {
      inferBusy = false;
      loadInfer(msg);
    }
  }

  // ------------------------------------------------------------ 도면(층) 관리
  function floorMsg(msg, warn) {
    const el = $("floorMsg");
    if (!el) return;
    el.textContent = msg || "";
    el.classList.toggle("warn", !!warn);
    if (msg) setTimeout(() => { if (el.textContent === msg) el.textContent = ""; }, 4000);
  }

  function renderFloorPanel() {
    const floors = (App.site && App.site.floors) || [];
    $("cntFloors").textContent = floors.length;
    const sel = $("floorEditSel");
    sel.innerHTML = floors.map((f) =>
      `<option value="${f.id}"${f.id === App.currentFloor ? " selected" : ""}>${f.name || f.id}</option>`
    ).join("");
    $("floorNameInp").value = (App.floor && App.floor.name) || "";
    $("floorDelBtn").disabled = (App.currentFloor === "default" || floors.length <= 1);
  }

  async function addFloor() {
    try {
      App.syncFloor();
      const n = ((App.site && App.site.floors) || []).length + 1;
      const summary = await API.addFloor(`${n}층`);
      await App.reloadSite();
      await App.setFloor(summary.id);              // 새 층으로 전환 → 뷰 재진입
      floorMsg(`새 층 추가됨: ${summary.name || summary.id}`);
    } catch (e) { floorMsg("층 추가 실패: " + e.message, true); }
  }

  async function delFloor() {
    const fid = App.currentFloor;
    if (fid === "default") { floorMsg("기본 층은 삭제할 수 없습니다.", true); return; }
    if (!confirm(`'${App.floorName(fid)}' 층을 삭제할까요? 소속 카메라는 기본 층으로 재배정됩니다.`)) return;
    try {
      await API.deleteFloor(fid);
      App.currentFloor = "default";
      await App.reloadCameras();
      await App.reloadSite();
      enter();                                     // 기본 층으로 재드로잉
      floorMsg("층 삭제됨 — 기본 층으로 전환.");
    } catch (e) { floorMsg("삭제 실패: " + e.message, true); }
  }

  async function renameFloor() {
    if (!App.floor) return;
    App.floor.name = $("floorNameInp").value.trim();
    try {
      App.syncFloor();
      App.site = await API.putSite(App.site);
      if (!App.site.floors || !App.site.floors.length) App.site.floors = [App.floor];
      App.applyFloor();
      App.updateChip(); App.renderFloorSelector();
      floorMsg("층 이름 저장됨.");
      refresh();
    } catch (e) { floorMsg("이름 저장 실패: " + e.message, true); }
  }

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
    // 출입구 C_j = W_eff × q_design (문별 폭·기준 반영). 백엔드도 저장 시
    // 같은 식으로 다시 계산하므로 여기 값은 저장 전 표시용이다.
    (s.exits || []).forEach((ex) => {
      const cj = capacityOf(ex);
      if (cj != null) ex.design_capacity = cj;
    });
    // 다중 도면: 별칭된 최상위 공간요소를 현재 층에 반영 후, floors 통째로 저장.
    // (top-level만 보내면 백엔드 재승격으로 다른 층이 사라짐 — floors 포함 필수)
    App.syncFloor();
    try {
      App.site = await API.putSite(s);
      if (!App.site.floors || !App.site.floors.length) App.site.floors = [App.floor];
      App.applyFloor();                              // 저장 후 현재 층 별칭 재설정
      App.updateChip(); App.renderFloorSelector();
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
      await API.uploadMap(img, meta, App.currentFloor);  // 현재 층에 업로드
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
      onClick, onDragDraw, onHover,
      onDragEnd: () => {},
      onDblClick: (p) => {
        if (tool === "graph") { graphDblClick(p); return; }
        finishDraft();
      },
      draw: overlay,
    });
    document.querySelectorAll("#mapTools .tag-btn").forEach((b) =>
      b.onclick = () => setTool(b.dataset.tool));
    $("drawDone").onclick = finishDraft;
    $("drawCancel").onclick = cancelDraft;
    $("drawUndo").onclick = undo;
    // Ctrl+Z — 이 화면이 열려 있고 입력칸에 포커스가 없을 때만
    // (텍스트 입력 중의 Ctrl+Z는 브라우저 기본 되돌리기를 그대로 둔다)
    window.addEventListener("keydown", (e) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z") return;
      if (App.view !== "map") return;
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
      e.preventDefault();
      undo();
    });
    syncUndoBtn();
    // 우측 패널: [설정] · [추론 모델] · [지표 설명] 토글
    const MS_TABS = { set: "mapSetPanel", model: "mapModelPanel", help: "mapHelpPanel" };
    const MS_BTN = { set: "msTabSet", model: "msTabModel", help: "msTabHelp" };
    const msTab = (which) => {
      Object.entries(MS_TABS).forEach(([k, id]) =>
        $(id).classList.toggle("hidden", k !== which));
      Object.entries(MS_BTN).forEach(([k, id]) =>
        $(id).classList.toggle("on", k === which));
      if (which === "model") loadInfer();     // 열 때마다 현재 적용값 재확인
    };
    Object.keys(MS_TABS).forEach((k) => { $(MS_BTN[k]).onclick = () => msTab(k); });
    // 도면(층) 관리 — 셀렉터/추가/삭제/이름
    $("floorEditSel").onchange = () => App.setFloor($("floorEditSel").value);
    $("floorAddBtn").onclick = addFloor;
    $("floorDelBtn").onclick = delFloor;
    $("floorRenameBtn").onclick = renameFloor;
    $("floorNameInp").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); renameFloor(); } };
    $("siteSave").onclick = save;
    $("siteReset").onclick = async () => {
      if (!confirm("현재 사이트 설정을 커밋된 디폴트(seed)로 되돌립니다.\n\n"
        + "· 지금까지 바꾼 카메라·경로·구역·병목·매핑이 전부 사라지고\n"
        + "  기본값(3개 층 · cam01~03)으로 복원됩니다.\n"
        + "· 세션 녹화본은 보존됩니다.\n\n정말 초기화할까요?")) return;
      $("mapSaveMsg").textContent = "디폴트로 초기화 중… (인제스트 재기동)";
      try {
        await API.resetSeed();
        await App.reloadCameras();
        await App.reloadSite();
        refresh();
        $("mapSaveMsg").textContent = `디폴트로 초기화됨 · v${App.site.version}`;
        setTimeout(() => { $("mapSaveMsg").textContent = ""; }, 5000);
      } catch (e) {
        $("mapSaveMsg").textContent = "초기화 실패: " + e.message;
      }
    };
    $("mapUpload").onchange = (e) => { if (e.target.files.length) upload(e.target.files); };
    // CAD 도면 편집기(:8910, 별도 서비스) — 새 창에서 터치업·Exit 지정 후 [저장 & 적용]하면
    // postMessage('evac-floor-applied')로 돌아와 아래 리스너가 맵을 자동 갱신한다.
    $("mapFromCad").onclick = () => {
      // 현재 선택된 층(App.currentFloor)을 편집기에 전달 → 그 층 맵으로 저장·반영
      const floor = App.currentFloor || "default";
      const url = location.protocol + "//" + location.hostname
                + ":8910/?live=1&floor=" + encodeURIComponent(floor);
      const w = window.open(url, "evacFloorEditor", "width=1560,height=980");
      const fname = (App.floor && App.floor.name) || floor;
      if (!w || w.closed || typeof w.closed === "undefined") {
        // 팝업 차단 — 안내문만 띄우면 "아무 일도 안 일어난 것"처럼 보인다.
        // 바로 누를 수 있는 링크를 함께 준다.
        const el = $("mapHint");
        el.classList.add("warn");
        el.innerHTML = `팝업이 차단되어 편집기 창이 열리지 않았습니다 — `
          + `<a href="${url}" target="_blank" rel="noopener" `
          + `style="color:var(--pia-cyan);text-decoration:underline">여기를 눌러 «${fname}» 층 편집기 열기</a>`
          + ` (또는 주소창 오른쪽 팝업 차단 아이콘에서 허용)`;
        return;
      }
      hint(`도면 편집기(새 창)에서 정리·Exit 지정 후 [저장 & 적용]하면 «${fname}» 층 맵에 반영됩니다.`);
    };
    window.addEventListener("message", async (ev) => {
      if (!ev.data || ev.data.type !== "evac-floor-applied") return;
      try {
        // 편집기가 서버를 이미 바꿨다 — 브라우저가 들고 있는 값은 낡았다.
        // 먼저 서버에서 다시 읽는다. (전에는 '다른 층이면' setFloor만 불렀는데,
        // setFloor는 서버를 안 읽고 syncFloor로 낡은 값을 되돌리기까지 해서
        // 옛 경로·구역이 그대로 보였고 [저장] 시 CAD 결과를 덮어썼다.)
        const applied = ev.data.floor || "default";
        await App.reloadSite();
        if (applied !== App.currentFloor
            && (App.site.floors || []).some((f) => f.id === applied)) {
          await App.setFloor(applied, { discardEdits: true });
        }
        if (App.floor && App.floor.map) mc.setImage(App.mapImg, App.floor.map.w, App.floor.map.h);
        hint("도면 편집기 적용 완료 — 맵·축척(m/px)이 자동 반영되었습니다.");
        refresh();
      } catch (e) { hint("맵 갱신 실패: " + e.message, true); }
    });
    $("scaleMeters").onchange = () => {              // 이미 지정된 축척의 실거리 갱신
      const m = parseFloat($("scaleMeters").value);
      if (App.site.map && App.site.map.scale && m > 0) {
        App.site.map.scale.meters = m; refresh();
      }
    };
    $("gridToggle").onclick = () => {
      gridOn = !gridOn;
      $("gridToggle").classList.toggle("on", gridOn);
      if (!mPerPx()) hint("축척이 없어 격자를 그릴 수 없습니다 — CAD 적용 또는 [축척 2점].", true);
      if (mc) mc.render();
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
    loadInfer();
  }

  return { enter, leave: () => {}, refresh };
})();
