/* 줌(휠)·팬(드래그) 지원 캔버스 + 공용 드로잉 헬퍼.
 * 좌표 규약: 저장·전송·콜백은 전부 "맵(이미지) 원본 px" — 표시 배율(s)과 분리.
 * 좌클릭(이동량 < 5px) = onClick(맵 px), 좌드래그 = 팬(freehand 모드에선 자유곡선). */
"use strict";

class MapCanvas {
  constructor(cv, opts = {}) {
    this.cv = cv;
    this.ctx = cv.getContext("2d");
    this.opts = opts;                 // {onClick, onDblClick, onDragDraw, onDragEnd, draw}
    this.img = null;
    this.w = 1000; this.h = 600;      // 원본(맵) px 크기
    this.s = 1; this.ox = 0; this.oy = 0;
    this.dpr = 1; this.cw = 0; this.ch = 0;
    this.freehand = false;            // true면 좌드래그가 팬 대신 자유곡선 드로잉
    this._down = null; this._moved = false; this._drawing = false;
    this._fitted = false;

    cv.addEventListener("wheel", (e) => this._wheel(e), { passive: false });
    cv.addEventListener("mousedown", (e) => this._md(e));
    window.addEventListener("mousemove", (e) => this._mm(e));
    window.addEventListener("mouseup", (e) => this._mu(e));
    cv.addEventListener("dblclick", (e) => {
      e.preventDefault();
      if (this.opts.onDblClick) this.opts.onDblClick(this.toMap(e), e);
    });
    cv.addEventListener("contextmenu", (e) => e.preventDefault());
    this._ro = new ResizeObserver(() => this.resize());
    this._ro.observe(cv);
    this.resize();
  }

  resize() {
    const r = this.cv.getBoundingClientRect();
    if (!r.width || !r.height) return;
    this.dpr = window.devicePixelRatio || 1;
    this.cw = r.width; this.ch = r.height;
    this.cv.width = Math.round(r.width * this.dpr);
    this.cv.height = Math.round(r.height * this.dpr);
    if (!this._fitted) { this.fit(); this._fitted = true; }
    this.render();
  }

  /** 배경 이미지(또는 null) + 원본 크기 지정 후 화면 맞춤. */
  setImage(img, w, h) {
    this.img = img || null;
    this.w = w || (img ? img.naturalWidth : 1000);
    this.h = h || (img ? img.naturalHeight : 600);
    this.fit(); this.render();
  }

  fit() {
    if (!this.cw) return;
    const pad = 14;
    this.s = Math.min((this.cw - 2 * pad) / this.w, (this.ch - 2 * pad) / this.h);
    if (!isFinite(this.s) || this.s <= 0) this.s = 1;
    this.ox = (this.cw - this.w * this.s) / 2;
    this.oy = (this.ch - this.h * this.s) / 2;
  }

  TX(x) { return this.ox + x * this.s; }
  TY(y) { return this.oy + y * this.s; }

  /** 마우스 이벤트 → 맵 원본 px (경계로 클램프). */
  toMap(e) {
    const r = this.cv.getBoundingClientRect();
    const x = (e.clientX - r.left - this.ox) / this.s;
    const y = (e.clientY - r.top - this.oy) / this.s;
    return { x: Math.min(Math.max(x, 0), this.w),
             y: Math.min(Math.max(y, 0), this.h) };
  }

  _wheel(e) {
    e.preventDefault();
    const r = this.cv.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    const f = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const ns = Math.min(30, Math.max(0.03, this.s * f));
    this.ox = sx - (sx - this.ox) * (ns / this.s);
    this.oy = sy - (sy - this.oy) * (ns / this.s);
    this.s = ns;
    this.render();
  }

  _md(e) {
    if (e.button !== 0 && e.button !== 1) return;
    this._down = { x: e.clientX, y: e.clientY, ox: this.ox, oy: this.oy, btn: e.button };
    this._moved = false;
    if (e.button === 0 && this.freehand && this.opts.onDragDraw) {
      this._drawing = true;
      this.opts.onDragDraw(this.toMap(e), true);
      this.render();
    }
  }

