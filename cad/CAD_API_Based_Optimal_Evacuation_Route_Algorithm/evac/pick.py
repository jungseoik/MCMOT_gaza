"""
evac.pick — Exit/출발점 대화식 지정기(GUI). scale 동일 맵을 띄워 N개 클릭 → 미터좌표.

디스플레이(GUI 백엔드)가 있는 환경에서 동작한다(로컬 데스크톱/X11/노트북).
헤드리스 서버에선 못 쓰므로, 그 경우 cli의 --exits/--exits-json 를 사용한다.
"""
import numpy as np


def pick_points(dxf, n=None, kind="Exit"):
    """평면도를 띄워 사용자가 점 N개를 클릭 → [(xm,ym)] (SW코너=0 미터).
    n=None 이면 Enter(또는 우클릭)로 종료할 때까지 무제한.
    """
    import matplotlib.pyplot as plt   # 기본(대화식) 백엔드 사용
    from matplotlib.collections import LineCollection
    from .render import _setup_font, _scale_axes
    if plt.get_backend().lower() == "agg":
        raise RuntimeError(
            "대화식 백엔드가 없다(Agg). 디스플레이 있는 환경에서 실행하거나, "
            "CLI의 --exits 'xm,ym;...' / --exits-json 를 사용하라.")
    _setup_font(plt)
    minx, miny, maxx, maxy = dxf.bounds
    Wm, Hm = (maxx - minx) / 1000, (maxy - miny) / 1000
    fig = plt.figure(figsize=(14, 14 * Hm / Wm)); ax = fig.add_axes([0.06, 0.06, 0.92, 0.88])
    ax.add_collection(LineCollection(
        [[(s[0], s[1]), (s[2], s[3])] for s in dxf.obstacles], colors="#888", linewidths=0.3))
    if dxf.exits:
        ax.scatter([e[0] for e in dxf.exits], [e[1] for e in dxf.exits],
                   s=90, marker="s", c="#0033aa", label="기존 Exit")
    _scale_axes(ax, np, dxf.bounds, grid_m=5.0)
    ax.set_title(f"{kind} 위치를 클릭하세요"
                 + (f" (정확히 {n}개)" if n else " (끝나면 Enter)")
                 + "  ·  좌표는 SW코너=0 미터")
    plt.tight_layout()

    pts = plt.ginput(n=(n if n else -1), timeout=0)
    plt.close(fig)
    # 도면좌표(mm) → 미터(SW=0)
    return [dxf.world_to_m(x, y) for (x, y) in pts]


def make_html(dxf, out_html, grid_m=5.0, dpi=140):
    """디스플레이 없이도 되는 **브라우저 클릭 피커** HTML 생성(자기완결 1파일).
    도면을 full-bleed PNG로 렌더해 base64로 박고, 클릭→미터좌표 선형매핑하는 JS를 붙인다.
    사용자는 파일을 브라우저로 열어 Exit를 클릭 → --exits 명령문자열을 복사한다.
    """
    import base64
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    import numpy as np

    minx, miny, maxx, maxy = dxf.bounds
    Wm, Hm = (maxx - minx) / 1000.0, (maxy - miny) / 1000.0
    base = 14.0
    figw, figh = base, base * Hm / Wm
    fig = plt.figure(figsize=(figw, figh))
    ax = fig.add_axes([0, 0, 1, 1])           # full-bleed → 픽셀↔데이터 선형
    ax.add_collection(LineCollection(
        [[(s[0], s[1]), (s[2], s[3])] for s in dxf.obstacles], colors="#888", linewidths=0.3))
    G = grid_m * 1000.0
    for gx in np.arange(0, (maxx - minx) + 1, G):
        ax.axvline(minx + gx, color="#1f77ff", lw=0.4, alpha=0.18)
    for gy in np.arange(0, (maxy - miny) + 1, G):
        ax.axhline(miny + gy, color="#1f77ff", lw=0.4, alpha=0.18)
    if dxf.exits:
        ax.scatter([e[0] for e in dxf.exits], [e[1] for e in dxf.exits],
                   s=80, marker="s", c="#0033aa", zorder=5)
    ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy); ax.set_aspect("equal"); ax.axis("off")
    buf = io.BytesIO()
    fig.savefig(buf, dpi=dpi, facecolor="white"); plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode()
    pxw, pxh = int(figw * dpi), int(figh * dpi)

    html = _HTML.replace("__IMG__", b64).replace("__WM__", f"{Wm:.3f}") \
        .replace("__HM__", f"{Hm:.3f}").replace("__PXW__", str(pxw)) \
        .replace("__PXH__", str(pxh)).replace("__GRID__", f"{grid_m:.0f}") \
        .replace("__SRC__", dxf.path)
    with open(out_html, "w") as f:
        f.write(html)
    return out_html


