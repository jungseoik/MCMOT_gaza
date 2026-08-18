"""
evac.web — 도면 편집기 웹서비스 (포트 8910, 기존 시스템과 완전 분리).

운영 웹(:8900) 맵설정에서 "CAD 도면에서 만들기" 버튼 → 새 창으로 여는 편집기.
업로드(DWG/DXF) → 정리(엔티티 삭제·개구부 carve) → Exit 지정 → worstn 검증
→ [저장&적용] 시 data/sites/<site>/ 에 map.png·floor.json·distfield.npz 저장하고,
:8900 이 떠있으면 POST /api/site/map (meta m_per_px, 계약 A-6)으로 즉시 반영.

실행:
  conda run -n boosttrack uvicorn evac.web:app --host 0.0.0.0 --port 8910
  (cwd = cad/CAD_API_Based_Optimal_Evacuation_Route_Algorithm)

설계 원칙
- 원본 DXF 무손상: 편집은 {삭제 handle 목록, 개구부 선, Exit 선} JSON으로만 관리.
- 클라이언트가 세그먼트를 직접 렌더(팬/줌/박스선택) — 서버는 파싱·격자연산·저장만.
- 세션은 메모리 1개(단일 사용자 설정 도구). 새 업로드가 세션을 대체한다.
"""
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from . import cad, core

app = FastAPI(title="evac floor editor")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SITE_ROOT = os.environ.get("SITE_ROOT", os.path.join(REPO, "data", "sites"))
SYSTEM_API = os.environ.get("SYSTEM_API", "http://127.0.0.1:8900")

PREVIEW_CELL = 100.0      # 연결성 프리뷰 셀(mm) — 응답속도 우선
FULL_CELL = 50.0          # 검증/저장 셀(mm) — 원본 매크로와 동일
OPENING_W = 900.0         # 개구부 기본 폭(mm) = 문폭

_lock = threading.Lock()
S = {}                    # 세션(단일): entities, exits0, bounds, edits...


# ───────────────────────────────────────────── 세션 상태
def _edits():
    return {"deleted": S.get("deleted", []),
            "openings": S.get("openings", []),
            "shapes": S.get("shapes", []),
            "starts": S.get("starts", []),
            "exits": S.get("exits", [])}


def _shapes():
    """격자에 적용할 편집 도형 — 구형식 openings(선)를 승격해 합친다.

    op=open(뚫기) / block(막기) × kind=line|rect|poly. 적용 순서(open→block)는
    core.apply_shapes 가 보장한다. 편집기 내부 전용 — :8900 으로 넘기지 않는다."""
    return (core.legacy_openings_to_shapes(S.get("openings"), OPENING_W)
            + list(S.get("shapes") or []))


def _obstacles():
    """삭제 반영된 장애물 세그먼트 (N,4) mm."""
    deleted = set(S.get("deleted", []))
    segs = [s for ent in S["entities"] if ent["handle"] not in deleted
            for s in ent["segs"]]
    return np.array(segs, float) if segs else np.zeros((0, 4))


def _exit_pts():
    """Exit 선 중점 리스트 (mm)."""
    return [((x1 + x2) / 2.0, (y1 + y2) / 2.0)
            for (x1, y1, x2, y2) in S.get("exits", [])]


def _unit_mm() -> float | None:
    """현재 유효한 mm 환산 계수 — 도면 선언값 또는 사용자 지정값."""
    return S.get("unit_mm")


def _size_m(bounds, unit_mm):
    """도면 크기(m). 단위 미확정이면 None — 화면이 '미터'라 단정하지 않게 한다."""
    if not unit_mm:
        return None
    return [round((bounds[2] - bounds[0]) * unit_mm / 1000.0, 1),
            round((bounds[3] - bounds[1]) * unit_mm / 1000.0, 1)]


def _to_m(v: float) -> float:
    """도면단위 → 미터. 단위 미확정이면 mm 로 가정(표시용 폴백)."""
    return v * (_unit_mm() or 1.0) / 1000.0


def _from_m(m: float) -> float:
    """미터 → 도면단위."""
    return m * 1000.0 / (_unit_mm() or 1.0)


