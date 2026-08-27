#!/usr/bin/env python
"""AI지원센터 xref-PLAN.dxf 층별 깨끗한 평면도(벽+방+코어+문) 렌더.
사용: render_plan.py <층 1..7> [out.png]

전략(전체 유지 + 기하 정리):
- xref 한 파일에 1F~7F 가 세로로 쌓임(층당 ΔY≈-89100). 각 층 상세도 X[556000,666000].
- 형상 대부분이 중첩 블록 → 재귀 전개해야 벽이 나온다.
- '확실한 클러터 레이어'만 제거하고 나머지(벽 포함)는 전부 유지한다.
  그런 뒤 '가장자리 밴드의 짧은 세그먼트'(기둥·멀리언·파사드 반복심볼)만
  기하학적으로 제거 → 중앙의 사무실 칸막이벽(긴 선)은 보존된다.
- 외곽은 굵은 실선으로 마감(샘플 스타일).
"""
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, numpy as np, math, re, sys
from matplotlib import font_manager as fm
from matplotlib.collections import LineCollection
import ezdxf
for fp in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",):
    try: fm.fontManager.addfont(fp); plt.rcParams["font.family"]=fm.FontProperties(fname=fp).get_name()
    except Exception: pass

DXF="cad/ai_hub_cad/_converted/xref-AI지원센터-PLAN.dxf"
FLOOR=int(sys.argv[1]) if len(sys.argv)>1 else 3
OUT=sys.argv[2] if len(sys.argv)>2 else f"cad/ai_hub_cad/_converted/plan_{FLOOR}F.png"
X0,X1=556000,666000
# 2~7층은 음수 y에 -89,100 간격으로 쌓임(3F≈-286k). 3F 기준 창을 이동.
# ※ 1층은 지상층이라 별도 위치(양수 y≈+82k)에 그려져 있어 예외 창 사용.
if FLOOR==1:
    Y0,Y1=45000,120000
else:
    DY=89100*(3-FLOOR); Y0,Y1=-322000+DY,-250000+DY

# 첫 전체렌더(사용자 확정)에서 '딱 그것만' 제거하는 최소 제거 방식.
# 방이 '막혀 보이던' 건 가구·집기가 공간을 채워서였으므로 가구는 유지한다.
# 제거: '불빛'(A_LIGHTING_*) · 천정선(*CEILING) · 소화전 반경원(Defpoint*) ·
#       개정 클라우드(A_REV_*) · 조경/수목/대지/주차/설비 등 도면 외 사이트 레이어.
DROP_PREFIX=("A_LIGHTING","Defpoint","A_REV_","TREE","0L","-L","LA_","LP-","DVM_",
             "cable","장비","_교목","_잔디","00_","0_","0L-","210","A_PARKING")
DROP_EXACT={"주차관제","대지경계선","그림자","솔리드","느티나무","은행나무","그림자",
            "A_LANDSCAPE","A_PARKING","A_DIM","A_ANNO_LEVEL","A_ANNO_NOTE",
            "TREE-shadow","Height","DEF","ELE","AR","A_COLUMN",   # A_COLUMN=기둥 제거
            "A_LINE","tc-2","tc-s"}   # A_LINE=1층 바닥타일 격자, tc-*=수목
DROP_CONTAINS=("CEILING","xref-AI지원센터-PLAN_rev","조경","수목","나무","AREA","점자")  # 점자블록 원 제거
def drop(lay):
    if lay in DROP_EXACT: return True
    if any(lay.startswith(p) for p in DROP_PREFIX): return True
    if any(c in lay for c in DROP_CONTAINS): return True
    return False

CURTAIN={"A_WINDOW","A_WIN","A-WIN"}   # 외곽 커튼월 유닛 심볼이 있는 레이어(가장자리만 정리)
doc=ezdxf.readfile(DXF); msp=doc.modelspace()
def inwin(x,y): return X0<=x<=X1 and Y0<=y<=Y1
SEG=[]; CURT=[]
def emit(e,lay):
    t=e.dxftype()
    if t=="INSERT":
        nm=(e.dxf.name or "").upper()
        if "COLUMN" in nm:  # 모든 기둥 심볼 제거(원형·각형, 사용자 요청)
            return
        try:
            for v in e.virtual_entities(): emit(v, e.dxf.layer if e.dxf.layer!="0" else lay)
        except Exception: pass
        return
    L=e.dxf.layer if e.dxf.layer!="0" else lay
    if drop(L): return
    isc=L in CURTAIN
    def add(a,b):
        if inwin(a[0],a[1]) or inwin(b[0],b[1]): SEG.append((a[0],a[1],b[0],b[1])); CURT.append(isc)
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
            try: vs=list(e.flattening(1.0))
            except Exception: vs=None
            if vs:
                for i in range(len(vs)-1): add((vs[i].x,vs[i].y),(vs[i+1].x,vs[i+1].y))
    except Exception: pass
