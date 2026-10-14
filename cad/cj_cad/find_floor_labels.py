#!/usr/bin/env python
"""A-101에서 '지상/지하 N층 평면도' 라벨만 전부 모아 y순 정렬 → 층 밴드 지도.
전개 안 함(top-level TEXT만) → 빠르고 좌표 안정적.
"""
import sys, re, ezdxf
from collections import defaultdict

DXF = sys.argv[1]
doc = ezdxf.readfile(DXF)
msp = doc.modelspace()

# "평면도"로 끝나는 층 라벨 (지상N층 평면도 / 지하N층 평면도 / 옥탑...)
pat = re.compile(r"(지하\s*\d+층|지상\s*\d+층|옥탑|PIT|기준층)")
rows = []
for e in msp:
    if e.dxftype() in ("TEXT", "MTEXT"):
        ss = (e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text) or ""
        line = ss.replace("\n", " ").strip()
        if "평면도" in line and pat.search(line) and len(line) < 25:
            try:
                ins = e.dxf.insert
                rows.append((ins.y, ins.x, line))
            except Exception:
                pass

rows.sort(reverse=True)   # y 큰 것(=낮은 층) 먼저
print(f"=== '…층 평면도' 라벨 {len(rows)}개 (y 내림차순) ===")
for y, x, line in rows:
    print(f"  y={y:12.0f}  x={x:11.0f}  {line}")

# 1층 라벨만
print("\n=== '지상1층' 포함 라벨 ===")
for y, x, line in rows:
    if "지상1층" in line.replace(" ", "") or "지상 1층" in line:
        print(f"  y={y:12.0f}  x={x:11.0f}  {line}")