def _units_info() -> dict:
    """단위 상태 + 상식 점검. 프론트가 이걸로 경고/입력 UI를 띄운다."""
    b = S.get("bounds")
    mm = _unit_mm()
    info = {"insunits": S.get("insunits"), "unit_name": S.get("unit_name"),
            "unit_mm": mm, "source": S.get("unit_source"),
            "resolved": mm is not None, "warn": None}
    if b and mm:
        wm = (b[2] - b[0]) * mm / 1000.0
        hm = (b[3] - b[1]) * mm / 1000.0
        info["size_m"] = [round(wm, 1), round(hm, 1)]
        # 단위를 잘못 선언한 도면(실제 m인데 헤더는 mm 등)을 잡는 상식 점검
        if max(wm, hm) < 3 or max(wm, hm) > 3000:
            info["warn"] = (f"환산된 도면 크기가 {wm:.1f} × {hm:.1f} m 입니다. "
                            "단위 설정이 맞는지 확인하세요.")
    return info


@app.get("/api/units")
def get_units():
    _require_session()
    return _units_info()


@app.post("/api/units")
async def set_units(payload: dict):
    """단위 수동 지정 — 두 방식 중 하나.

    {"unit": "mm"|"cm"|"m"|"inch"|"ft"}          단위 직접 선택
    {"ref_mm": <도면단위 거리>, "real_m": <실제 m>}  도면 2점 실측 입력

    $INSUNITS 가 없는 도면(=0)에서 쓴다. 지정 전에는 저장·적용을 막는다.
    """
    _require_session()
    named = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "inch": 25.4, "ft": 304.8}
    if "unit" in payload:
        u = str(payload["unit"])
        if u not in named:
            raise HTTPException(422, f"unit은 {'|'.join(named)} — 받은 값: {u!r}")
        mm, src, name = named[u], "user", u
    elif "ref_mm" in payload and "real_m" in payload:
        ref = float(payload["ref_mm"])          # 도면단위로 잰 두 점 사이 거리
        real = float(payload["real_m"])         # 그 구간의 실제 거리(m)
        if ref <= 0 or real <= 0:
            raise HTTPException(422, "ref_mm·real_m 은 0보다 커야 합니다")
        mm, src, name = real * 1000.0 / ref, "measure", "실측 2점"
    else:
        raise HTTPException(422, "unit 또는 (ref_mm, real_m) 이 필요합니다")
    with _lock:
        S["unit_mm"], S["unit_source"], S["unit_name"] = mm, src, name
    return _units_info()


def _norm_shape(sh: dict) -> dict:
    """편집 도형 검증·정규화. 잘못된 값은 422로 막는다."""
    op = str(sh.get("op", "open"))
    kind = str(sh.get("kind", "line"))
    if op not in ("open", "block"):
        raise HTTPException(422, f"op은 open|block — 받은 값: {op!r}")
    if kind not in ("line", "rect", "poly"):
        raise HTTPException(422, f"kind는 line|rect|poly — 받은 값: {kind!r}")
    pts = [float(v) for v in sh.get("pts", [])]
    need = 6 if kind == "poly" else 4
    if len(pts) < need or len(pts) % 2:
        raise HTTPException(422, f"{kind}는 좌표 {need}개 이상(짝수) 필요")
    out = {"op": op, "kind": kind, "pts": pts}
    if kind == "line":
        out["w"] = max(1.0, float(sh.get("w", OPENING_W)))
    return out


def _require_session():
    if "entities" not in S:
        raise HTTPException(409, "도면이 로드되지 않음 — 먼저 업로드하세요.")


# ───────────────────────────────────────────── 페이지
@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(HERE, "static", "editor.html"), encoding="utf-8") as f:
        return f.read()


