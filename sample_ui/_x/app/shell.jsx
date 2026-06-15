// shell.jsx — Icon helper, left nav rail, top bar. Shares to window.
const { useState, useEffect, useRef, useMemo, useCallback } = React;

// Inline-SVG icon (renders in every browser / export). Recolor via `color`.
function Icon({ name, size = 20, style, className }) {
  const html = (window.PIA_ICONS && window.PIA_ICONS[name])
    ? window.PIAIcons.svg(name) : "";
  return (
    <span
      className={"ico-wrap" + (className ? " " + className : "")}
      style={{ display: "inline-flex", width: size, height: size, color: "inherit", ...style }}
      dangerouslySetInnerHTML={{ __html: html.replace('width:1em;height:1em', `width:${size}px;height:${size}px`) }}
    />
  );
}

function Logo({ size = 26 }) {
  return (
    <span className="piamark" style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
      <span style={{ font: `800 ${size}px/1 var(--font-sans)`, letterSpacing: "0.01em", color: "var(--text-1)" }}>PIA</span>
      <img src="assets/logo/pia-x-mark.svg" alt="" style={{ width: size * 0.58, height: size * 0.64, marginTop: -size * 0.06 }} />
    </span>
  );
}

const NAV = [
  { id: "live", ico: "video", label: "Live" },
  { id: "recordings", ico: "media-library", label: "Recordings" },
  { id: "detections", ico: "detect-log", label: "Detections" },
  { id: "analytics", ico: "graph-growth", label: "Analytics" },
  { id: "map", ico: "map", label: "Map" },
  { id: "cameras", ico: "cctv", label: "Cameras" },
];

function NavRail({ active, onNav, alarmCount }) {
  return (
    <nav className="rail" data-screen-label="Nav rail">
      <div className="brand"><img src="assets/logo/pia-x-mark.svg" alt="PIA" /></div>
      {NAV.map((n) => (
        <button key={n.id} className={"navbtn" + (active === n.id ? " on" : "")}
          onClick={() => onNav(n.id)} title={n.label} aria-label={n.label}>
          <Icon name={n.ico} size={22} />
          {n.id === "detections" && alarmCount > 0 && <span className="badge">{alarmCount}</span>}
        </button>
      ))}
      <div className="sp" />
      <button className="navbtn" title="Settings" aria-label="Settings"><Icon name="settings" size={22} /></button>
    </nav>
  );
}

function TopBar({ source, clock, onChangeSource, alarmCount, onAlarms }) {
  return (
    <header className="topbar" data-screen-label="Top bar">
      <div className="crumb">
        <b>Live analytics</b>
        <span>실시간 분석 · Single source</span>
      </div>
      {source && (
        <div className="srcchip">
          <span className={"sdot " + (source.kind === "rtsp" ? "cyan" : "blue")} />
          <Icon name={source.kind === "rtsp" ? "cctv" : "media-library"} size={15} />
          <span className="snm">{source.name}</span>
          <span className="ssub">{source.kind === "rtsp" ? "RTSP" : "FILE"}</span>
        </div>
      )}
      <div className="sp" />
      <div className="clock t-num">{clock}</div>
      <button className="icbtn" onClick={onAlarms} title="Alarms" aria-label="Alarms">
        <Icon name="notification" size={20} />
        {alarmCount > 0 && <span className="dot" />}
      </button>
      {source && (
        <button className="btn mono sm" onClick={onChangeSource}>
          <Icon name="refresh" size={15} /> Change source
        </button>
      )}
      <div className="acct">
        <div className="av t-num">OP</div>
        <div className="who"><b>Operator</b><span>Control room A</span></div>
      </div>
    </header>
  );
}

Object.assign(window, { Icon, Logo, NavRail, TopBar });
