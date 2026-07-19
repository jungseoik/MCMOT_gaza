"""
evac.cad — DXF 입출력. 장애물/Exit/Occupant 수집 + Exit 역주입(Evac_Exit 선 쓰기).

좌표계: 척도이미지(17F_plan_scale.png)와 동일하게 SW코너(extents min)=0 기준 미터를 쓴다.
world(도면단위 mm) ↔ meter 변환 헬퍼 제공.
"""
import math
from collections import deque

import numpy as np

SKIP_LAYERS = {"Evac_Exit", "Evac_Occupant",
               "Evac_Path_Pass", "Evac_Path_Fail", "Evac_Worst5"}


class DxfData:
    def __init__(self, obstacles, exits, occupants, bounds, doc, path):
        self.obstacles = obstacles      # (N,4) mm
        self.exits = exits              # [(x,y)] mm  (Evac_Exit Line 중점)
        self.occupants = occupants      # [(x,y)] mm  (Evac_Occupant 꼭짓점)
        self.bounds = bounds            # (minx,miny,maxx,maxy) mm  (SW=min)
        self.doc = doc
        self.path = path

    # ── 미터(SW=0) ↔ 도면좌표(mm)
    def m_to_world(self, xm, ym):
        return (self.bounds[0] + xm * 1000.0, self.bounds[1] + ym * 1000.0)

    def world_to_m(self, x, y):
        return ((x - self.bounds[0]) / 1000.0, (y - self.bounds[1]) / 1000.0)


def load_dxf(path):
    """DXF → DxfData. INSERT(블록) 재귀 전개, 특수레이어 분리, HATCH 무시."""
    import ezdxf
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    obstacles, exits, occupants = [], [], []

    def add_poly(pts, closed):
        pts = [(p[0], p[1]) for p in pts]
        if closed and len(pts) >= 2:
            pts = pts + [pts[0]]
        for a, b in zip(pts[:-1], pts[1:]):
            obstacles.append((a[0], a[1], b[0], b[1]))

    def emit(e):
        t = e.dxftype()
        try:
            if t == "LINE":
                obstacles.append((e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y))
            elif t == "LWPOLYLINE":
                add_poly(list(e.get_points()), e.closed)
            elif t == "POLYLINE":
                add_poly([(p[0], p[1]) for p in e.points()], e.is_closed)
            elif t in ("ARC", "CIRCLE", "ELLIPSE", "SPLINE"):
                pts = list(e.flattening(distance=2.0))
                add_poly([(p[0], p[1]) for p in pts], closed=(t == "CIRCLE"))
        except Exception:
            pass

    stack = deque(msp)
    guard = 0
    while stack:
        guard += 1
        if guard > 5_000_000:
            break
        e = stack.popleft()
        layer, t = e.dxf.layer, e.dxftype()
        if layer == "Evac_Exit" and t == "LINE":
            exits.append(((e.dxf.start.x + e.dxf.end.x) / 2.0,
                          (e.dxf.start.y + e.dxf.end.y) / 2.0))
            continue
        if layer == "Evac_Occupant":
            if t == "LWPOLYLINE":
                occupants.extend((p[0], p[1]) for p in e.get_points())
            elif t == "POLYLINE":
                occupants.extend((p[0], p[1]) for p in e.points())
            elif t == "LINE":
                occupants.append((e.dxf.start.x, e.dxf.start.y))
                occupants.append((e.dxf.end.x, e.dxf.end.y))
            continue
        if layer in SKIP_LAYERS or t == "HATCH":
            continue
        if t == "INSERT":
            try:
                for ve in e.virtual_entities():
                    stack.append(ve)
            except Exception:
                pass
            continue
        emit(e)

    obstacles = np.array(obstacles, float) if obstacles else np.zeros((0, 4))
    bounds = _bounds(doc, obstacles, exits, occupants)
    return DxfData(obstacles, exits, occupants, bounds, doc, path)


def _bounds(doc, obstacles, exits, occupants):
    """헤더 $EXTMIN/$EXTMAX 우선(멀리 떨어진 stray 배제), 없으면 형상 bbox."""
    try:
        (xmn, ymn, _), (xmx, ymx, _) = doc.header["$EXTMIN"], doc.header["$EXTMAX"]
        if xmx - xmn > 1.0 and ymx - ymn > 1.0:
            return (xmn, ymn, xmx, ymx)
    except Exception:
        pass
    if len(obstacles):
        xs = np.r_[obstacles[:, 0], obstacles[:, 2]]
        ys = np.r_[obstacles[:, 1], obstacles[:, 3]]
        return (xs.min(), ys.min(), xs.max(), ys.max())
    pts = exits + occupants
    xs = np.array([p[0] for p in pts]); ys = np.array([p[1] for p in pts])
    return (xs.min(), ys.min(), xs.max(), ys.max())


