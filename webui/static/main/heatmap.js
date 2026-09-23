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
 * 성능 — 재생 중 프레임률로 잰 실측(headless Chromium = 소프트웨어 래스터, 최악 조건.
 * 실기기 GPU 에서는 더 싸다). 1098x922 논리 캔버스 · dpr2 · 13명:
 *     히트맵 OFF                     60.0 fps
 *     초판(보간 확대)                 37.1 fps   ← 여기가 문제였다
 *     보간 끔                        49.4 fps
 *     보간 끔 + 배율 0.6              아래 커밋 메시지 참고
 * 주의 — 단계를 따로 떼어 재면 전부 0ms 로 나온다. 브라우저가 소비되지 않는 그리기를
 * 생략하기 때문이다. 반드시 **프레임률**로 재야 실제 비용이 드러난다.
 * 비용의 정체는 색칠(픽셀 루프)도, 블롭 누적도 아니고 **확대 blit 의 보간**이었다.
 */
(function () {
  "use strict";

  const DEFAULT_R_M = 2.5;          // 한 사람이 퍼뜨리는 반경 (실거리 m)
  // 알파 누적 해상도 배율. 낮추면 만드는 비용이 줄지만 **올릴 때 확대 배율이 커진다**.
  // 확대가 실제 비용의 대부분이라(아래 '성능') 너무 낮추면 오히려 손해다.
  const SCALE = 0.6;
  const MIN_PX = 14, MAX_PX = 260;  // 화면 반경 하한·상한 (줌 극단 방어)
  // 재생성 최소 간격(ms). 렌더 루프는 20~60fps 로 도는데 밀도장이 그 속도로 바뀔
  // 이유가 없다 — 사람이 0.1초에 갈 수 있는 거리는 반경 2.5m 블롭 안에서 티도 안 난다.
  // 그 사이 프레임은 직전 버퍼를 그대로 올린다(blit 은 사실상 공짜).
  // 실측(headless, 소프트웨어 래스터): 매 프레임 재생성이면 60fps → 36fps 로 떨어졌다.
  const MIN_REBUILD_MS = 120;

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

  let _buf = null, _key = null, _at = 0, _rect = null;  // 오프스크린·입력키·재생성시각·화면사각형
                                    // (프레임마다 새로 만들면 GC 폭탄 + 왕복 동기화)
  function buf(w, h) {
    if (!_buf) _buf = document.createElement("canvas");
    if (_buf.width !== w || _buf.height !== h) { _buf.width = w; _buf.height = h; }
    return _buf;
  }

  // 블롭 스프라이트 — 반경마다 **한 번만** 그려 두고 점마다 복사해 쓴다.
  // createRadialGradient 를 점×프레임마다 만들면(사람 13명·20fps = 초당 260개)
  // 그래디언트 객체 생성·래스터화가 누적돼 실측 비용의 대부분을 차지했다.
  let _sprite = null, _spriteR = -1;
  function sprite(r) {
    const d = Math.max(2, Math.ceil(r * 2));
    if (_sprite && _spriteR === d) return _sprite;
    const c = document.createElement("canvas");
    c.width = c.height = d;
    const x = c.getContext("2d");
    const g = x.createRadialGradient(d / 2, d / 2, 0, d / 2, d / 2, d / 2);
    g.addColorStop(0, "rgba(255,255,255,0.55)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    x.fillStyle = g;
    x.fillRect(0, 0, d, d);
    _sprite = c; _spriteR = d;
    return c;
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

    // 화면 좌표로 옮기고, **사람이 있는 사각형**만 처리 대상으로 잡는다.
    // 도면은 넓은데 사람은 한쪽에 몰려 있는 게 보통이라, 캔버스 전체를 만들고
    // 올리면 빈 바닥을 계속 확대·합성하게 된다(실측 비용의 대부분이 이 확대였다).
    const sx = [], sy = [];
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of pts) {
      const q = g.P ? g.P(p.x, p.y) : [g.TX(p.x), g.TY(p.y)];
      if (q[0] < -R || q[1] < -R || q[0] > cw + R || q[1] > ch + R) continue;
      sx.push(q[0]); sy.push(q[1]);
      if (q[0] < x0) x0 = q[0];
      if (q[1] < y0) y0 = q[1];
      if (q[0] > x1) x1 = q[0];
      if (q[1] > y1) y1 = q[1];
    }
    if (!sx.length) return;
    x0 = Math.max(0, Math.floor(x0 - R)); y0 = Math.max(0, Math.floor(y0 - R));
    x1 = Math.min(cw, Math.ceil(x1 + R)); y1 = Math.min(ch, Math.ceil(y1 + R));
    const rw = Math.max(1, x1 - x0), rh = Math.max(1, y1 - y0);

    const w = Math.max(1, Math.round(rw * SCALE));
    const h = Math.max(1, Math.round(rh * SCALE));

    const now = (typeof performance !== "undefined" ? performance.now() : Date.now());
    // 입력이 같으면 물론이고, 너무 자주 바뀌어도 직전 버퍼를 그대로 올린다.
    const key = w + "|" + h + "|" + Math.round(R) + "|" + x0 + "," + y0 + "|"
      + sx.length + "|" + sx.map((v, i) => (v | 0) + "," + (sy[i] | 0)).join(";");
    if (_buf && key === _key) { blit(ctx, _buf, _rect, o.alpha); return; }
    if (_buf && _buf.width === w && _buf.height === h && now - _at < MIN_REBUILD_MS) {
      blit(ctx, _buf, _rect, o.alpha); return;
    }
    _key = key; _at = now; _rect = [x0, y0, rw, rh];

    const bc = buf(w, h);
    const b = bc.getContext("2d", { willReadFrequently: true });
    b.clearRect(0, 0, w, h);

    // 1) 알파 누적 — 한 사람 = 중심 진하고 가장자리 0 인 원(스프라이트 복사)
    const r = R * SCALE;
    const sp0 = sprite(r), sd = sp0.width;
    b.globalCompositeOperation = "lighter";
    for (let i = 0; i < sx.length; i++) {
      const cx = (sx[i] - x0) * SCALE, cy = (sy[i] - y0) * SCALE;
      b.drawImage(sp0, cx - sd / 2, cy - sd / 2);
    }
    b.globalCompositeOperation = "source-over";

    // 2) 누적 알파 → 색 (버퍼가 곧 군집 영역이라 전체를 훑어도 작다)
    const img = b.getImageData(0, 0, w, h);
    const d = img.data, L = lut();
    for (let i = 0; i < d.length; i += 4) {
      const a = d[i + 3];
      if (!a) continue;
      const k = a * 4;
      d[i] = L[k]; d[i + 1] = L[k + 1]; d[i + 2] = L[k + 2]; d[i + 3] = L[k + 3];
    }
    b.putImageData(img, 0, 0);

    // 3) 본 캔버스의 그 자리에만 올린다
    blit(ctx, bc, _rect, o.alpha);
  }

  function blit(ctx, bc, rect, alpha) {
    if (!rect) return;
    ctx.save();
    ctx.globalAlpha = alpha == null ? 0.72 : alpha;
    // 보간을 끈다 — 확대 blit 의 쌍선형 보간이 비용의 대부분이었다(실측 37→49fps).
    // 원본이 이미 방사 그라디언트라 값이 매끄럽고, 확대 배율도 1.7배뿐이라
    // 최근접 확대로도 계단이 눈에 띄지 않는다. 히트맵은 원래 뭉개는 표현이다.
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(bc, rect[0], rect[1], rect[2], rect[3]);
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