# ───────────────────────────────────────────── 업로드/로드
def _dwg_to_dxf(src, out_dir):
    """DWG→DXF. ODAFileConverter(정합 최상) 우선, 없으면 libredwg(dwg2dxf, OSS)로
    폴백 — tools/cad_convert.py 와 동일한 엔진 선택. 둘 다 없으면 설치 안내(500).

    (순정 서버 재현: ODA는 EULA로 자동설치 불가하지만 libredwg는
     tools/setup_cad_convert.sh 로 자동 빌드되므로, 편집기가 ODA를 강제하면
     ODA 미설치 서버에서 DWG 업로드가 막힌다 — 폴백으로 그 갭을 없앤다.)"""
    base = os.path.splitext(os.path.basename(src))[0]
    out = os.path.join(out_dir, base + ".dxf")
    if shutil.which("ODAFileConverter"):
        with tempfile.TemporaryDirectory() as tin:
            shutil.copy(src, tin)
            cmd = ["xvfb-run", "-a", "ODAFileConverter", tin, out_dir,
                   "ACAD2018", "DXF", "0", "1", "*.dwg"]
            subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if os.path.exists(out):
            return out
        # ODA가 있었으나 실패 → libredwg로 재시도(있으면)
    if shutil.which("dwg2dxf"):
        subprocess.run(["dwg2dxf", "-o", out, src],
                       capture_output=True, text=True, timeout=300)
        if os.path.exists(out):
            return out
        raise HTTPException(422, "DWG→DXF 변환 실패(libredwg)")
    if not shutil.which("ODAFileConverter"):
        raise HTTPException(500, "DWG 변환기 없음(ODAFileConverter/dwg2dxf) — "
                                 "tools/setup_cad_convert.sh 로 설치")
    raise HTTPException(422, "DWG→DXF 변환 실패")


@app.post("/api/load")
async def load(file: UploadFile = File(...)):
    name = file.filename or "upload"
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".dxf", ".dwg"):
        raise HTTPException(422, "DWG 또는 DXF만 지원 (PDF는 AutoCAD PDF Import 후 DWG로)")
    workdir = tempfile.mkdtemp(prefix="evac_editor_")
    src = os.path.join(workdir, os.path.basename(name))
    with open(src, "wb") as f:
        f.write(await file.read())
    dxf_path = _dwg_to_dxf(src, workdir) if ext == ".dwg" else src

    entities, exits, occupants, bounds, _doc = cad.load_dxf_entities(dxf_path)
    # Evac_Occupant 레이어(폴리라인 꼭짓점 등) → occupant 모드 출발점 초기값.
    # 원본 매크로의 occupant 명령과 같은 입력이다.
    # 도면 단위($INSUNITS) — 없으면 unit_mm=None 으로 두고 사용자에게 묻는다.
    # 임의로 mm 로 가정하면 m 단위 도면에서 축척이 1000배 틀어진 채 조용히 넘어간다.
    ins_code, unit_mm, unit_name = cad.read_units(_doc)
    with _lock:
        S.clear()
        S.update(entities=entities, bounds=list(bounds), src_name=name,
                 dxf_path=dxf_path, deleted=[], openings=[], shapes=[],
                 insunits=ins_code, unit_mm=unit_mm, unit_name=unit_name,
                 unit_source="dxf" if unit_mm else None,
                 starts=[[float(x), float(y)] for (x, y) in occupants],
                 # 도면에 이미 Evac_Exit 가 있으면 그 중점을 초기 Exit(짧은 선)로
                 exits=[(x - 450, y, x + 450, y) for (x, y) in exits])
    n_seg = sum(len(e["segs"]) for e in entities)
    return {"name": name, "bounds": bounds, "entities": len(entities),
            "segments": n_seg, "exits": S["exits"],
            "units": _units_info(),
            "size_m": _size_m(bounds, unit_mm)}


@app.get("/api/session")
def session_state():
    """세션 존재 여부·요약 — 페이지 새로고침 복원용(가벼운 조회).

    편집기 세션은 서버 메모리에만 있어(S), 브라우저를 새로 열면 화면이 비어
    보인다. 프론트가 이 응답으로 복원 여부를 판단한다."""
    if "entities" not in S:
        return {"loaded": False}
    e = _edits()
    return {"loaded": True,
            "name": S.get("src_name", ""),
            "entities": len(S.get("entities", [])),
            "bounds": S.get("bounds"),
            "deleted": len(e["deleted"]), "openings": len(e["openings"]),
            "shapes": len(e["shapes"]), "exits": len(e["exits"]),
            "starts": len(e["starts"])}