def load_dxf_entities(path):
    """DXF → 엔티티 단위 지오메트리(웹 편집기용 — 엔티티별 삭제 지원).

    top-level 엔티티 1개 = 삭제 단위. INSERT(블록참조)는 통째로 한 단위
    (문짝 블록을 한 번에 지우는 게 목적에 부합). 특수레이어(Evac_*)는 제외.

    반환: (entities, exits, occupants, bounds, doc)
      entities = [{"handle": str, "layer": str, "segs": [(x1,y1,x2,y2), ...]}]
    """
    import ezdxf
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    entities, exits, occupants = [], [], []

    def collect(e, out):
        t = e.dxftype()
        def add_poly(pts, closed):
            pts = [(p[0], p[1]) for p in pts]
            if closed and len(pts) >= 2:
                pts = pts + [pts[0]]
            for a, b in zip(pts[:-1], pts[1:]):
                out.append((a[0], a[1], b[0], b[1]))
        try:
            if t == "LINE":
                out.append((e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y))
            elif t == "LWPOLYLINE":
                add_poly(list(e.get_points()), e.closed)
            elif t == "POLYLINE":
                add_poly([(p[0], p[1]) for p in e.points()], e.is_closed)
            elif t in ("ARC", "CIRCLE", "ELLIPSE", "SPLINE"):
                pts = list(e.flattening(distance=2.0))
                add_poly([(p[0], p[1]) for p in pts], closed=(t == "CIRCLE"))
            elif t == "INSERT":
                stack = deque(e.virtual_entities())
                guard = 0
                while stack:
                    guard += 1
                    if guard > 200000:
                        break
                    ve = stack.popleft()
                    if ve.dxftype() == "INSERT":
                        try:
                            stack.extend(ve.virtual_entities())
                        except Exception:
                            pass
                    else:
                        collect(ve, out)
        except Exception:
            pass

    for e in msp:
        layer, t = e.dxf.layer, e.dxftype()
        if layer == "Evac_Exit" and t == "LINE":
            exits.append(((e.dxf.start.x + e.dxf.end.x) / 2.0,
                          (e.dxf.start.y + e.dxf.end.y) / 2.0))
            continue
        if layer == "Evac_Occupant":
            if t == "LWPOLYLINE":
                occupants.extend((p[0], p[1]) for p in e.get_points())
            elif t == "POLYLINE":
                occupants.extend((p[0], p[1]) for p in e.points())
            elif t == "LINE":
                occupants.append((e.dxf.start.x, e.dxf.start.y))
                occupants.append((e.dxf.end.x, e.dxf.end.y))
            continue
        if layer in SKIP_LAYERS or t == "HATCH":
            continue
        segs = []
        collect(e, segs)
        if segs:
            entities.append({"handle": str(e.dxf.handle), "layer": layer, "segs": segs})

    all_segs = [s for ent in entities for s in ent["segs"]]
    obstacles = np.array(all_segs, float) if all_segs else np.zeros((0, 4))
    bounds = _bounds(doc, obstacles, exits, occupants)
    return entities, exits, occupants, bounds, doc


def load_reference_paths(doc):
    """매크로 원본 출력 경로(검증 비교용). {'pass':[[(x,y)]], 'fail':[...]}"""
    msp = doc.modelspace()
    ref = {"pass": [], "fail": []}
    for e in msp:
        if e.dxftype() != "LWPOLYLINE":
            continue
        if e.dxf.layer == "Evac_Path_Pass":
            ref["pass"].append([(p[0], p[1]) for p in e.get_points()])
        elif e.dxf.layer == "Evac_Path_Fail":
            ref["fail"].append([(p[0], p[1]) for p in e.get_points()])
    return ref


def write_exits_dxf(src_path, out_path, exits_world, bar_len=1000.0):
    """exits_world[(x,y)] 를 원본 DXF에 'Evac_Exit' 레이어 짧은 Line 으로 역주입.
    → 매크로 입력 규약과 동일한 '태깅된 도면' 생성(재사용/검증용). bar_len=선길이(mm)."""
    import ezdxf
    doc = ezdxf.readfile(src_path)
    msp = doc.modelspace()
    if "Evac_Exit" not in doc.layers:
        doc.layers.add("Evac_Exit", color=5)   # 파랑
    h = bar_len / 2.0
    for (x, y) in exits_world:
        msp.add_line((x - h, y), (x + h, y), dxfattribs={"layer": "Evac_Exit"})
    doc.saveas(out_path)
    return out_path


def save_exits_json(path, exits_m, meta=None):
    import json
    data = {"exits_m": [[round(x, 2), round(y, 2)] for x, y in exits_m]}
    if meta:
        data["meta"] = meta
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def load_exits_json(path):
    import json
    with open(path) as f:
        return [tuple(p) for p in json.load(f)["exits_m"]]
