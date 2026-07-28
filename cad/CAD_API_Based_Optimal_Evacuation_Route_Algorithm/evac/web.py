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
            "exits": S.get("exits", [])}


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
    """ODAFileConverter(xvfb 헤드리스)로 DWG→DXF. tools/cad_convert.py 와 동일 방식."""
    if not shutil.which("ODAFileConverter"):
        raise HTTPException(500, "ODAFileConverter 없음 — tools/setup_cad_convert.sh 참조")
    with tempfile.TemporaryDirectory() as tin:
        shutil.copy(src, tin)
        cmd = ["xvfb-run", "-a", "ODAFileConverter", tin, out_dir,
               "ACAD2018", "DXF", "0", "1", "*.dwg"]
        subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    base = os.path.splitext(os.path.basename(src))[0]
    out = os.path.join(out_dir, base + ".dxf")
    if not os.path.exists(out):
        raise HTTPException(422, "DWG→DXF 변환 실패")
    return out


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
    with _lock:
        S.clear()
        S.update(entities=entities, bounds=list(bounds), src_name=name,
                 dxf_path=dxf_path, deleted=[], openings=[],
                 # 도면에 이미 Evac_Exit 가 있으면 그 중점을 초기 Exit(짧은 선)로
                 exits=[(x - 450, y, x + 450, y) for (x, y) in exits])
    n_seg = sum(len(e["segs"]) for e in entities)
    return {"name": name, "bounds": bounds, "entities": len(entities),
            "segments": n_seg, "exits": S["exits"],
            "size_m": [round((bounds[2] - bounds[0]) / 1000, 1),
                       round((bounds[3] - bounds[1]) / 1000, 1)]}


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
    return _edits()


# ───────────────────────────────────────────── 연결성 프리뷰
@app.get("/api/connectivity.png")
def connectivity_png():
    """보행공간 오버레이 PNG(투명배경): 초록=최대연결영역, 주황=고립조각."""
    _require_session()
    obs = _obstacles()
    bounds = S["bounds"]
    conn = core.connectivity(obs, bounds, cell=PREVIEW_CELL,
                             openings=S.get("openings") or None,
                             opening_width=OPENING_W)
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
                          cell=PREVIEW_CELL, openings=S.get("openings") or None,
                          opening_width=OPENING_W)
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
                    headers={"X-Max-Dist-M": f"{dmax/1000:.1f}"})


# ───────────────────────────────────────────── 경로 검증
@app.post("/api/verify")
async def verify(payload: dict = None):
    """worstn 자동검증(기본 5). exits 필요. 경로 폴리라인(mm) 반환."""
    _require_session()
    exits = _exit_pts()
    if not exits:
        raise HTTPException(422, "Exit가 없습니다 — Exit 모드로 선을 그어주세요.")
    payload = payload or {}
    n = int(payload.get("worst_n", 5))
    thr = float(payload.get("threshold_m", 30.0)) * 1000.0
    try:
        an = core.analyze(_obstacles(), exits, S["bounds"], mode="worstn",
                          worst_n=n, cell=FULL_CELL, threshold_mm=thr,
                          openings=S.get("openings") or None,
                          opening_width=OPENING_W)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"paths": [{"pts": p["path_m"], "dist_m": round(p["dist_mm"] / 1000, 1),
                       "pass": p["is_pass"]} for p in an.paths],
            "skipped": an.skipped, "threshold_m": thr / 1000}


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

    obs = _obstacles()
    bounds = S["bounds"]
    site_dir = os.path.join(SITE_ROOT, site)
    os.makedirs(site_dir, exist_ok=True)

    # 1) map.png (터치업 도면) — 층별 파일명
    map_path = os.path.join(site_dir, map_name)
    w_px, h_px = _render_map_png(obs, bounds, map_path)
    m_per_px = (bounds[2] - bounds[0]) / 1000.0 / w_px

    # 2) 거리장 사전계산 (트래킹 좌표 → 실시간 피난거리용)
    an = core.analyze(obs, exits, bounds, mode="worstn", worst_n=1,
                      cell=FULL_CELL, openings=S.get("openings") or None,
                      opening_width=OPENING_W)
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

    saved_names = (map_name, floor_name, "evac_distfield.npz")
    # 4) 운영 서버(:8900) 반영 — 명시적 opt-in 일 때만. 해당 층(floor)에만 반영.
    applied = False
    if not bool(payload.get("apply_live", False)):
        return {"site": site, "floor": floor, "map_px": [w_px, h_px],
                "m_per_px": round(m_per_px, 5),
                "exits": len(exits), "openings": len(S["openings"]),
                "deleted": len(S["deleted"]),
                "worst_dist_m": round(max((p["dist_mm"] for p in an.paths), default=0) / 1000, 1),
                "applied_to_system": False,
                "saved": [os.path.join("data/sites", site, n) for n in saved_names]}
    try:
        import urllib.request
        boundary = "evacfloor"
        with open(map_path, "rb") as f:
            img = f.read()
        meta = json.dumps({"m_per_px": m_per_px})
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"meta\"\r\n\r\n"
                f"{meta}\r\n--{boundary}\r\nContent-Disposition: form-data; "
                f"name=\"image\"; filename=\"map.png\"\r\nContent-Type: image/png\r\n\r\n"
                ).encode() + img + f"\r\n--{boundary}--\r\n".encode()
        # ?floor= 로 해당 층에만 반영 (:8900 v1.7). default면 기존과 동일.
        from urllib.parse import urlencode
        url = f"{SYSTEM_API}/api/site/map?" + urlencode({"floor": floor})
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            applied = (r.status == 200)
    except Exception:
        applied = False

    return {"site": site, "floor": floor, "map_px": [w_px, h_px],
            "m_per_px": round(m_per_px, 5),
            "exits": len(exits), "openings": len(S["openings"]),
            "deleted": len(S["deleted"]),
            "worst_dist_m": round(max((p["dist_mm"] for p in an.paths), default=0) / 1000, 1),
            "applied_to_system": applied,
            "saved": [os.path.join("data/sites", site, n) for n in saved_names]}


# ───────────────────────────────────────────── 기존 floor.json 재개(선택)
@app.get("/api/floor/{site}")
def get_floor(site: str):
    p = os.path.join(SITE_ROOT, site, "floor.json")
    if not os.path.isfile(p):
        raise HTTPException(404, "floor.json 없음")
    return FileResponse(p, media_type="application/json")
