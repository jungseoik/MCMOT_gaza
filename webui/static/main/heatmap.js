/* 밀도 히트맵 — 도면 위에 "사람이 어디 몰렸나"를 색으로 깐다.
 * ③ 운영 뷰와 ④ 리플레이가 같은 코드를 쓴다(보이는 그림이 달라지면 안 된다).
 *
 * 만드는 방식(고전적 KDE 렌더):
 *   1) 오프스크린에 사람마다 **반경 R 의 방사 그라디언트**를 알파로 누적한다
 *      → 겹칠수록 알파가 커진다 = 밀집도
 *   2) 누적 알파를 팔레트(파랑→청록→초록→노랑→빨강)로 색칠한다
 *   3) 본 캔버스에 반투명으로 올린다
 *
 * 반경은 **실거리 기준**(기본 2.5 m)이다 — 화면 px 로 고정하면 줌·도면 축척에 따라
 * 같은 밀집이 다른 그림으로 보인다. m_per_px 가 없으면 화면 px 폴백.
 *
 * 성능: 알파 누적은 절반 해상도로 그리고 올릴 때 확대한다. 흐릿한 게 정상이고
 * (히트맵은 원래 뭉개는 표현) 픽셀 수가 1/4 이라 5Hz 갱신에도 부담이 없다.
 */
(function () {
  "use strict";

  const DEFAULT_R_M = 2.5;          // 한 사람이 퍼뜨리는 반경 (실거리 m)
  const SCALE = 0.5;                // 알파 누적 해상도 배율
  const MIN_PX = 14, MAX_PX = 260;  // 화면 반경 하한·상한 (줌 극단 방어)

  // 팔레트 — 0(없음) → 1(최대). 알파가 낮은 쪽은 투명하게 빼서 빈 바닥이 안 물든다.
  const STOPS = [
    [0.00, [0, 0, 0], 0],
    [0.12, [40, 110, 220], 90],
    [0.32, [40, 200, 200], 150],
    [0.55, [70, 210, 90], 185],
    [0.78, [245, 200, 40], 210],
    [1.00, [240, 60, 50], 230],
  ];

  let _lut = null;
  function lut() {                  // 256단계 조견표 — 픽셀마다 보간하지 않게
    if (_lut) return _lut;
    _lut = new Uint8ClampedArray(256 * 4);
    for (let i = 0; i < 256; i++) {
      const t = i / 255;
      let a = STOPS[0], b = STOPS[STOPS.length - 1];
      for (let k = 0; k < STOPS.length - 1; k++) {
        if (t >= STOPS[k][0] && t <= STOPS[k + 1][0]) { a = STOPS[k]; b = STOPS[k + 1]; break; }
      }
      const span = (b[0] - a[0]) || 1;
      const f = (t - a[0]) / span;
      _lut[i * 4 + 0] = a[1][0] + (b[1][0] - a[1][0]) * f;
      _lut[i * 4 + 1] = a[1][1] + (b[1][1] - a[1][1]) * f;
      _lut[i * 4 + 2] = a[1][2] + (b[1][2] - a[1][2]) * f;
      _lut[i * 4 + 3] = a[2] + (b[2] - a[2]) * f;
    }
    return _lut;
  }

  let _buf = null;                  // 오프스크린 재사용 (프레임마다 새로 만들면 GC 폭탄)
  function buf(w, h) {
    if (!_buf) _buf = document.createElement("canvas");
    if (_buf.width !== w || _buf.height !== h) { _buf.width = w; _buf.height = h; }
    return _buf;
  }

  /** 히트맵을 그린다.
   *  g       mapcanvas 오버레이 번들 {ctx, P, s, mc}
   *  pts     [{x,y}] — **맵 원본 px** 좌표
   *  opts    {mPerPx, radiusM, alpha}
   */
  function draw(g, pts, opts) {
    if (!g || !pts || !pts.length) return;
    const o = opts || {};
    const ctx = g.ctx;
    const cw = g.mc ? g.mc.cw : ctx.canvas.width;
    const ch = g.mc ? g.mc.ch : ctx.canvas.height;
    if (!cw || !ch) return;

    // 실거리 반경 → 화면 px. m_per_px 가 없으면 맵 px 를 그대로 쓴다.
    const rm = o.radiusM || DEFAULT_R_M;
    const rMapPx = o.mPerPx ? (rm / o.mPerPx) : 60;
    const R = Math.max(MIN_PX, Math.min(MAX_PX, rMapPx * (g.s || 1)));

    const w = Math.max(1, Math.round(cw * SCALE));
    const h = Math.max(1, Math.round(ch * SCALE));
    const bc = buf(w, h);
    const b = bc.getContext("2d", { willReadFrequently: true });
    b.clearRect(0, 0, w, h);

    // 1) 알파 누적 — 한 사람 = 중심 진하고 가장자리 0 인 원
    const r = R * SCALE;
    b.globalCompositeOperation = "lighter";
    for (const p of pts) {
      const sp = g.P ? g.P(p.x, p.y) : [g.TX(p.x), g.TY(p.y)];
      const cx = sp[0] * SCALE, cy = sp[1] * SCALE;
      if (cx < -r || cy < -r || cx > w + r || cy > h + r) continue;   // 화면 밖 건너뜀
      const grd = b.createRadialGradient(cx, cy, 0, cx, cy, r);
      grd.addColorStop(0, "rgba(255,255,255,0.55)");
      grd.addColorStop(1, "rgba(255,255,255,0)");
      b.fillStyle = grd;
      b.beginPath(); b.arc(cx, cy, r, 0, 7); b.fill();
    }
    b.globalCompositeOperation = "source-over";

    // 2) 누적 알파 → 색
    const img = b.getImageData(0, 0, w, h);
    const d = img.data, L = lut();
    for (let i = 0; i < d.length; i += 4) {
      const a = d[i + 3];
      if (!a) continue;
      const k = a * 4;
      d[i] = L[k]; d[i + 1] = L[k + 1]; d[i + 2] = L[k + 2]; d[i + 3] = L[k + 3];
    }
    b.putImageData(img, 0, 0);

    // 3) 본 캔버스에 올린다 (도면이 비쳐야 어디인지 알 수 있다)
    ctx.save();
    ctx.globalAlpha = o.alpha == null ? 0.72 : o.alpha;
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(bc, 0, 0, cw, ch);
    ctx.restore();
  }

  /** 도면 축척 (m/px). site.map 에서 꺼낸다 — 없으면 null. */
  function mPerPxOf(site) {
    const mp = (site && site.map) || {};
    if (mp.m_per_px) return mp.m_per_px;
    if (mp.scale_m && mp.scale_px) return mp.scale_m / mp.scale_px;
    return null;
  }

  /** 범례 HTML — 버튼 옆에 색 띠를 깔아 "빨강이 몰림"을 알려준다. */
  function legendHTML() {
    return `<span class="heatleg" title="사람이 몰릴수록 붉어집니다 (반경 ${DEFAULT_R_M}m 기준)">`
      + `<i></i><span>적음</span><b></b><span>몰림</span></span>`;
  }

  window.Heatmap = { draw, mPerPxOf, legendHTML, DEFAULT_R_M };
})();