@app.get("/api/geometry")
def geometry():
    """세그먼트 바이너리(Float32 x1,y1,x2,y2 …) + 엔티티 인덱스 헤더.
    앞 4바이트 = 헤더(JSON) 길이. 클라이언트가 직접 캔버스에 그린다."""
    _require_session()
    ents = S["entities"]
    index = []
    off = 0
    for e in ents:
        n = len(e["segs"])
        index.append({"h": e["handle"], "layer": e["layer"], "o": off, "n": n})
        off += n
    buf = np.empty((off, 4), np.float32)
    pos = 0
    for e in ents:
        n = len(e["segs"])
        buf[pos:pos + n] = e["segs"]
        pos += n
    head = json.dumps({"bounds": S["bounds"], "index": index,
                       "deleted": S["deleted"], "openings": S["openings"],
                       # shapes(뚫기·차단)·starts(출발점)도 함께 — 없으면 프론트가
                       # 새로고침·업로드 직후 이 편집 상태를 복원하지 못한다.
                       "shapes": S.get("shapes", []),
                       "starts": S.get("starts", []),
                       "exits": S["exits"], "name": S["src_name"]}).encode()
    payload = len(head).to_bytes(4, "little") + head + buf.tobytes()
    return Response(payload, media_type="application/octet-stream")


# ───────────────────────────────────────────── 편집 상태 갱신
@app.post("/api/edits")
async def set_edits(payload: dict):
    """클라이언트 편집 상태 전체 동기화(단순·무충돌)."""
    _require_session()
    with _lock:
        S["deleted"] = [str(h) for h in payload.get("deleted", [])]
        S["openings"] = [list(map(float, o)) for o in payload.get("openings", [])]
        S["exits"] = [list(map(float, e)) for e in payload.get("exits", [])]
        S["shapes"] = [_norm_shape(sh) for sh in payload.get("shapes", [])]
        S["starts"] = [[float(p[0]), float(p[1])]
                       for p in payload.get("starts", []) if len(p) >= 2]
    return _edits()


# ───────────────────────────────────────────── 연결성 프리뷰
@app.get("/api/connectivity.png")
def connectivity_png():
    """보행공간 오버레이 PNG(투명배경): 초록=최대연결영역, 주황=고립조각."""
    _require_session()
    obs = _obstacles()
    bounds = S["bounds"]
    conn = core.connectivity(obs, bounds, cell=PREVIEW_CELL,
                             shapes=_shapes() or None)
    lab, main = conn["labels"], conn["main"]
    img = np.zeros((*lab.shape, 4), np.uint8)          # (cols, rows, RGBA)
    img[lab > 0] = (255, 170, 60, 110)                 # 고립: 주황
    img[lab == main] = (60, 200, 90, 90)               # 연결: 초록
    # (cols,rows) → 이미지(y down): 転置 후 상하반전
    img = np.flipud(img.transpose(1, 0, 2))
    import cv2
    ok, enc = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGBA2BGRA))
    if not ok:
        raise HTTPException(500, "PNG 인코딩 실패")
    return Response(enc.tobytes(), media_type="image/png",
                    headers={"X-Components": str(conn["n_components"]),
                             "X-Largest-Frac": f"{conn['largest_frac']:.3f}"})


