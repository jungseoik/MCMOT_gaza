#!/usr/bin/env python
"""A-101에서 지상1층 band만 추출 → 미리보기 PNG + band 내 레이어별 선분 수.
아직 DXF 저장 안 함(레이어 선별 전 단계). 사용자가 레이어 결정하면 save_1f.py 로 저장.

band: 지상1층 캡션 y≈2,296,152, 위 지하1층 캡션 2,414,952 → 도면은 그 사이.
"""
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, numpy as np, math, sys
from matplotlib.collections import LineCollection
import ezdxf
from collections import Counter

DXF = "cad/cj_cad/_converted/A-101_128_각 층 평면도_최종_수정.dxf"
# 지상1층 band (mm). 넉넉히 캡션 바로 밑 ~ 지하1층 캡션.
Y0, Y1 = 2_290_000, 2_418_000
# 좌표 쓰레기(±수백 km) 클램프 — 전개 폭주로 생기는 가짜 엔티티 제거
CLAMP = 50_000_000

doc = ezdxf.readfile(DXF)
msp = doc.modelspace()

SEG = []      # (x1,y1,x2,y2)
LAY = []      # layer per seg
lay_cnt = Counter()
def sane(x, y):
    return abs(x) < CLAMP and abs(y) < CLAMP
def inband(x, y):
    return Y0 <= y <= Y1 and sane(x, y)

def emit(e, lay):
    t = e.dxftype()
    if t == "INSERT":
        try:
            for v in e.virtual_entities():
                emit(v, e.dxf.layer if e.dxf.layer != "0" else lay)
        except Exception:
            pass
        return
    L = e.dxf.layer if e.dxf.layer != "0" else lay
    def add(a, b):
        if inband(a[0], a[1]) or inband(b[0], b[1]):
            if sane(*a) and sane(*b):
                SEG.append((a[0], a[1], b[0], b[1]))
                LAY.append(L)
                lay_cnt[L] += 1
    try:
        if t == "LINE":
            add((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y))
        elif t == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points()]
            for i in range(len(pts) - 1):
                add(pts[i], pts[i + 1])
            if e.closed and len(pts) > 2:
                add(pts[-1], pts[0])
        elif t == "POLYLINE":
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
            for i in range(len(pts) - 1):
                add(pts[i], pts[i + 1])
        elif t in ("ARC", "CIRCLE", "ELLIPSE", "SPLINE"):
            try:
                vs = list(e.flattening(1.0))
                for i in range(len(vs) - 1):
                    add((vs[i].x, vs[i].y), (vs[i + 1].x, vs[i + 1].y))
            except Exception:
                pass
    except Exception:
        pass

for e in msp:
    emit(e, "0")

if not SEG:
    print("band에 형상 없음 — Y 범위 재확인 필요")
    sys.exit(1)

s = np.asarray(SEG, float)
lay = np.asarray(LAY, object)
# 강건 bbox (0.5~99.5 백분위)
xa = np.r_[s[:, 0], s[:, 2]]; ya = np.r_[s[:, 1], s[:, 3]]
bx0, bx1 = np.percentile(xa, 0.5), np.percentile(xa, 99.5)
by0, by1 = np.percentile(ya, 0.5), np.percentile(ya, 99.5)
insb = (s[:,0]>=bx0)&(s[:,0]<=bx1)&(s[:,2]>=bx0)&(s[:,2]<=bx1)&\
       (s[:,1]>=by0)&(s[:,1]<=by1)&(s[:,3]>=by0)&(s[:,3]<=by1)
s2 = s[insb]; lay2 = lay[insb]
bx0 = min(s2[:,0].min(), s2[:,2].min()); bx1 = max(s2[:,0].max(), s2[:,2].max())
by0 = min(s2[:,1].min(), s2[:,3].min()); by1 = max(s2[:,1].max(), s2[:,3].max())
Wm = (bx1-bx0)/1000; Hm = (by1-by0)/1000

print(f"=== band 내 선분 {len(s)} (robust bbox 후 {len(s2)}) ===")
print(f"=== 지상1층 도면 범위: {Wm:.1f} × {Hm:.1f} m  (참고 map_floor3.png = 93.8×66.9 m) ===")
print(f"=== X[{bx0:.0f}..{bx1:.0f}] Y[{by0:.0f}..{by1:.0f}] mm ===\n")

# bbox 내 레이어별 선분 수
cnt2 = Counter()
for L in lay2:
    cnt2[L] += 1
print(f"=== bbox 내 레이어별 선분 수 (전 {len(cnt2)}개 레이어) ===")
for L, c in cnt2.most_common():
    print(f"  {c:8d}  {L}")

# 미리보기 PNG (전 레이어, 검정)
pad = 2000
x0, x1, y0, y1 = bx0-pad, bx1+pad, by0-pad, by1+pad
Wm2 = (x1-x0)/1000; Hm2 = (y1-y0)/1000
fig, ax = plt.subplots(figsize=(16, 16*Hm2/Wm2 + 0.5))
gs = 5000
for g in np.arange(math.ceil(x0/gs)*gs, x1, gs): ax.axvline(g, color="#e3eef7", lw=0.3, zorder=0)
for g in np.arange(math.ceil(y0/gs)*gs, y1, gs): ax.axhline(g, color="#e3eef7", lw=0.3, zorder=0)
ax.add_collection(LineCollection(s2.reshape(-1,2,2), colors="#111", linewidths=0.4, zorder=2))
ax.plot([x0+2000, x0+12000], [y0+3000, y0+3000], color="r", lw=3)
ax.text(x0+2000, y0+5000, "10 m", fontsize=10, color="r")
ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
ax.set_xticks([]); ax.set_yticks([])
ax.set_title(f"A-101 지상1층 band (전 레이어) · {Wm:.0f}×{Hm:.0f} m", fontsize=13)
OUT = "cad/cj_cad/_preview_1f_all.png"
fig.savefig(OUT, dpi=150, facecolor="white", bbox_inches="tight"); plt.close(fig)
print(f"\n미리보기 저장: /home/pia/seoik/MCMOT_gaza/{OUT}")