_HTML = """<!doctype html><html lang=ko><meta charset=utf-8>
<title>Exit 피커 — __SRC__</title>
<style>
 body{font-family:system-ui,AppleGothic,sans-serif;margin:0;background:#222;color:#eee}
 #bar{padding:10px 14px;background:#111;position:sticky;top:0;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
 #wrap{position:relative;display:inline-block;margin:12px}
 #plan{display:block;max-width:98vw;height:auto;cursor:crosshair;background:#fff}
 svg{position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none}
 button{padding:6px 12px;border:0;border-radius:6px;background:#0a7;color:#fff;font-size:14px;cursor:pointer}
 button.g{background:#555}
 code{background:#000;padding:6px 10px;border-radius:6px;color:#6f6;user-select:all}
 #hint{color:#9cf}
</style>
<div id=bar>
 <b>Exit 클릭</b> <span id=hint>도면 위를 클릭해 Exit를 찍으세요 (격자 __GRID__ m)</span>
 <button class=g onclick=undo()>실행취소</button>
 <button class=g onclick=clearAll()>전체삭제</button>
 <button onclick=copyCmd()>명령어 복사</button>
 <span id=xy style=color:#fc9></span>
</div>
<div style=padding:10px>
 <div>Exit 목록(SW코너=0, m): <span id=list>-</span></div>
 <div style=margin-top:6px>명령: <code id=cmd>--exits ""</code></div>
</div>
<div id=wrap>
 <img id=plan src="data:image/png;base64,__IMG__" width=__PXW__ height=__PXH__>
 <svg id=ov viewBox="0 0 __PXW__ __PXH__"></svg>
</div>
<script>
const WM=__WM__, HM=__HM__, PXW=__PXW__, PXH=__PXH__;
let pts=[];
const img=document.getElementById('plan'), ov=document.getElementById('ov');
function toM(ev){const r=img.getBoundingClientRect();
 const px=(ev.clientX-r.left)/r.width, py=(ev.clientY-r.top)/r.height;
 return [px*WM, (1-py)*HM];}       // y축 반전(이미지 위=큰 Y)
img.onmousemove=e=>{const [x,y]=toM(e);document.getElementById('xy').textContent=`(${x.toFixed(1)}, ${y.toFixed(1)}) m`};
img.onclick=e=>{pts.push(toM(e));draw()};
function draw(){
 let s='';pts.forEach(([x,y],i)=>{const px=x/WM*PXW, py=(1-y/HM)*PXH;
  s+=`<circle cx=${px} cy=${py} r=8 fill=none stroke=red stroke-width=3/>`+
     `<text x=${px+11} y=${py+4} fill=red font-size=20 font-weight=bold>E${i+1}</text>`});
 ov.innerHTML=s;
 document.getElementById('list').textContent=pts.length?pts.map(([x,y])=>`(${x.toFixed(1)},${y.toFixed(1)})`).join('  '):'-';
 const str=pts.map(([x,y])=>`${x.toFixed(1)},${y.toFixed(1)}`).join(';');
 document.getElementById('cmd').textContent=`--exits "${str}"`;
}
function undo(){pts.pop();draw()}
function clearAll(){pts=[];draw()}
function copyCmd(){navigator.clipboard.writeText(document.getElementById('cmd').textContent);
 document.getElementById('hint').textContent='복사됨! 터미널 evac_route.py route 뒤에 붙여넣기';}
draw();
</script></html>"""