  _mm(e) {
    if (!this._down) return;
    const dx = e.clientX - this._down.x, dy = e.clientY - this._down.y;
    if (Math.hypot(dx, dy) > 5) this._moved = true;
    if (this._drawing) {
      if (this._moved) { this.opts.onDragDraw(this.toMap(e), false); this.render(); }
      return;
    }
    if (this._moved) {                              // 팬
      this.ox = this._down.ox + dx;
      this.oy = this._down.oy + dy;
      this.render();
    }
  }

  _mu(e) {
    if (!this._down) return;
    const btn = this._down.btn, moved = this._moved, drawing = this._drawing;
    this._down = null; this._drawing = false;
    if (drawing) {
      if (moved && this.opts.onDragEnd) this.opts.onDragEnd();
      else if (!moved && this.opts.onClick) this.opts.onClick(this.toMap(e), e);
      this.render();
      return;
    }
    if (!moved && btn === 0 && this.opts.onClick) {
      this.opts.onClick(this.toMap(e), e);
      this.render();
    }
  }

  render() {
    const c = this.ctx;
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    c.clearRect(0, 0, this.cw, this.ch);
    c.fillStyle = "#0d0d0d";
    c.fillRect(0, 0, this.cw, this.ch);
    const X = this.TX(0), Y = this.TY(0), W = this.w * this.s, H = this.h * this.s;
    if (this.img) {
      c.drawImage(this.img, X, Y, W, H);
    } else {                                        // 이미지 없음: 흰 캔버스 + 격자
      c.fillStyle = "#f5f5f5"; c.fillRect(X, Y, W, H);
      c.strokeStyle = "#dddddd"; c.lineWidth = 1;
      const step = 100 * this.s;
      for (let gx = X; gx <= X + W + 0.5; gx += step) {
        c.beginPath(); c.moveTo(gx, Y); c.lineTo(gx, Y + H); c.stroke();
      }
      for (let gy = Y; gy <= Y + H + 0.5; gy += step) {
        c.beginPath(); c.moveTo(X, gy); c.lineTo(X + W, gy); c.stroke();
      }
    }
    c.strokeStyle = "#525252"; c.lineWidth = 1;
    c.strokeRect(X - 0.5, Y - 0.5, W + 1, H + 1);
    if (this.opts.draw) {
      this.opts.draw({ ctx: c, TX: (x) => this.TX(x), TY: (y) => this.TY(y),
                       s: this.s, mc: this });
    }
  }

  destroy() { this._ro.disconnect(); }
}

/* ================================ 공용 드로잉 헬퍼 (인자는 맵 원본 px) */

const MC_COLORS = {
  route: "#30DCFB", zone: "#28A138", bottleneck: "#FF6F21",
  exit: "#FF4A44", scale: "#2E90FA", roi: "#30DCFB", over: "#FF4A44",
  graph: "#B48CFF",
};

/** IDR 공간그래프 렌더 — 엣지(점선)·노드(원+id). opts.sel = 엣지 연결용 선택 노드. */
function drawGraph(g, graph, opts = {}) {
  if (!graph || !graph.nodes || !graph.nodes.length) return;
  const { ctx, TX, TY } = g;
  const col = MC_COLORS.graph;
  const pos = {};
  graph.nodes.forEach((n) => { pos[n.id] = [TX(n.xy[0]), TY(n.xy[1])]; });
  ctx.globalAlpha = opts.faint ? 0.45 : 1.0;
  ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.setLineDash([6, 4]);
  (graph.edges || []).forEach(([a, b]) => {
    if (!pos[a] || !pos[b]) return;
    ctx.beginPath(); ctx.moveTo(pos[a][0], pos[a][1]); ctx.lineTo(pos[b][0], pos[b][1]); ctx.stroke();
  });
  ctx.setLineDash([]);
  graph.nodes.forEach((n) => {
    const [x, y] = pos[n.id];
    const selected = opts.sel === n.id;
    ctx.fillStyle = selected ? "#fff" : col;
    ctx.beginPath(); ctx.arc(x, y, selected ? 6 : 4.5, 0, 7); ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,.5)"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(x, y, selected ? 6 : 4.5, 0, 7); ctx.stroke();
    if (g.s > 0.35 || selected) {
      ctx.font = "10px Pretendard, sans-serif";
      ctx.fillStyle = col;
      ctx.fillText(n.id, x + 7, y - 6);
    }
  });
  ctx.globalAlpha = 1.0;
}