# ───────────────────────────────────────────── 거리 열지도(전체 거리장)
@app.get("/api/distfield.png")
def distfield_png():
    """모든 지점 → 최근접 Exit 거리(멀티소스 다익스트라 거리장)를 열지도 PNG로.
    이것이 '전체 최적경로'의 총체 — 어느 위치든 이 장(field)의 내리막을 따라가면
    그 지점의 최적 피난경로가 된다. worst-N 은 이 장에서 가장 먼 지점을 뽑는 것."""
    _require_session()
    exits = _exit_pts()
    if not exits:
        raise HTTPException(422, "Exit가 없습니다 — Exit 모드로 선을 그어주세요.")
    obs = _obstacles()
    bounds = S["bounds"]
    try:
        an = core.analyze(obs, exits, bounds, mode="worstn", worst_n=1,
                          cell=PREVIEW_CELL, shapes=_shapes() or None)
    except ValueError as e:
        raise HTTPException(422, str(e))
    dist = an.dist
    finite = np.isfinite(dist)
    dmax = float(dist[finite].max()) if finite.any() else 1.0
    # 컬러맵: 가까움(초록) → 멀어짐(노랑→빨강), 도달불가 free=회색, 벽=투명
    img = np.zeros((*dist.shape, 4), np.uint8)
    t = np.clip(dist / max(dmax, 1e-6), 0, 1)
    r = np.where(t < 0.5, (t * 2) * 255, 255)
    g = np.where(t < 0.5, 255, (2 - 2 * t) * 255)
    img[..., 0] = r.astype(np.uint8)
    img[..., 1] = g.astype(np.uint8)
    img[..., 3] = np.where(finite, 130, 0).astype(np.uint8)
    unreachable = (~an.grid) & (~finite)
    img[unreachable] = (120, 120, 120, 90)
    img = np.flipud(img.transpose(1, 0, 2))
    import cv2
    ok, enc = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGBA2BGRA))
    if not ok:
        raise HTTPException(500, "PNG 인코딩 실패")
    return Response(enc.tobytes(), media_type="image/png",
                    headers={"X-Max-Dist-M": f"{_to_m(dmax):.1f}"})


# ───────────────────────────────────────────── 경로 검증
@app.post("/api/verify")
async def verify(payload: dict = None):
    """worstn 자동검증(기본 5). exits 필요. 경로 폴리라인(mm) 반환."""
    _require_session()
    exits = _exit_pts()
    if not exits:
        raise HTTPException(422, "Exit가 없습니다 — Exit 모드로 선을 그어주세요.")
    payload = payload or {}
    n = max(1, int(payload.get("worst_n") or 5))          # 0/빈값 → 경로 0개 방지
    thr = _from_m(float(payload.get("threshold_m") or 30.0))
    # 모드 — worstn(자동으로 가장 먼 N곳) | occupant(사용자가 찍은 출발점)
    # 두 모드는 거리장을 공유하고 "어디서 역추적할지"만 다르다.
    mode = str(payload.get("mode") or "worstn")
    if mode not in ("worstn", "occupant"):
        raise HTTPException(422, f"mode는 worstn|occupant — 받은 값: {mode!r}")
    starts = S.get("starts") or []
    if mode == "occupant" and not starts:
        raise HTTPException(422, "출발점이 없습니다 — 🧍 출발점 모드로 찍거나 "
                                 "DXF에 Evac_Occupant 레이어를 넣으세요.")
    try:
        an = core.analyze(_obstacles(), exits, S["bounds"], mode=mode,
                          starts=[tuple(p) for p in starts] if mode == "occupant" else None,
                          worst_n=n, cell=FULL_CELL, threshold_mm=thr,
                          shapes=_shapes() or None)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"paths": [{"pts": p["path_m"], "dist_m": round(_to_m(p["dist_mm"]), 1),
                       "pass": p["is_pass"]} for p in an.paths],
            "skipped": an.skipped, "threshold_m": round(_to_m(thr), 1),
            "mode": mode, "starts": len(starts),
            "dropped": [{"xy": list(d["start_m"]), "reason": d["reason"]}
                        for d in getattr(an, "dropped", [])]}


