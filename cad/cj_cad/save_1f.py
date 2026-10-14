#!/usr/bin/env python
"""A-101 지상1층 band → 구조 레이어만(벽·문·창·기둥·계단) 추출.
--save 없으면 KEEP-only 미리보기 PNG만. --save 면 정리 DXF + PNG + 척도PNG 저장.
좌표: 남서코너=원점(0,0), mm, $INSUNITS=4. 글자/치수 없음(편집기용 순수벡터).
"""
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, numpy as np, math, sys
from matplotlib.collections import LineCollection
from matplotlib import font_manager as fm
import ezdxf
from collections import Counter

for fp in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",):
    try: fm.fontManager.addfont(fp); plt.rcParams["font.family"]=fm.FontProperties(fname=fp).get_name()
    except Exception: pass

DXF = "cad/cj_cad/_converted/A-101_128_각 층 평면도_최종_수정.dxf"
SAVE = "--save" in sys.argv
Y0, Y1 = 2_290_000, 2_418_000
CLAMP = 50_000_000

# 구조 레이어 화이트리스트 (band 레이어 실측 기반). 용도별 그룹.
WALL   = {"AA-CWALL","AA-DWALL","AA-BRCK","AA-STON","AA-석재","A-WA3","실구획선","AA-FIN8"}
DOOR   = {"AA-DOOR","A-DOOR","방화도어","RXDOOR","DOR","A-SHU","AA-SHU"}
GLASS  = {"AA-WINDOW","AA-WIN4","A-WIN","WIN","유리","AA-GLASS"}
COLUMN = {"AA-COL","STEEL COLUMN"}
STAIR  = {"STAIR","VOID","080808 조경base$0$STAIR"}
def group(L):
    if L in WALL: return "WALL"
    if L in DOOR: return "DOOR"
    if L in GLASS: return "GLASS"
    if L in COLUMN: return "COLUMN"
    if L in STAIR: return "STAIR"
    return None

doc = ezdxf.readfile(DXF); msp = doc.modelspace()
def sane(x,y): return abs(x)<CLAMP and abs(y)<CLAMP
def inband(x,y): return Y0<=y<=Y1 and sane(x,y)

SEG=[]; GRP=[]
def emit(e, lay):
    t=e.dxftype()
    if t=="INSERT":
        try:
            for v in e.virtual_entities(): emit(v, e.dxf.layer if e.dxf.layer!="0" else lay)
        except Exception: pass
        return
    L=e.dxf.layer if e.dxf.layer!="0" else lay
    g=group(L)
    if g is None: return
    def add(a,b):
        if (inband(a[0],a[1]) or inband(b[0],b[1])) and sane(*a) and sane(*b):
            SEG.append((a[0],a[1],b[0],b[1])); GRP.append(g)
    try:
        if t=="LINE": add((e.dxf.start.x,e.dxf.start.y),(e.dxf.end.x,e.dxf.end.y))
        elif t=="LWPOLYLINE":
            pts=[(p[0],p[1]) for p in e.get_points()]
            for i in range(len(pts)-1): add(pts[i],pts[i+1])
            if e.closed and len(pts)>2: add(pts[-1],pts[0])
        elif t=="POLYLINE":
            pts=[(v.dxf.location.x,v.dxf.location.y) for v in e.vertices]
            for i in range(len(pts)-1): add(pts[i],pts[i+1])
        elif t in ("ARC","CIRCLE","ELLIPSE","SPLINE"):
            try:
                vs=list(e.flattening(1.0))
                for i in range(len(vs)-1): add((vs[i].x,vs[i].y),(vs[i+1].x,vs[i+1].y))
            except Exception: pass
    except Exception: pass

for e in msp: emit(e,"0")
if not SEG:
    print("구조 선분 없음"); sys.exit(1)

s=np.asarray(SEG,float); grp=np.asarray(GRP,object)
print(f"=== 구조 선분 {len(s)} ===")
for g,c in Counter(GRP).most_common(): print(f"  {c:7d}  {g}")

