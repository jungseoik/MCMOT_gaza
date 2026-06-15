// dashboard.jsx — right-side live analytics panel.

function fmtDwell(s) {
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + "s";
  return Math.floor(s / 60) + "m " + String(s % 60).padStart(2, "0") + "s";
}

function Sparkline({ data, color = "var(--pia-cyan)", h = 28 }) {
  if (!data || data.length < 2) return null;
  const w = 100;
  const max = Math.max(...data, 0.001);
  const min = Math.min(...data, 0);
  const rng = max - min || 1;
  const step = w / (data.length - 1);
  const pts = data.map((v, i) => `${(i * step).toFixed(1)},${(h - ((v - min) / rng) * (h - 4) - 2).toFixed(1)}`);
  const area = `0,${h} ${pts.join(" ")} ${w},${h}`;
  const id = "sg" + Math.round(min * 1000) + data.length;
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ height: h }}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={area} fill={`url(#${id})`} />
      <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth="1.6"
        vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function Metric({ icon, label, kr, value, unit, sub, accent, spark, sparkColor }) {
  return (
    <div className={"metric" + (accent ? " accent-" + accent : "")}>
      <div className="mh"><Icon name={icon} size={13} /> {label} <span className="kr">{kr}</span></div>
      <div className="val"><b>{value}</b>{unit && <span className="u">{unit}</span>}</div>
      {sub && <div className="sub">{sub}</div>}
      {spark && <Sparkline data={spark} color={sparkColor} />}
    </div>
  );
}

function BarChart({ data, color }) {
  const max = Math.max(...data, 1);
  const tail = data.slice(-26);
  return (
    <div className="gauge-row">
      {tail.map((v, i) => (
        <div key={i} className={"bar" + (i === tail.length - 1 ? " on" : "")}
          style={{ height: `${(v / max) * 100}%`,
            background: i === tail.length - 1 ? undefined : (color || "var(--bg-4)") }} />
      ))}
    </div>
  );
}

function Dashboard({ snap, vis, accent, pos }) {
  const m = snap.metrics;
  const cyan = accent === "coral" ? "var(--pia-coral)" : "var(--pia-cyan)";
  const sorted = snap.agents.slice().sort((a, b) => b.speed - a.speed);
  const levelClass = m.level === "High" ? "pill-high" : m.level === "Normal" ? "pill-normal" : "pill-low";

  return (
    <aside className={"dash" + (pos === "bottom" ? " bottom" : "")} data-screen-label="Analytics dashboard">
      <div className="dash-head">
        <div className="ttl">Analytics<span>실시간 영상 분석</span></div>
        <span className="live-mini"><span className="d" /> LIVE</span>
      </div>
      <div className="dash-body">

        <div className="dgroup">
        <div className="sectitle">Live metrics <span className="kr">실시간 지표</span></div>
        <div className="cardgrid">
          {vis.count && (
            <Metric icon="user" label="People now" kr="현재 인원" value={m.n}
              accent={accent} sub={<span>누적 <b className="t-num" style={{ color: "var(--text-2)" }}>{snap.cumulative.toLocaleString()}</b> today</span>} />
          )}
          {vis.density && (
            <Metric icon="layers" label="Density" kr="밀집도" value={m.density.toFixed(1)} unit="/100㎡"
              sub={<span className={"pill " + levelClass} style={{ padding: "1px 7px" }}>{m.level} · {m.levelKr}</span>} />
          )}
          {vis.speed && (
            <Metric icon="graph-growth" label="Avg speed" kr="평균 속도" value={m.avgSpeed.toFixed(2)} unit="m/s"
              sub={<span>peak <b className="t-num" style={{ color: "var(--text-2)" }}>{m.maxSpeed.toFixed(1)}</b> m/s</span>}
              spark={snap.speedHist} sparkColor={cyan} />
          )}
          {vis.accel && (
            <Metric icon="time" label="Acceleration" kr="가속도" value={m.avgAccel.toFixed(2)} unit="m/s²"
              sub={<span>mean magnitude</span>} />
          )}
          {vis.dwell && (
            <Metric icon="time" label="Avg dwell" kr="체류시간" value={fmtDwell(m.avgDwell)}
              sub={<span>max <b className="t-num" style={{ color: "var(--text-2)" }}>{fmtDwell(m.maxDwell)}</b></span>} />
          )}
          {vis.queue && (
            <Metric icon="roi-essential" label="In queue" kr="대기열" value={m.inQueue}
              sub={<span>queue zone</span>} />
          )}
        </div>
        </div>

        {vis.trend && (
          <div className="dgroup"><div className="bigcard">
            <div className="bh">
              <div className="t">Occupancy<span>인원 추이 · 30s</span></div>
              <div className={"pill " + levelClass}>{m.level} · {m.levelKr}</div>
            </div>
            <BarChart data={snap.history.length ? snap.history : [m.n]} />
          </div></div>
        )}

        {vis.list && (
          <div className="dgroup">
            <div className="sectitle">Tracked people <span className="kr">추적 객체 · {snap.agents.length}</span></div>
            <div className="ppl-list">
              {sorted.map((a) => {
                const zone = a.zone === "dwell" ? "Queue" : a.zone === "entry" ? "Entry" : "Floor";
                const dwell = (performance.now() - a.entry) / 1000;
                return (
                  <div key={a.id} className={"ppl" + (a.flag === "run" ? " hot" : "")}>
                    <span className="pid">{a.pid}</span>
                    <span className="zinfo">in <b>{zone}</b>{a.flag === "run" ? " · running" : ""}</span>
                    <span className="col"><span className="n t-num">{a.speed.toFixed(1)}</span><span className="k">m/s</span></span>
                    <span className="col"><span className="n t-num">{fmtDwell(dwell)}</span><span className="k">dwell</span></span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {vis.alerts && (
          <div className="dgroup">
            <div className="sectitle">Recent alerts <span className="kr">이벤트</span></div>
            <div className="alert-list">
              {snap.alerts.length === 0
                ? <div className="empty-note">No alerts · 이벤트 없음</div>
                : snap.alerts.map((al) => (
                  <div key={al.id} className="alertrow">
                    <div className="ai"><Icon name="warning-filled" size={16} /></div>
                    <div className="am">
                      <div className="l">{al.label}</div>
                      <div className="s">{al.pid} · conf {al.conf.toFixed(2)}</div>
                    </div>
                    <div className="at">{al.t}</div>
                  </div>
                ))}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

Object.assign(window, { Dashboard });
