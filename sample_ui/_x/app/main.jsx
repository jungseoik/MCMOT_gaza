// main.jsx — MACS 2.0 Live Analytics: state, sim loop, composition, tweaks.

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "density": "full",
  "accent": "cyan",
  "dashPos": "right",
  "m_count": true,
  "m_density": true,
  "m_speed": true,
  "m_accel": true,
  "m_dwell": true,
  "m_queue": false,
  "s_trend": true,
  "s_list": true,
  "s_alerts": true
}/*EDITMODE-END*/;

function fmtClock(totalSec) {
  const s = Math.floor(totalSec) % 60;
  const mn = Math.floor(totalSec / 60) % 60;
  const h = Math.floor(totalSec / 3600) % 24;
  const p = (n) => String(n).padStart(2, "0");
  return p(h) + ":" + p(mn) + ":" + p(s);
}

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [source, setSource] = useState(null);
  const [playing, setPlaying] = useState(true);
  const [snap, setSnap] = useState(null);
  const [clock, setClock] = useState(fmtClock(53528));
  const simRef = useRef(null);
  const clockBase = useRef(53528);
  const startRef = useRef(performance.now());

  const startSource = useCallback((s) => {
    simRef.current = new window.PeopleSim();
    setSnap(simRef.current.snapshot());
    startRef.current = performance.now();
    setPlaying(true);
    setSource(s);
  }, []);

  // sim + clock loop @ 10fps
  useEffect(() => {
    if (!source) return;
    let last = performance.now();
    const id = setInterval(() => {
      const now = performance.now();
      const dt = now - last; last = now;
      if (playing && simRef.current) simRef.current.step(Math.min(dt, 150));
      if (simRef.current) setSnap(simRef.current.snapshot());
      if (playing) setClock(fmtClock(clockBase.current + (now - startRef.current) / 1000));
    }, 100);
    return () => clearInterval(id);
  }, [source, playing]);

  const alarmCount = snap ? snap.alerts.length : 0;

  if (!source || !snap) {
    return (
      <div className="app">
        <NavRail active="live" onNav={() => {}} alarmCount={0} />
        <TopBar source={null} clock={clock} alarmCount={0} onAlarms={() => {}} />
        <div className="stage"><EmptyState onSource={startSource} /></div>
        <TweakUI t={t} setTweak={setTweak} />
      </div>
    );
  }

  const vis = {
    count: t.m_count, density: t.m_density, speed: t.m_speed, accel: t.m_accel,
    dwell: t.m_dwell, queue: t.m_queue, trend: t.s_trend, list: t.s_list, alerts: t.s_alerts,
  };
  const accent = t.accent === "none" ? null : t.accent;
  const bottom = t.dashPos === "bottom";

  return (
    <div className="app">
      <NavRail active="live" onNav={() => {}} alarmCount={alarmCount} />
      <TopBar source={source} clock={clock} alarmCount={alarmCount}
        onAlarms={() => {}} onChangeSource={() => { setSource(null); simRef.current = null; setSnap(null); }} />
      <div className="stage" style={bottom ? { flexDirection: "column" } : null}>
        <div className="workspace">
          <VideoStage source={source} snap={snap} density={t.density}
            playing={playing} onTogglePlay={() => setPlaying((p) => !p)} clock={clock} />
        </div>
        <Dashboard snap={snap} vis={vis} accent={accent} pos={t.dashPos} />
      </div>
      <TweakUI t={t} setTweak={setTweak} />
    </div>
  );
}

function TweakUI({ t, setTweak }) {
  return (
    <TweaksPanel>
      <TweakSection label="Detection overlay" />
      <TweakRadio label="Overlay density" value={t.density}
        options={["min", "box", "label", "full"]}
        onChange={(v) => setTweak("density", v)} />
      <TweakRadio label="Accent" value={t.accent}
        options={["cyan", "coral", "none"]}
        onChange={(v) => setTweak("accent", v)} />

      <TweakSection label="Layout" />
      <TweakRadio label="Dashboard" value={t.dashPos}
        options={["right", "bottom"]}
        onChange={(v) => setTweak("dashPos", v)} />

      <TweakSection label="Metric cards" />
      <TweakToggle label="People count" value={t.m_count} onChange={(v) => setTweak("m_count", v)} />
      <TweakToggle label="Density" value={t.m_density} onChange={(v) => setTweak("m_density", v)} />
      <TweakToggle label="Avg speed" value={t.m_speed} onChange={(v) => setTweak("m_speed", v)} />
      <TweakToggle label="Acceleration" value={t.m_accel} onChange={(v) => setTweak("m_accel", v)} />
      <TweakToggle label="Dwell time" value={t.m_dwell} onChange={(v) => setTweak("m_dwell", v)} />
      <TweakToggle label="In queue" value={t.m_queue} onChange={(v) => setTweak("m_queue", v)} />

      <TweakSection label="Sections" />
      <TweakToggle label="Occupancy trend" value={t.s_trend} onChange={(v) => setTweak("s_trend", v)} />
      <TweakToggle label="Tracked people" value={t.s_list} onChange={(v) => setTweak("s_list", v)} />
      <TweakToggle label="Recent alerts" value={t.s_alerts} onChange={(v) => setTweak("s_alerts", v)} />
    </TweaksPanel>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