function camColor(camId, cams) {
  let idx = (cams || []).findIndex((c) => c.cam_id === camId);
  if (idx < 0) {
    idx = 0;
    for (let i = 0; i < camId.length; i++) idx = (idx * 31 + camId.charCodeAt(i)) | 0;
    idx = Math.abs(idx);
  }
  return `hsl(${(idx * 67 + 190) % 360},75%,60%)`;
}

function mcPath(g, pts, close) {
  const { ctx, TX, TY } = g;
  ctx.beginPath();
  pts.forEach((p, i) => {
    const x = TX(p[0] !== undefined ? p[0] : p.x), y = TY(p[1] !== undefined ? p[1] : p.y);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  if (close && pts.length >= 3) ctx.closePath();
}

function mcArrowHead(ctx, x, y, ang, size, color) {
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x - size * Math.cos(ang - 0.45), y - size * Math.sin(ang - 0.45));
  ctx.lineTo(x - size * Math.cos(ang + 0.45), y - size * Math.sin(ang + 0.45));
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
}

/** 번호 달린 점 목록 (대응점·꼭짓점). colorFn(i) 지정 시 점마다 색. */
function mcNumbered(g, pts, color, colorFn) {
  const { ctx, TX, TY } = g;
  pts.forEach((p, i) => {
    const x = TX(p[0] !== undefined ? p[0] : p.x), y = TY(p[1] !== undefined ? p[1] : p.y);
    const c = colorFn ? colorFn(i) : color;
    ctx.fillStyle = c;
    ctx.beginPath(); ctx.arc(x, y, 5, 0, 7); ctx.fill();
    ctx.strokeStyle = "#111"; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(x, y, 5, 0, 7); ctx.stroke();
    ctx.font = "bold 12px Pretendard, sans-serif";
    ctx.fillStyle = "#111";
    ctx.fillText(String(i + 1), x + 8, y - 7);
    ctx.fillStyle = c;
    ctx.fillText(String(i + 1), x + 7, y - 8);
  });
}

/** 사이트 요소(경로·구역·병목·출입구·축척) 렌더 — 3개 화면 공용.
 * opts: {faint, state(MapState: 상태 하이라이트/카운트), showScale} */
function drawSiteElements(g, site, opts = {}) {
  if (!site) return;
  const { ctx, TX, TY } = g;
  const a = opts.faint ? 0.35 : 1.0;
  const lab = (txt, x, y, color) => {
    ctx.font = "bold 11px Pretendard, sans-serif";
    const w = ctx.measureText(txt).width + 10;
    ctx.fillStyle = "rgba(17,17,17,.75)";
    ctx.fillRect(x - w / 2, y - 9, w, 17);
    ctx.fillStyle = color;
    ctx.fillText(txt, x - w / 2 + 5, y + 4);
  };
  const centroid = (poly) => {
    let cx = 0, cy = 0;
    poly.forEach((p) => { cx += p[0]; cy += p[1]; });
    return [cx / poly.length, cy / poly.length];
  };
  const st = opts.state || null;
  const find = (list, id) => (list || []).find((e) => e.id === id);

  // 구역
  (site.zones || []).forEach((z) => {
    ctx.globalAlpha = a;
    mcPath(g, z.polygon, true);
    ctx.fillStyle = "rgba(40,161,56,.12)"; ctx.fill();
    ctx.strokeStyle = MC_COLORS.zone; ctx.lineWidth = 1.5; ctx.stroke();
    const [cx, cy] = centroid(z.polygon);
    const zs = st && find(st.zones, z.id);
    const txt = zs ? `${z.name || z.id} · ${zs.count}명${zs.density != null ? ` · ${zs.density}/m²` : ""}`
                   : (z.name || z.id);
    lab(txt, TX(cx), TY(cy), MC_COLORS.zone);
    ctx.globalAlpha = 1;
  });

  // 병목 (over → 붉은 하이라이트)
  (site.bottlenecks || []).forEach((b) => {
    const bs = st && find(st.bottlenecks, b.id);
    const over = !!(bs && bs.over);
    ctx.globalAlpha = a;
    mcPath(g, b.polygon, true);
    ctx.fillStyle = over ? "rgba(255,74,68,.32)" : "rgba(255,111,33,.12)";
    ctx.fill();
    ctx.strokeStyle = over ? MC_COLORS.over : MC_COLORS.bottleneck;
    ctx.lineWidth = over ? 3 : 1.5;
    ctx.stroke();
    const [cx, cy] = centroid(b.polygon);
    const txt = bs ? `${b.name || b.id} · ${bs.count}명${bs.density != null ? ` · ${bs.density}/m²` : ""}${over ? " ⚠" : ""}`
                   : `${b.name || b.id} · ρ임계 ${b.rho_crit}`;
    lab(txt, TX(cx), TY(cy), over ? MC_COLORS.over : MC_COLORS.bottleneck);
    ctx.globalAlpha = 1;
  });

  // 피난경로 (진행 화살표)
  (site.routes || []).forEach((r) => {
    ctx.globalAlpha = a;
    mcPath(g, r.points, false);
    ctx.strokeStyle = MC_COLORS.route; ctx.lineWidth = 2.5;
    ctx.setLineDash([8, 5]); ctx.stroke(); ctx.setLineDash([]);
    const pts = r.points;
    for (let i = 1; i < pts.length; i += Math.max(1, Math.floor(pts.length / 4))) {
      const ang = Math.atan2(TY(pts[i][1]) - TY(pts[i - 1][1]),
                             TX(pts[i][0]) - TX(pts[i - 1][0]));
      mcArrowHead(ctx, TX(pts[i][0]), TY(pts[i][1]), ang, 9, MC_COLORS.route);
    }
    lab(r.name || r.id, TX(pts[0][0]), TY(pts[0][1]) - 14, MC_COLORS.route);
    ctx.globalAlpha = 1;
  });

  // 출입구 통과선 + inside 표시 + in/out 뱃지
  (site.exits || []).forEach((e) => {
    ctx.globalAlpha = a;
    const [p1, p2] = e.line;
    ctx.strokeStyle = MC_COLORS.exit; ctx.lineWidth = 3;
    ctx.beginPath(); ctx.moveTo(TX(p1[0]), TY(p1[1])); ctx.lineTo(TX(p2[0]), TY(p2[1])); ctx.stroke();
    [p1, p2].forEach((p) => {
      ctx.fillStyle = MC_COLORS.exit;
      ctx.beginPath(); ctx.arc(TX(p[0]), TY(p[1]), 4, 0, 7); ctx.fill();
    });
    if (e.inside) {                                  // 안쪽 방향 짧은 화살표
      const mx = (p1[0] + p2[0]) / 2, my = (p1[1] + p2[1]) / 2;
      const ang = Math.atan2(TY(e.inside[1]) - TY(my), TX(e.inside[0]) - TX(mx));
      const ex = TX(mx) + 20 * Math.cos(ang), ey = TY(my) + 20 * Math.sin(ang);
      ctx.strokeStyle = MC_COLORS.exit; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(TX(mx), TY(my)); ctx.lineTo(ex, ey); ctx.stroke();
      mcArrowHead(ctx, ex, ey, ang, 7, MC_COLORS.exit);
      ctx.font = "10px Pretendard, sans-serif"; ctx.fillStyle = MC_COLORS.exit;
      ctx.fillText("안", ex + 5, ey + 3);
    }
    const es = st && find(st.exits, e.id);
    const mx = (p1[0] + p2[0]) / 2, my = (p1[1] + p2[1]) / 2;
    const txt = es ? `${e.name || e.id} · IN ${es.in_count} / OUT ${es.out_count}`
                   : (e.name || e.id);
    lab(txt, TX(mx), TY(my) - 16, MC_COLORS.exit);
    ctx.globalAlpha = 1;
  });

  // 축척 2점
  if (opts.showScale && site.map && site.map.scale) {
    const sc = site.map.scale;
    ctx.strokeStyle = MC_COLORS.scale; ctx.lineWidth = 2; ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(TX(sc.p1[0]), TY(sc.p1[1]));
    ctx.lineTo(TX(sc.p2[0]), TY(sc.p2[1]));
    ctx.stroke(); ctx.setLineDash([]);
    [sc.p1, sc.p2].forEach((p) => {
      ctx.fillStyle = MC_COLORS.scale;
      ctx.beginPath(); ctx.arc(TX(p[0]), TY(p[1]), 4, 0, 7); ctx.fill();
    });
    const mx = (TX(sc.p1[0]) + TX(sc.p2[0])) / 2, my = (TY(sc.p1[1]) + TY(sc.p2[1])) / 2;
    lab(`${sc.meters} m`, mx, my - 12, MC_COLORS.scale);
  }
}
