/* sim.js — MACS 2.0 people-tracking simulation.
   Drives the on-video detection overlays AND every dashboard metric from a
   single coherent state, so counts / speed / acceleration / dwell all agree.
   Plain JS (no JSX) — attaches window.PeopleSim. */
(function () {
  "use strict";

  // Camera covers roughly this real-world floor area (metres).
  const FRAME_W_M = 14;
  const FRAME_H_M = 9;
  const M_PER_X = FRAME_W_M / 100; // metres per 1% of frame width
  const M_PER_Y = FRAME_H_M / 100;

  // Walkable region in frame % (leaves a back-wall band up top).
  const BX = [9, 91];
  const BY = [24, 88];

  const rnd = (a, b) => a + Math.random() * (b - a);
  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
  const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

  const ZONES = [
    // Polygons in frame % — an entry corridor and a dwell/queue zone.
    { id: "entry", label: "Entry", kind: "entry",
      poly: [[10, 26], [40, 26], [36, 86], [11, 86]] },
    { id: "dwell", label: "Queue", kind: "dwell",
      poly: [[58, 40], [88, 40], [88, 84], [55, 84]] },
  ];

  function pointInPoly(px, py, poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const xi = poly[i][0], yi = poly[i][1];
      const xj = poly[j][0], yj = poly[j][1];
      const hit = (yi > py) !== (yj > py) &&
        px < ((xj - xi) * (py - yi)) / (yj - yi) + xi;
      if (hit) inside = !inside;
    }
    return inside;
  }

  let nextId = 1;
  function makeAgent(spawnEdge) {
    const id = nextId++;
    // Spawn just inside an edge.
    let x, y;
    const e = spawnEdge != null ? spawnEdge : Math.floor(rnd(0, 4));
    if (e === 0) { x = BX[0] + 1; y = rnd(BY[0], BY[1]); }
    else if (e === 1) { x = BX[1] - 1; y = rnd(BY[0], BY[1]); }
    else if (e === 2) { x = rnd(BX[0], BX[1]); y = BY[0] + 1; }
    else { x = rnd(BX[0], BX[1]); y = BY[1] - 1; }
    return {
      id,
      pid: "P-" + String(id).padStart(2, "0"),
      x, y,
      speed: rnd(0.7, 1.3),          // current speed, m/s
      accel: 0,                       // instantaneous accel, m/s^2
      base: rnd(1.0, 1.6),           // preferred walking speed
      dir: { x: 0, y: 0 },
      wp: null,                       // current waypoint
      pauseT: 0,                      // remaining pause seconds (dwell)
      entry: performance.now(),
      lifespan: rnd(14, 34) * 1000,   // ms before it heads for an exit
      born: performance.now(),
      exiting: false,
      flag: null,                     // 'run' | 'loiter' | null
      trail: [],
      zone: null,
    };
  }

  function newWaypoint(a) {
    // ~25% chance to pick a dwell point inside the queue zone & pause.
    if (Math.random() < 0.28) {
      const z = ZONES[1];
      return { x: rnd(60, 85), y: rnd(46, 80), pause: rnd(2.5, 7) };
    }
    return { x: rnd(BX[0] + 2, BX[1] - 2), y: rnd(BY[0] + 2, BY[1] - 2), pause: 0 };
  }

  class PeopleSim {
    constructor() {
      this.agents = [];
      this.cumulative = 1280;     // cumulative count "today"
      this.maxCount = 7;
      this.minCount = 3;
      this.history = [];          // rolling people-count samples for sparkline
      this.speedHist = [];
      this.alerts = [];           // recent alert events
      this._sampleAcc = 0;
      // seed
      const n = Math.round(rnd(this.minCount, this.maxCount));
      for (let i = 0; i < n; i++) {
        const a = makeAgent();
        a.entry = performance.now() - rnd(0, 9000);
        this.agents.push(a);
      }
    }

    _spawn() {
      const a = makeAgent();
      this.agents.push(a);
      this.cumulative += 1;
    }

    step(dtMs) {
      const dt = dtMs / 1000;
      const now = performance.now();

      // population control
      if (this.agents.length < this.minCount) this._spawn();
      else if (this.agents.length < this.maxCount && Math.random() < 0.012) this._spawn();

      for (const a of this.agents) {
        // decide to exit after lifespan
        if (!a.exiting && now - a.born > a.lifespan) {
          a.exiting = true;
          // nearest edge as target
          const ex = (a.x - BX[0]) < (BX[1] - a.x) ? BX[0] - 4 : BX[1] + 4;
          a.wp = { x: ex, y: a.y, pause: 0 };
          a.pauseT = 0;
        }

        // waypoint logic
        if (!a.wp) a.wp = newWaypoint(a);
        if (a.pauseT > 0) {
          a.pauseT -= dt;
          a.flag = a.flag === "run" ? null : "loiter";
        } else if (dist(a, a.wp) < 2) {
          if (a.exiting) { a._dead = true; continue; }
          if (a.wp.pause > 0) { a.pauseT = a.wp.pause; }
          a.wp = a.exiting ? a.wp : newWaypoint(a);
          if (a.flag === "loiter") a.flag = null;
        }

        // desired direction & speed
        const dx = a.wp.x - a.x, dy = a.wp.y - a.y;
        const len = Math.hypot(dx, dy) || 1;
        a.dir = { x: dx / len, y: dy / len };
        // gentle continuous speed variation -> believable non-zero acceleration
        a._noise = clamp((a._noise || 0) + rnd(-0.05, 0.05), -0.32, 0.32);
        let desired = a.pauseT > 0 ? 0 : a.base + Math.sin(now / 640 + a.id * 1.7) * 0.22 + a._noise;
        if (desired < 0.2 && a.pauseT <= 0) desired = 0.2;
        // occasional sprint
        if (!a.exiting && a.flag !== "run" && Math.random() < 0.0008) a.flag = "run";
        if (a.flag === "run") desired = rnd(2.4, 3.1);
        if (a.flag === "run" && Math.random() < 0.01) a.flag = null;

        // accelerate toward desired (limit accel)
        const maxA = 2.2; // m/s^2
        const prev = a.speed;
        const dv = clamp(desired - a.speed, -maxA * dt, maxA * dt);
        a.speed = Math.max(0, a.speed + dv);
        a.accel = (a.speed - prev) / dt;

        // move (convert m/s into %/s along the two axes)
        const vxPct = (a.dir.x * a.speed) / M_PER_X;
        const vyPct = (a.dir.y * a.speed) / M_PER_Y;
        a.x = a.x + vxPct * dt;
        a.y = a.y + vyPct * dt;
        if (!a.exiting) { a.x = clamp(a.x, BX[0], BX[1]); a.y = clamp(a.y, BY[0], BY[1]); }

        // zone test
        a.zone = null;
        for (const z of ZONES) if (pointInPoly(a.x, a.y, z.poly)) { a.zone = z.id; break; }

        // trail
        a.trail.push({ x: a.x, y: a.y });
        if (a.trail.length > 14) a.trail.shift();

        // alert detection (running) -> raise once
        if (a.flag === "run" && !a._alerted) {
          a._alerted = true;
          this.alerts.unshift({
            id: "EVT-" + Math.floor(rnd(1000, 9999)),
            pid: a.pid, kind: "run",
            label: "Rapid movement",
            t: this._clock(),
            conf: rnd(0.9, 0.98),
          });
          if (this.alerts.length > 6) this.alerts.pop();
        }
        if (a.flag !== "run") a._alerted = false;
      }

      // reap dead
      this.agents = this.agents.filter((a) => !a._dead);

      // sample history ~2x/sec
      this._sampleAcc += dtMs;
      if (this._sampleAcc >= 500) {
        this._sampleAcc = 0;
        this.history.push(this.agents.length);
        if (this.history.length > 60) this.history.shift();
        const m = this.metrics();
        this.speedHist.push(m.avgSpeed);
        if (this.speedHist.length > 60) this.speedHist.shift();
      }
    }

    _clock() {
      const base = 53288 + Math.floor((performance.now() - this._t0) / 1000 || 0);
      const s = base % 60, mn = Math.floor(base / 60) % 60, h = Math.floor(base / 3600) % 24;
      const p = (n) => String(n).padStart(2, "0");
      return p(h) + ":" + p(mn) + ":" + p(s);
    }

    metrics() {
      const A = this.agents;
      const n = A.length;
      const now = performance.now();
      const speeds = A.map((a) => a.speed);
      const avgSpeed = n ? speeds.reduce((s, v) => s + v, 0) / n : 0;
      const maxSpeed = n ? Math.max(...speeds) : 0;
      const avgAccel = n ? A.reduce((s, a) => s + Math.abs(a.accel), 0) / n : 0;
      const dwellSecs = A.map((a) => (now - a.entry) / 1000);
      const avgDwell = n ? dwellSecs.reduce((s, v) => s + v, 0) / n : 0;
      const maxDwell = n ? Math.max(...dwellSecs) : 0;
      const inQueue = A.filter((a) => a.zone === "dwell").length;
      // density: people per 100 m^2
      const area = FRAME_W_M * FRAME_H_M; // ~126 m^2
      const density = (n / area) * 100;
      let level = "Low", levelKr = "여유";
      if (density > 4.8) { level = "High"; levelKr = "혼잡"; }
      else if (density > 2.6) { level = "Normal"; levelKr = "보통"; }
      return { n, avgSpeed, maxSpeed, avgAccel, avgDwell, maxDwell, inQueue, density, level, levelKr };
    }

    snapshot() {
      return { agents: this.agents, zones: ZONES, metrics: this.metrics(),
        cumulative: this.cumulative, history: this.history.slice(),
        speedHist: this.speedHist.slice(), alerts: this.alerts.slice() };
    }
  }

  PeopleSim.prototype._t0 = performance.now();
  window.PeopleSim = PeopleSim;
  window.SIM_ZONES = ZONES;
})();
