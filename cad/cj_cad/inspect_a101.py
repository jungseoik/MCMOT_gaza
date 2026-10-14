#!/usr/bin/env python
"""A-101(각 층 평면도 통짜 시트) 구조 분석 — 1층 band 찾기용.
출력: (1)전체 extents (2)레이어별 엔티티 수·종류 (3)y 히스토그램으로 층 밴드 추정
     (4)재귀 전개 후 선분 좌표 분포.
사용: inspect_a101.py <dxf경로>
"""
import sys, math, numpy as np, ezdxf
from collections import Counter, defaultdict

DXF = sys.argv[1] if len(sys.argv) > 1 else "cad/cj_cad/_converted/A-101_128_각 층 평면도_최종_수정.dxf"
doc = ezdxf.readfile(DXF)
msp = doc.modelspace()

h = doc.header
print("=== 헤더 ===")
print("  $INSUNITS:", h.get("$INSUNITS"))
for k in ("$EXTMIN", "$EXTMAX"):
    print(f"  {k}:", h.get(k))

# 레이어 목록
print("\n=== 레이어 (정의) ===", len(doc.layers))

# top-level 엔티티 타입 분포
top_types = Counter(e.dxftype() for e in msp)
print("\n=== top-level 엔티티 타입 ===")
for t, c in top_types.most_common():
    print(f"  {t:14s} {c}")

# 재귀 전개하여 선분 수집 (레이어별 bbox + y 분포)
SEG = []          # (x1,y1,x2,y2,layer)
layer_cnt = Counter()
def flat(e, lay):
    t = e.dxftype()
    if t == "INSERT":
        try:
            for v in e.virtual_entities():
                flat(v, e.dxf.layer if e.dxf.layer != "0" else lay)
        except Exception:
            pass
        return
    L = e.dxf.layer if e.dxf.layer != "0" else lay
    layer_cnt[L] += 1
    def add(a, b):
        SEG.append((a[0], a[1], b[0], b[1], L))
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
    flat(e, "0")

print(f"\n=== 전개 후 선분 수: {len(SEG)} ===")
print("\n=== 레이어별 (전개 후) 엔티티 수 — 상위 40 ===")
for L, c in layer_cnt.most_common(40):
    print(f"  {c:8d}  {L}")

if not SEG:
    print("선분 없음")
    sys.exit(0)

s = np.asarray([(a, b, c, d) for a, b, c, d, _ in SEG], float)
xa = np.r_[s[:, 0], s[:, 2]]
ya = np.r_[s[:, 1], s[:, 3]]
print("\n=== 전체 좌표 범위 (전개 후, 선분 기준) ===")
print(f"  X: {xa.min():.0f} .. {xa.max():.0f}  (폭 {(xa.max()-xa.min())/1000:.1f} m @mm)")
print(f"  Y: {ya.min():.0f} .. {ya.max():.0f}  (높이 {(ya.max()-ya.min())/1000:.1f} m @mm)")

# Y 히스토그램 — 층 밴드(세로 적층) 추정
print("\n=== Y 분포 히스토그램 (선분 중점 y, 40 bins) — 밀집 band = 층 ===")
my = (s[:, 1] + s[:, 3]) / 2
hist, edges = np.histogram(my, bins=40)
mx_h = hist.max()
for i in range(len(hist)):
    bar = "#" * int(40 * hist[i] / mx_h) if mx_h else ""
    print(f"  y[{edges[i]:11.0f} .. {edges[i+1]:11.0f}] {hist[i]:7d} {bar}")

# X 히스토그램도 (가로 배치일 수도)
print("\n=== X 분포 히스토그램 (선분 중점 x, 40 bins) ===")
mxx = (s[:, 0] + s[:, 2]) / 2
hist, edges = np.histogram(mxx, bins=40)
mx_h = hist.max()
for i in range(len(hist)):
    bar = "#" * int(40 * hist[i] / mx_h) if mx_h else ""
    print(f"  x[{edges[i]:11.0f} .. {edges[i+1]:11.0f}] {hist[i]:7d} {bar}")

# 층 라벨 텍스트 찾기 (1F/1층/지상1층 등) — 위치가 곧 band 단서
print("\n=== 층 라벨 후보 텍스트 (위치 포함) ===")
import re
pat = re.compile(r"(지상|지하|B?\d+\s*F|\d+\s*층|1F|PIT|옥탑|RF|PH)")
seen = 0
for e in msp:
    if e.dxftype() in ("TEXT", "MTEXT"):
        ss = (e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text) or ""
        line = ss.split("\n")[0].strip()
        if pat.search(line) and len(line) < 20:
            try:
                ins = e.dxf.insert
                print(f"  @({ins.x:11.0f},{ins.y:11.0f})  {line!r}")
                seen += 1
            except Exception:
                pass
    if seen > 60:
        print("  ...(생략)")
        break
