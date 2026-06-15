// stage.jsx — empty-state source picker + live video stage with detection overlays.

function useMeasure() {
  const ref = useRef(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    if (!ref.current) return;
    const measure = () => {
      const r = ref.current.getBoundingClientRect();
      setSize({ w: r.width, h: r.height });
    };
    measure();
    const raf = requestAnimationFrame(measure);
    const ro = new ResizeObserver(measure);
    ro.observe(ref.current);
    window.addEventListener("resize", measure);
    return () => { ro.disconnect(); window.removeEventListener("resize", measure); cancelAnimationFrame(raf); };
  }, []);
  return [ref, size];
}

/* ---------------- Empty state ---------------- */
function EmptyState({ onSource }) {
  const [over, setOver] = useState(false);
  const [rtsp, setRtsp] = useState("");
  const fileRef = useRef(null);

  const pickFile = (file) => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    onSource({ kind: "file", name: file.name, url, demo: false });
  };
  const connect = () => {
    const v = rtsp.trim();
    if (!v) return;
    onSource({ kind: "rtsp", name: v, url: null, demo: true });
  };

  return (
    <div className="empty" data-screen-label="Source picker">
      <div className="srcpanel">
        <div className="eyebrow"><span className="ld" /> MACS 2.0 · 실시간 분석</div>
        <h1>Add a source</h1>
        <p className="lede">Upload a video file or connect an RTSP stream. MACS begins tracking
          people and reporting motion analytics in real time.</p>

        <div
          className={"drop" + (over ? " over" : "")}
          onClick={() => fileRef.current && fileRef.current.click()}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); pickFile(e.dataTransfer.files[0]); }}
        >
          <div className="ic"><Icon name="upload" size={24} /></div>
          <div className="big">Drop a video file here, or <span className="browse">browse</span></div>
          <div className="small">MP4 · MOV · MKV · up to 4K — 비디오 파일 업로드</div>
          <input ref={fileRef} type="file" accept="video/*" hidden
            onChange={(e) => pickFile(e.target.files[0])} />
        </div>

        <div className="orline">OR</div>

        <div className="rtsp">
          <div className="fld">
            <Icon name="cctv" size={18} />
            <input value={rtsp} onChange={(e) => setRtsp(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && connect()}
              placeholder="rtsp://192.168.0.21:554/stream1" />
          </div>
          <button className="btn primary" onClick={connect} disabled={!rtsp.trim()}>
            <Icon name="arrow-right" size={16} /> Connect
          </button>
        </div>

        <div className="samples">
          <div className="lbl">Or try a sample feed</div>
          <div className="samplerow">
            <button className="samplecard" onClick={() => onSource({ kind: "rtsp", name: "Concourse — West gate", url: null, demo: true })}>
              <div className="th"><span className="b" style={{ left: 8, top: 6, width: 12, height: 18 }} /><span className="b" style={{ left: 26, top: 10, width: 11, height: 16 }} /></div>
              <div><div className="nm">Concourse</div><div className="mt">RTSP · 25 fps</div></div>
            </button>
            <button className="samplecard" onClick={() => onSource({ kind: "rtsp", name: "Lobby — North 01", url: null, demo: true })}>
              <div className="th"><span className="b" style={{ left: 14, top: 8, width: 12, height: 17 }} /></div>
              <div><div className="nm">Lobby cam</div><div className="mt">RTSP · 30 fps</div></div>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---------------- Detection overlay geometry ---------------- */
function figGeom(a, W, H) {
  const px = (a.x / 100) * W;
  const py = (a.y / 100) * H;
  const t = (Math.max(24, Math.min(88, a.y)) - 24) / 64; // 0..1 depth
  const scale = 0.55 + t * 0.85;
  const figH = scale * H * 0.16;
  const figW = figH * 0.4;
  return { px, py, figH, figW, scale };
}

function Figure({ a, W, H }) {
  const { px, py, figH, figW } = figGeom(a, W, H);
  const headD = figW * 0.6;
  const bodyH = figH - headD * 0.65;
  return (
    <div className="person" style={{ left: px, top: py, width: figW, height: figH }}>
      <div className="shadow" style={{ width: figW * 1.5, height: figW * 0.5 }} />
      <div className="head" style={{ width: headD, height: headD, top: 0 }} />
      <div className="body" style={{ width: figW * 0.92, height: bodyH,
        borderRadius: `${figW * 0.46}px ${figW * 0.46}px ${figW * 0.2}px ${figW * 0.2}px` }} />
    </div>
  );
}

function DetectSVG({ agents, zones, W, H, density }) {
  if (!W || !H) return null;
  const showZones = density === "full";
  const showBox = density !== "min";
  const showVec = density === "full";
  const showTrail = density === "full";
  return (
    <svg className="detect-overlay" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      {showZones && zones.map((z) => {
        const pts = z.poly.map(([x, y]) => `${(x / 100) * W},${(y / 100) * H}`).join(" ");
        const hot = z.kind === "dwell";
        const col = hot ? "rgba(48,220,251,0.9)" : "rgba(112,112,243,0.9)";
        const fill = hot ? "rgba(48,220,251,0.06)" : "rgba(42,41,237,0.06)";
        const cx = (z.poly.reduce((s, p) => s + p[0], 0) / z.poly.length / 100) * W;
        const cy = (z.poly[0][1] / 100) * H;
        return (
          <g key={z.id}>
            <polygon points={pts} fill={fill} stroke={col} strokeWidth="1.5" strokeDasharray="6 5" />
            <text x={cx} y={cy + 16} fill={col} fontSize="11" fontFamily="Inter, sans-serif"
              fontWeight="700" textAnchor="middle" letterSpacing="0.06em">{z.label.toUpperCase()}</text>
          </g>
        );
      })}
      {agents.map((a) => {
        const { px, py, figH, figW } = figGeom(a, W, H);
        const pad = figW * 0.22;
        const bx = px - figW / 2 - pad, by = py - figH - pad;
        const bw = figW + pad * 2, bh = figH + pad * 2;
        const hot = a.flag === "run";
        const col = hot ? "#FF4A44" : "#30DCFB";
        const ck = Math.min(10, bw * 0.28); // corner bracket length
        return (
          <g key={a.id}>
            {showTrail && a.trail.length > 1 && (
              <polyline
                points={a.trail.map((p) => `${(p.x / 100) * W},${(p.y / 100) * H}`).join(" ")}
                fill="none" stroke={col} strokeWidth="1.5" strokeOpacity="0.5"
                strokeLinejoin="round" strokeDasharray="1 4" strokeLinecap="round" />
            )}
            {showBox && (
              <g>
                <rect x={bx} y={by} width={bw} height={bh} fill="none"
                  stroke={col} strokeOpacity={hot ? 0.95 : 0.85} strokeWidth="1.5" rx="2" />
                {/* corner brackets */}
                {[[bx, by, 1, 1], [bx + bw, by, -1, 1], [bx, by + bh, 1, -1], [bx + bw, by + bh, -1, -1]].map((c, i) => (
                  <path key={i} d={`M ${c[0]} ${c[1] + c[3] * ck} L ${c[0]} ${c[1]} L ${c[0] + c[2] * ck} ${c[1]}`}
                    fill="none" stroke={col} strokeWidth="2.5" />
                ))}
              </g>
            )}
            {showVec && a.speed > 0.25 && (() => {
              const cx = px, cy = py - figH * 0.5;
              const L = Math.min(60, a.speed * 20);
              const ex = cx + a.dir.x * L, ey = cy + a.dir.y * L;
              const ang = Math.atan2(ey - cy, ex - cx);
              const ah = 5;
              return (
                <g>
                  <line x1={cx} y1={cy} x2={ex} y2={ey} stroke={col} strokeWidth="2" strokeLinecap="round" />
                  <path d={`M ${ex} ${ey} L ${ex - ah * Math.cos(ang - 0.5)} ${ey - ah * Math.sin(ang - 0.5)} L ${ex - ah * Math.cos(ang + 0.5)} ${ey - ah * Math.sin(ang + 0.5)} Z`} fill={col} />
                </g>
              );
            })()}
          </g>
        );
      })}
    </svg>
  );
}

function Tags({ agents, W, H, density }) {
  if (density !== "label" && density !== "full") return null;
  return agents.map((a) => {
    const { px, py, figH, figW } = figGeom(a, W, H);
    const pad = figW * 0.22;
    const left = px - figW / 2 - pad;
    const top = py - figH - pad - 17;
    const hot = a.flag === "run";
    return (
      <div key={a.id} className={"tag" + (hot ? " hot" : "")} style={{ left, top, transform: "none" }}>
        {a.pid}<span className="sp">{a.speed.toFixed(1)} m/s</span>
      </div>
    );
  });
}

/* ---------------- Video stage ---------------- */
function VideoStage({ source, snap, density, playing, onTogglePlay, clock }) {
  const [boxRef, size] = useMeasure();
  const anyRun = snap.agents.some((a) => a.flag === "run");
  const m = snap.metrics;
  return (
    <div className="videowrap" data-screen-label="Live video">
      <div ref={boxRef} className={"videobox" + (anyRun ? " alarm" : "")}>
        {source.kind === "file" && source.url
          ? <video className="feed-real" src={source.url} autoPlay loop muted playsInline />
          : (
            <div className="scene">
              <div className="floor" />
              <div className="backwall" />
              <div className="scan" />
              {playing && <div className="scanmove" />}
              <div className="grain" />
              <div className="vign" />
            </div>
          )}

        <DetectSVG agents={snap.agents} zones={snap.zones} W={size.w} H={size.h} density={density} />
        {(source.kind !== "file" || !source.url) &&
          <div className="detect-overlay" style={{ zIndex: 3 }}>
            {snap.agents.map((a) => <Figure key={a.id} a={a} W={size.w} H={size.h} />)}
          </div>}
        <Tags agents={snap.agents} W={size.w} H={size.h} density={density} />

        {/* HUD */}
        <div className="hud">
          <div className="hud-top">
            <span className="live-token"><span className="d" />{source.kind === "rtsp" ? "LIVE" : "PLAY"}</span>
            <span className="src-name">{source.name}</span>
            <div className="meta-r">
              <span className="chip-h"><Icon name="user" size={13} /> {m.n}</span>
              <span>1920×1080</span>
              <span>25 fps</span>
            </div>
          </div>
        </div>

        <div className="hud-bot">
          <div className="transport">
            <button className="tbtn" title="Rewind"><Icon name="rewind5" size={16} /></button>
            <button className="tbtn play" onClick={onTogglePlay} title={playing ? "Pause" : "Play"}>
              <Icon name={playing ? "pause" : "play-filled"} size={18} />
            </button>
            <button className="tbtn" title="Skip"><Icon name="skip-front" size={16} /></button>
          </div>
          <span className="tcbig">{clock}</span>
          <div className="btrack">
            <div className="bf" style={{ width: "68%" }} />
            <div className="bev" style={{ left: "44%" }} />
            <div className="bev" style={{ left: "61%" }} />
          </div>
          <button className="tbtn" title="Fit"><Icon name="fit-to-screen" size={16} /></button>
          <button className="tbtn" title="Snapshot"><Icon name="download" size={16} /></button>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { EmptyState, VideoStage });