# ───────────────────────────────────────────── 저장 & 적용
def _render_map_png(obs, bounds, out_path, px_w=2000):
    """터치업 반영된 깨끗한 도면 PNG(축 없음, 흰 배경) — 2D맵 배경용."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    minx, miny, maxx, maxy = bounds
    Wm, Hm = (maxx - minx), (maxy - miny)
    dpi = 100
    figw = px_w / dpi
    fig = plt.figure(figsize=(figw, figw * Hm / Wm))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.add_collection(LineCollection(
        [[(s[0], s[1]), (s[2], s[3])] for s in obs], colors="#222", linewidths=0.3))
    ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
    ax.set_aspect("equal"); ax.axis("off")
    fig.savefig(out_path, dpi=dpi, facecolor="white")
    plt.close(fig)
    import cv2
    im = cv2.imread(out_path)
    return im.shape[1], im.shape[0]      # (w, h) px


@app.post("/api/apply")
async def apply(payload: dict = None):
    """map.png + floor.json + distfield.npz 저장 → :8900 반영(가능하면)."""
    _require_session()
    payload = payload or {}
    site = str(payload.get("site", "default"))
    if not site.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(422, "site 이름은 영숫자/-/_ 만")
    # 층(floor) — 다중 도면 지원(:8900 v1.7). default=map.png, 그외=map_<floor>.png
    # (system/config/store.map_path 규칙과 정합). 파일명은 floor.json도 층별로.
    floor = str(payload.get("floor", "default")) or "default"
    if not floor.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(422, "floor 이름은 영숫자/-/_ 만")
    map_name = "map.png" if floor == "default" else f"map_{floor}.png"
    floor_name = "floor.json" if floor == "default" else f"floor_{floor}.json"
    exits = _exit_pts()
    if not exits:
        raise HTTPException(422, "Exit가 없습니다 — 저장 전에 Exit를 지정하세요.")
    # 단위 미확정 상태로 저장하면 축척(m_per_px)이 틀린 채 :8900 까지 전파되고,
    # 속도·밀도가 조용히 어긋난다. 여기서 막는다.
    if not _unit_mm():
        raise HTTPException(
            422, "도면 단위가 확인되지 않았습니다($INSUNITS 없음) — "
                 "단위를 선택하거나 실측 2점으로 축척을 지정한 뒤 저장하세요.")

    obs = _obstacles()
    bounds = S["bounds"]
    site_dir = os.path.join(SITE_ROOT, site)
    os.makedirs(site_dir, exist_ok=True)

    # 1) map.png (터치업 도면) — 층별 파일명
    map_path = os.path.join(site_dir, map_name)
    w_px, h_px = _render_map_png(obs, bounds, map_path)
    m_per_px = _to_m(bounds[2] - bounds[0]) / w_px   # 도면 실단위 반영

    # 2) 거리장 사전계산 (트래킹 좌표 → 실시간 피난거리용)
    #    worst_n을 반영용으로 넉넉히(기본 5) 뽑는다 — dist/grid 는 worst_n과
    #    무관(멀티소스 다익스트라 거리장)하므로 distfield.npz 는 그대로 유효하고,
    #    an.paths 만 EPFI 기준경로(routes) 반영에 재사용한다.
    route_n = int(payload.get("route_n", 5))
    an = core.analyze(obs, exits, bounds, mode="worstn", worst_n=route_n,
                      cell=FULL_CELL, shapes=_shapes() or None)
    np.savez_compressed(os.path.join(site_dir, "evac_distfield.npz"),
                        dist=an.dist.astype(np.float32), grid=an.grid,
                        bounds=np.array(bounds), cell=FULL_CELL)

    # 3) floor.json (비파괴 편집내역 + 좌표계) — 층별 파일명(floor_name)
    floor_meta = {"source": S["src_name"], "bounds_mm": bounds,
                  "m_per_px": m_per_px, "map_px": [w_px, h_px],
                  "exits_mm": S["exits"], "openings_mm": S["openings"],
                  "opening_width_mm": OPENING_W,
                  "deleted_handles": S["deleted"],
                  "cell_mm": FULL_CELL, "clearance_mm": core.CLEARANCE}
    with open(os.path.join(site_dir, floor_name), "w") as f:
        json.dump(floor_meta, f, ensure_ascii=False, indent=2)

    # 3.5) 최단경로(worst-N) → 맵 원본 px polyline 으로 변환 (EPFI 기준경로 반영용).
    #      _render_map_png 는 xlim=[minx,maxx]·ylim=[miny,maxy] 를 축[0,0,1,1]에
    #      꽉 채우므로 도면 mm → 맵 px 는 선형(이미지 좌표계라 y축 뒤집힘):
    #        px_x = (wx-minx)/(maxx-minx)*w_px,  px_y = (maxy-wy)/(maxy-miny)*h_px
    minx, miny, maxx, maxy = bounds
    def _to_px(wx, wy):
        return [round((wx - minx) / (maxx - minx) * w_px, 1),
                round((maxy - wy) / (maxy - miny) * h_px, 1)]
    routes_px = []
    for i, p in enumerate(an.paths):
        pm = p["path_m"]
        # per-cell 폴리라인은 과밀 — 형태 보존하며 최대 ~60점으로 데시메이트
        # (첫·끝점 항상 유지). 맵 canvas 표출·계약 min_length(2) 충족.
        step = max(1, len(pm) // 60)
        thin = pm[::step]
        if thin[-1] != pm[-1]:
            thin = thin + [pm[-1]]
        pts = [_to_px(x, y) for (x, y) in thin]
        if len(pts) >= 2:
            routes_px.append({"id": f"auto-evac-{i}",
                              "name": f"자동피난경로{i + 1}", "points": pts})

    # 3.6) Exit 선분(mm) → ExitLine(맵 px) 변환 — CAD 기준 출입구 통과선 반영용.
    #      inside(안쪽 반평면 지정점) 추론: 편집기 Exit엔 방향정보가 없으므로,
    #      Exit 중점에서 도면 bounds 중심 쪽으로 오프셋한 점을 inside로 둔다.
    #      근거: 대형 평면도에서 출구는 보통 외곽, 건물 안쪽은 도면 중심 방향.
    #      [한계] 안뜰·중정형 평면, 중심이 통과선상에 놓이는 배치에서는 방향이
    #      틀릴 수 있음 — 그런 경우 맵설정에서 inside를 수동 보정해야 한다.
    cx_mm, cy_mm = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    diag_mm = math.hypot(maxx - minx, maxy - miny)
    exits_px = []
    for i, (x1, y1, x2, y2) in enumerate(S["exits"]):
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        dx, dy = cx_mm - mx, cy_mm - my
        norm = math.hypot(dx, dy)
        if norm < 1e-6:  # 중점이 도면 중심과 일치(희귀) → 선분 수직방향으로 대체
            dx, dy = -(y2 - y1), (x2 - x1)
            norm = math.hypot(dx, dy) or 1.0
        off = max(1500.0, 0.05 * diag_mm)   # 안쪽으로 1.5m 또는 대각선 5% 이동
        ix_mm, iy_mm = mx + dx / norm * off, my + dy / norm * off
        exits_px.append({"id": f"exit-{i}", "name": f"출구{i + 1}",
                         "line": [_to_px(x1, y1), _to_px(x2, y2)],
                         "inside": _to_px(ix_mm, iy_mm),
                         "design_capacity": None})

    saved_names = (map_name, floor_name, "evac_distfield.npz")
    # 4) 운영 서버(:8900) 반영 — 명시적 opt-in 일 때만. 해당 층(floor)에만 반영.
    applied = False
    elements_applied = False
    if not bool(payload.get("apply_live", False)):
        return {"site": site, "floor": floor, "map_px": [w_px, h_px],
                "m_per_px": round(m_per_px, 5),
                "exits": len(exits_px), "openings": len(S["openings"]),
                "deleted": len(S["deleted"]), "routes": len(routes_px),
                "worst_dist_m": round(_to_m(max((p["dist_mm"] for p in an.paths), default=0)), 1),
                "applied_to_system": False, "routes_applied": False,
                "elements_applied": False,
                "saved": [os.path.join("data/sites", site, n) for n in saved_names]}
    import urllib.request
    from urllib.parse import urlencode
    try:
        boundary = "evacfloor"
        with open(map_path, "rb") as f:
            img = f.read()
        meta = json.dumps({"m_per_px": m_per_px})
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"meta\"\r\n\r\n"
                f"{meta}\r\n--{boundary}\r\nContent-Disposition: form-data; "
                f"name=\"image\"; filename=\"map.png\"\r\nContent-Type: image/png\r\n\r\n"
                ).encode() + img + f"\r\n--{boundary}--\r\n".encode()
        # ?floor= 로 해당 층에만 반영 (:8900 v1.7). default면 기존과 동일.
        url = f"{SYSTEM_API}/api/site/map?" + urlencode({"floor": floor})
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            applied = (r.status == 200)
    except Exception:
        applied = False

    # 4.5) 맵 반영 성공 시에만 그 층의 공간요소를 CAD 기준으로 재세팅.
    #      PUT /api/site/floor-elements?floor= (:8900 v1.9): 피난경로·출입구는
    #      CAD 산출값으로 새로 세팅, 구역·병목은 비운다(옛 맵 px 좌표가 새 맵과
    #      안 맞으므로 새 맵 위에 다시 그리게 함). 맵을 방금 올렸으므로 :8900
    #      그 층 map.w/h == 여기 w_px/h_px 로 좌표 정합.
    if applied:
        try:
            # 맵이 통째로 바뀌므로 경로도 전체 교체한다("all").
            # "auto"면 수동으로 그린 경로(r1·r2 등)가 남는데, 그 좌표는 옛 맵
            # px 기준이라 새 맵에서 엉뚱한 위치를 가리킨다 — 구역·병목을 비우는
            # 것(clear_zones/clear_bottlenecks)과 같은 이유다.
            ebody = json.dumps({"routes": routes_px, "replace": "all",
                                "exits": exits_px,
                                "clear_zones": True,
                                "clear_bottlenecks": True}).encode()
            eurl = f"{SYSTEM_API}/api/site/floor-elements?" + urlencode({"floor": floor})
            req2 = urllib.request.Request(
                eurl, data=ebody, method="PUT",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req2, timeout=5) as r2:
                elements_applied = (r2.status == 200)
        except Exception:
            elements_applied = False

    return {"site": site, "floor": floor, "map_px": [w_px, h_px],
            "m_per_px": round(m_per_px, 5),
            "exits": len(exits_px), "openings": len(S["openings"]),
            "deleted": len(S["deleted"]), "routes": len(routes_px),
            "worst_dist_m": round(_to_m(max((p["dist_mm"] for p in an.paths), default=0)), 1),
            "applied_to_system": applied, "routes_applied": elements_applied,
            "elements_applied": elements_applied,
            "saved": [os.path.join("data/sites", site, n) for n in saved_names]}


# ───────────────────────────────────────────── 기존 floor.json 재개(선택)
@app.get("/api/floor/{site}")
def get_floor(site: str):
    p = os.path.join(SITE_ROOT, site, "floor.json")
    if not os.path.isfile(p):
        raise HTTPException(404, "floor.json 없음")
    return FileResponse(p, media_type="application/json")


# ───────────────────────────────────────────── 층 목록 — 운영서버(:8900) 프록시
# 편집기는 "어느 층을 교체할지"를 사용자가 고르게 한다. 층 구성의 원본은
# :8900 이므로 여기서 만들지 않고 그대로 중계한다(편집기는 사본을 갖지 않음).
def _system_json(path: str, *, data: bytes | None = None, method: str = "GET"):
    import urllib.request
    req = urllib.request.Request(
        SYSTEM_API + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


@app.get("/api/system/floors")
def system_floors():
    """운영서버의 층 목록. 서버가 꺼져 있으면 목록 대신 사유를 돌려준다
    (편집기 자체는 오프라인에서도 도면 편집이 가능해야 하므로 500 금지)."""
    try:
        return {"ok": True, "floors": _system_json("/api/floors")}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "floors": []}


@app.post("/api/system/floors")
async def system_add_floor(payload: dict = None):
    """층 추가 — body {id?, name?}. 실패 사유는 그대로 올려보낸다."""
    body = payload or {}
    try:
        return {"ok": True,
                "floor": _system_json("/api/floors",
                                      data=json.dumps(body).encode(),
                                      method="POST")}
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        if hasattr(e, "read"):          # HTTPError — :8900 의 사유를 그대로
            try:
                detail = json.loads(e.read()).get("detail", detail)
            except Exception:
                pass
        raise HTTPException(502, detail)