# 건물 클러스터 추출: 흩어진 잔여선 제거를 위해 축별 '밀집영역(sliding window)'만 유지.
# 건물은 ~70m 폭. window(x=100m,y=90m)를 1m 스텝으로 밀어 선분 중점이 가장 많은 위치 = 건물.
mx=(s[:,0]+s[:,2])/2; my=(s[:,1]+s[:,3])/2
def densest(v, win):
    lo,hi=v.min(),v.max()
    if hi-lo<=win: return lo,hi
    vs=np.sort(v); best_c,best_a=0,lo
    for a in np.arange(lo,hi-win,1000.0):  # 1m 스텝
        c=np.searchsorted(vs,a+win)-np.searchsorted(vs,a)
        if c>best_c: best_c,best_a=c,a
    return best_a,best_a+win
wx0,wx1=densest(mx,100_000.0); wy0,wy1=densest(my,90_000.0)
insb=(mx>=wx0)&(mx<=wx1)&(my>=wy0)&(my<=wy1)
s=s[insb]; grp=grp[insb]
bx0=min(s[:,0].min(),s[:,2].min()); bx1=max(s[:,0].max(),s[:,2].max())
by0=min(s[:,1].min(),s[:,3].min()); by1=max(s[:,1].max(),s[:,3].max())
Wm=(bx1-bx0)/1000; Hm=(by1-by0)/1000
print(f"=== 건물 footprint: {Wm:.1f} × {Hm:.1f} m (참고 map_floor3=93.8×66.9) ===")

COLG={"WALL":"#111","DOOR":"#c0392b","GLASS":"#2980b9","COLUMN":"#7d3c98","STAIR":"#148f77"}
pad=1500; x0,x1,y0,y1=bx0-pad,bx1+pad,by0-pad,by1+pad
Wm2=(x1-x0)/1000; Hm2=(y1-y0)/1000
fig,ax=plt.subplots(figsize=(16,16*Hm2/Wm2+0.6)); gs=5000
for g in np.arange(math.ceil(x0/gs)*gs,x1,gs): ax.axvline(g,color="#e6eef5",lw=0.3,zorder=0)
for g in np.arange(math.ceil(y0/gs)*gs,y1,gs): ax.axhline(g,color="#e6eef5",lw=0.3,zorder=0)
for gname,col in COLG.items():
    m=grp==gname
    if m.any(): ax.add_collection(LineCollection(s[m].reshape(-1,2,2),colors=col,linewidths=0.6,zorder=2))
ax.plot([x0+1500,x0+11500],[y0+2500,y0+2500],color="k",lw=3); ax.text(x0+1500,y0+4000,"10 m",fontsize=10)
ax.set_xlim(x0,x1); ax.set_ylim(y0,y1); ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
hands=[plt.Line2D([0],[0],color=c,lw=2,label=g) for g,c in COLG.items()]
ax.legend(handles=hands,loc="upper right",fontsize=9)
ax.set_title(f"CJ제일제당센터 지상1층 (구조만) · {Wm:.0f}×{Hm:.0f} m · 격자 5m",fontsize=13)
OUT="cad/cj_cad/_preview_1f_keep.png" if not SAVE else "cad/cj_cad/CJ제일제당센터_1F.png"
fig.savefig(OUT,dpi=160,facecolor="white",bbox_inches="tight"); plt.close(fig)
print(f"PNG: /home/pia/seoik/MCMOT_gaza/{OUT}")

if SAVE:
    # 척도 PNG (별도) — 동일 이미지 재사용(격자·스케일 이미 포함)
    DXF_OUT="cad/cj_cad/CJ제일제당센터_1F.dxf"
    nd=ezdxf.new(setup=True); nd.header["$INSUNITS"]=4
    nm=nd.modelspace(); ox,oy=bx0,by0
    nd.layers.add("WALL",color=7); nd.layers.add("DOOR",color=1)
    nd.layers.add("GLASS",color=5); nd.layers.add("COLUMN",color=6)
    nd.layers.add("STAIR",color=3); nd.layers.add("ENVELOPE",color=7)
    for a,g in zip(s,grp):
        nm.add_line((a[0]-ox,a[1]-oy),(a[2]-ox,a[3]-oy),dxfattribs={"layer":g})
    # 외곽 실선 테두리
    ENV=[(bx0,by0,bx1,by0),(bx1,by0,bx1,by1),(bx1,by1,bx0,by1),(bx0,by1,bx0,by0)]
    for a in ENV:
        nm.add_line((a[0]-ox,a[1]-oy),(a[2]-ox,a[3]-oy),dxfattribs={"layer":"ENVELOPE","lineweight":50})
    nd.saveas(DXF_OUT)
    print(f"DXF: /home/pia/seoik/MCMOT_gaza/{DXF_OUT}")