for e in msp: emit(e,"0")
if not SEG:
    print(f"{FLOOR}F: 형상 없음"); sys.exit(1)
s=np.asarray(SEG,float); curt0=np.asarray(CURT,bool)
# 강건 bbox: 좌표 백분위수로 사이트 잔여선(창 경계 걸침) 배제
xa=np.r_[s[:,0],s[:,2]]; ya=np.r_[s[:,1],s[:,3]]
bx0,bx1=np.percentile(xa,0.5),np.percentile(xa,99.5)
by0,by1=np.percentile(ya,0.5),np.percentile(ya,99.5)
# bbox 안(양끝 모두)만 유지
insb=(s[:,0]>=bx0)&(s[:,0]<=bx1)&(s[:,2]>=bx0)&(s[:,2]<=bx1)&\
     (s[:,1]>=by0)&(s[:,1]<=by1)&(s[:,3]>=by0)&(s[:,3]<=by1)
s=s[insb]; curt0=curt0[insb]
# 실제 형상 기준으로 bbox 재계산
bx0=min(s[:,0].min(),s[:,2].min()); bx1=max(s[:,0].max(),s[:,2].max())
by0=min(s[:,1].min(),s[:,3].min()); by1=max(s[:,1].max(),s[:,3].max())
# 가장자리 밴드의 짧은 세그먼트(기둥/멀리언/파사드 심볼)만 제거
# 커튼월 유닛 심볼만(A_WINDOW 계열) 가장자리 밴드에서 짧은 것 제거.
# 내부 벽·유리 칸막이는 전부 보존.
EB=4000; SHORT=1800
mx=(s[:,0]+s[:,2])/2; my=(s[:,1]+s[:,3])/2
ln=np.hypot(s[:,2]-s[:,0],s[:,3]-s[:,1])
band=(mx<bx0+EB)|(mx>bx1-EB)|(my<by0+EB)|(my>by1-EB)
keep=~(band&(ln<SHORT))   # 가장자리 얇은 밴드의 짧은 창호/조명 심볼 제거(안쪽 벽 보존)
k=s[keep]
# 외곽 실선 테두리
ENV=np.asarray([(bx0,by0,bx1,by0),(bx1,by0,bx1,by1),(bx1,by1,bx0,by1),(bx0,by1,bx0,by0)],float)

# ── 유리파티션 오버레이(원본 평면엔 없음) ──
# 유리벽 확정: A701 창호일람표 = 폰룸/소회의실/업무공간 T5 투명강화유리·T10 강화유리.
# 평면 좌표 소스가 없어, AI hub 샘플 도면(사용자 확정)의 중앙 클러스터 구조를
# 그대로 실제 평면 좌표에 옮겨 그린다. 청색 = 유리파티션.
GLASS=[]; GLABEL=[]
if False:  # 유리파티션 오버레이 비활성(사용자 롤백)
    xL,xR=590000.0,603500.0; W=xR-xL
    yT,yB=-281000.0,-291500.0; dY=yB-yT
    def xf(f): return xL+f*W
    def yf(f): return yT+f*dY
    def W_(a,b,c,d): GLASS.append((a,b,c,d))
    ym=yf(0.55)
    # 외곽 + 상/하 구분
    W_(xL,yT,xR,yT); W_(xR,yT,xR,yB); W_(xR,yB,xL,yB); W_(xL,yB,xL,yT)
    W_(xL,ym,xR,ym)
    # 상단: 좌 큰셀 | 좁은열(2분할) | 우 큰셀+하부밴드
    W_(xf(0.25),yT,xf(0.25),ym); W_(xf(0.38),yT,xf(0.38),ym)
    W_(xf(0.25),yf(0.30),xf(0.38),yf(0.30))
    W_(xf(0.38),yf(0.33),xR,yf(0.33))
    # 하단: 9셀 | 10셀 | 우측 2x2 그리드
    W_(xf(0.22),ym,xf(0.22),yB); W_(xf(0.48),ym,xf(0.48),yB); W_(xf(0.68),ym,xf(0.68),yB)
    W_(xf(0.48),yf(0.78),xf(0.68),yf(0.78))
    W_(xf(0.68),yf(0.80),xR,yf(0.80))
    # 라벨(샘플 셀 위치에)
    GLABEL=[(xf(0.11),yf(0.78),"소회의실_02"),(xf(0.35),yf(0.78),"소회의실_01"),
            (xf(0.58),yf(0.66),"폰룸_01"),(xf(0.58),yf(0.90),"폰룸_02"),
            (xf(0.84),yf(0.67),"폰룸_03"),(xf(0.84),yf(0.90),"폰룸_04"),
            (xf(0.5),yf(0.02),"유리파티션(T5강화유리)")]
GLASS=np.asarray(GLASS,float) if GLASS else np.zeros((0,4))

# 실명 텍스트
BAD=("반영","변경","수정","추가","축소","삭제","위치","type","CL","PY","BF","안전망",
     "맞춤","보이드","자문","항온","mm","500","이동","삭 제","위 치","공용부","#","인승",
     # 마감/시공 주석 텍스트(방이름 아님)
     "루버","석고","스터드","방수","단차","결정","하부","구획","보드","표현","주차",
     "SL","x8","x1","반경","높이","단면","두께","필요","O.A","E.A","블럭","블록")
def isroom(x):
    x=x.strip()
    if not x or any(b in x for b in BAD): return False
    if re.fullmatch(r"[\d,\.\-~()A-Za-z]+",x): return False
    return True
texts=[]
for e in msp:
    if e.dxftype() in ("TEXT","MTEXT"):
        ss=(e.plain_text() if e.dxftype()=="MTEXT" else e.dxf.text) or ""
        line=ss.split("\n")[0].strip()
        if isroom(line):
            try: texts.append((e.dxf.insert.x,e.dxf.insert.y,line[:12]))
            except: pass

pad=1500; x0,x1,y0,y1=bx0-pad,bx1+pad,by0-pad,by1+pad
Wm=(x1-x0)*.001; Hm=(y1-y0)*.001
fig,axp=plt.subplots(figsize=(15,15*Hm/Wm+0.5)); gs=5000
for g in np.arange(math.ceil(x0/gs)*gs,x1,gs): axp.axvline(g,color="#dbe8f4",lw=0.4,zorder=0)
for g in np.arange(math.ceil(y0/gs)*gs,y1,gs): axp.axhline(g,color="#dbe8f4",lw=0.4,zorder=0)
axp.add_collection(LineCollection(k.reshape(-1,2,2),colors="#111",linewidths=0.6,zorder=2))
axp.add_collection(LineCollection(ENV.reshape(-1,2,2),colors="#111",linewidths=2.2,zorder=4))
if len(GLASS):
    axp.add_collection(LineCollection(GLASS.reshape(-1,2,2),colors="#0a6fd6",linewidths=1.6,zorder=5))
    for gx,gy,gs in GLABEL:
        fs=6 if gs.startswith("유리") else 7
        axp.text(gx,gy,gs,fontsize=fs,color="#0a6fd6",ha="center",va="center",zorder=6,fontweight="bold")
nn=0
for tx,ty,ss in texts:
    if x0<=tx<=x1 and y0<=ty<=y1:
        axp.text(tx,ty,ss,fontsize=8,color="#c00",ha="center",va="center",zorder=5,fontweight="bold"); nn+=1
axp.plot([x0+1500,x0+11500],[y0+2000,y0+2000],color="k",lw=3); axp.text(x0+1500,y0+3300,"10 m",fontsize=9)
axp.set_xlim(x0,x1); axp.set_ylim(y0,y1); axp.set_aspect("equal"); axp.set_xticks([]); axp.set_yticks([])
axp.set_title(f"AI지원센터 {FLOOR}층 평면도 · 격자 5m · 약 {Wm:.0f}×{Hm:.0f} m",fontsize=13)
fig.savefig(OUT,dpi=160,facecolor="white",bbox_inches="tight"); plt.close(fig)

# ── CAD(DXF) 내보내기 ── 정리된 선만 담은 벡터 파일(도면 편집기/CAD용).
# 남서(좌하단) 코너를 원점(0,0)으로 이동, 단위 mm 유지($INSUNITS=4).
DXF_OUT=OUT.rsplit(".",1)[0]+".dxf"
try:
    nd=ezdxf.new(setup=True); nd.header["$INSUNITS"]=4
    nm=nd.modelspace()
    ox,oy=bx0,by0
    # 도면 편집기용 = 순수 벡터(글자 없음). 실명 텍스트는 PNG에만 남긴다.
    nd.layers.add("WALL",color=7); nd.layers.add("ENVELOPE",color=7)
    nd.layers.add("GLASS",color=5)
    for a in k:
        nm.add_line((a[0]-ox,a[1]-oy),(a[2]-ox,a[3]-oy),dxfattribs={"layer":"WALL"})
    for a in ENV:
        nm.add_line((a[0]-ox,a[1]-oy),(a[2]-ox,a[3]-oy),dxfattribs={"layer":"ENVELOPE","lineweight":50})
    for a in (GLASS if len(GLASS) else []):
        nm.add_line((a[0]-ox,a[1]-oy),(a[2]-ox,a[3]-oy),dxfattribs={"layer":"GLASS"})
    nd.saveas(DXF_OUT)
    print(f"{FLOOR}F: seg{len(k)}/{len(s)} 방{nn} {Wm:.0f}x{Hm:.0f}m -> {OUT} + {DXF_OUT}")
except Exception as e:
    print(f"{FLOOR}F: PNG만 저장(DXF 실패: {e}) -> {OUT}")
