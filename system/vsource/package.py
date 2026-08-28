"""리허설 패키지 — 폴더 하나가 진실의 원천 (ADR 09).

`media/vsource/<site>/<set>/rehearsal.json` 이 영상·시나리오·카메라·매핑·도면을
전부 정의한다. 이 모듈은 그 매니페스트를 읽어

- 시나리오를 vsource 가 아는 형태(Stream 정의)로 펼치고     → scenario.py 가 사용
- 카메라를 **가상 CameraConfig**(cam_id `rh_*`)로 만들고     → RT.cameras() 가 얹음
- 층을 **가상 Floor**(id `rh_*`)로 만들고                    → RT.reload_engine() 이 얹음
- UI 매핑 결과를 사이트가 아니라 **매니페스트에** 저장한다   → PUT mapping 분기

사이트(`data/sites/`)는 한 글자도 건드리지 않는다 — 리허설을 끄면 얹기를
멈추는 것으로 복원이 끝난다 (overlay.py 와 같은 원칙).

시나리오 id 네임스페이스: ``pkg:<패키지id>:<시나리오id>``
(예 ``pkg:cj-rehearsal:scenario_01``) — 기존 data/scenarios/ id 와 충돌하지 않는다.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from system.config.schema import CameraConfig, Floor, MapSpec

logger = logging.getLogger("system.vsource")

PKG_ROOT = Path("media/vsource")
MANIFEST = "rehearsal.json"
SCENARIO_PREFIX = "pkg:"
CAM_PREFIX = "rh_"          # 가상 카메라 cam_id 접두 — 사이트 cam01.. 과 절대 안 겹침
FLOOR_PREFIX = "rh_"        # 가상 층 id 접두 — 사이트 floor2.. 와 절대 안 겹침
RTSP_HOST_DEFAULT = "127.0.0.1:8554"


# ------------------------------------------------------------------ 로드
_cache: dict[str, tuple[float, dict]] = {}   # pkg_id → (mtime, manifest)


def _read(f: Path) -> dict | None:
    try:
        mt = f.stat().st_mtime
    except OSError:
        return None
    pid_key = str(f)
    hit = _cache.get(pid_key)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("[vsource] 패키지 매니페스트 읽기 실패: %s", f)
        return None
    d["_root"] = str(f.parent)
    _cache[pid_key] = (mt, d)
    return d


def discover() -> list[dict]:
    """모든 패키지 매니페스트 (id 순). `media/vsource/*/*/rehearsal.json` 스캔."""
    out = []
    if PKG_ROOT.is_dir():
        for f in sorted(PKG_ROOT.glob(f"*/*/{MANIFEST}")):
            d = _read(f)
            if d and d.get("id"):
                out.append(d)
    return out


def get(pkg_id: str) -> dict | None:
    for d in discover():
        if d.get("id") == pkg_id:
            return d
    return None


def parse_scenario_id(sid: str) -> tuple[str, str] | None:
    """'pkg:<pkg>:<scen>' → (pkg, scen). 패키지 시나리오가 아니면 None."""
    if not sid.startswith(SCENARIO_PREFIX):
        return None
    rest = sid[len(SCENARIO_PREFIX):]
    pkg, _, scen = rest.partition(":")
    return (pkg, scen) if pkg and scen else None


def source(pkg: dict) -> str:
    """입력 경로 — "file"(기본, ADR 09 §11: 영상을 직접 읽어 잠금 동기 추론) | "rtsp"(송출→재수신)."""
    return str(pkg.get("source") or "file").lower()


def stream_path(pkg: dict, cam: str) -> str:
    """RTSP 경로 — 시나리오가 달라도 같은 cam 은 같은 경로 (매핑 재사용의 핵심)."""
    return f"{pkg.get('rtsp_prefix') or pkg['id']}_{cam}"


def scenario_def(pkg: dict, scen_id: str) -> dict | None:
    """패키지 시나리오 1개를 data/scenarios/ JSON 과 같은 모양으로 펼친다."""
    root = Path(pkg["_root"])
    for s in pkg.get("scenarios", []):
        if s.get("id") == scen_id:
            return {
                "id": f"{SCENARIO_PREFIX}{pkg['id']}:{scen_id}",
                "name": f"{pkg.get('name', pkg['id'])} — {s.get('name', scen_id)}",
                "note": pkg.get("note", ""),
                "cycle_sec": s.get("cycle_sec") or 0,
                "streams": [{"path": stream_path(pkg, st["cam"]),
                             "file": str(root / st["file"])}
                            for st in s.get("streams", [])],
            }
    return None


def scenario_ids(pkg: dict) -> list[str]:
    return [f"{SCENARIO_PREFIX}{pkg['id']}:{s['id']}"
            for s in pkg.get("scenarios", []) if s.get("id")]


def scenario_cam_ids(pkg: dict, scen_id: str) -> set[str]:
    """해당 시나리오가 실제로 쓰는 카메라들의 **런타임 id**(rh_*).

    패키지 카메라 전부가 아니라 이 부분집합만 ingest 에 얹는다 — 시나리오에
    없는 카메라는 송출이 없어서 영원히 reconnecting 으로 DS 슬롯만 차지한다
    (16ch 한계를 리허설이 잠식하면 안 된다).
    """
    for s in pkg.get("scenarios", []):
        if s.get("id") == scen_id:
            return {cam_id_of(st["cam"]) for st in s.get("streams", []) if st.get("cam")}
    return set()


# ------------------------------------------------------------------ 가상 카메라·층
def cam_id_of(cam: str) -> str:
    return f"{CAM_PREFIX}{cam}"


def _own_floor_ids(pkg: dict) -> set[str]:
    """패키지가 **도면까지 직접 정의**한 층(자립 모드) — 이것만 가상 층(rh_*)이 된다."""
    return {f["id"] for f in pkg.get("floors", []) if f.get("id") and f.get("image")}


def floor_id_of(fid: str | None, pkg: dict | None = None) -> str | None:
    """매니페스트 층 id → 런타임 층 id.

    두 모드 (ADR 09 §7):
    - **빙의(기본)** — `floor` 가 사이트 층 id(`floor10` 등)를 가리킨다. 그대로 쓴다.
      도면·구역·출구·경로는 ① 맵설정 소관이고 리허설은 그 층을 빌려 쓴다 →
      나중에 CAD/구역을 고치면 리허설 지표에도 그대로 반영된다.
    - **자립** — 패키지 `floors[]` 에 도면(image)까지 정의된 층이면 가상 층 `rh_<id>`.
    """
    if not fid:
        return None
    if pkg is not None and fid in _own_floor_ids(pkg):
        return f"{FLOOR_PREFIX}{fid}"
    return fid


def virtual_cameras(pkg: dict, rtsp_host: str = RTSP_HOST_DEFAULT) -> list[CameraConfig]:
    """매니페스트 cameras[] → 가상 CameraConfig 목록.

    리허설이 도는 동안만 RT.cameras() 뒤에 얹힌다. 매핑도 매니페스트에서 온다 —
    UI가 새로 찍으면 save_mapping() 이 매니페스트에 되쓴다.
    """
    out = []
    for c in pkg.get("cameras", []):
        cam = c.get("cam")
        if not cam:
            continue
        try:
            out.append(CameraConfig(
                cam_id=cam_id_of(cam),
                name=c.get("name") or f"{pkg.get('name', pkg['id'])} {cam}",
                rtsp=f"rtsp://{rtsp_host}/{c.get('path') or stream_path(pkg, cam)}",
                enabled=True,
                analyze_fps=float(c.get("analyze_fps") or 5.0),
                floor_id=floor_id_of(c.get("floor"), pkg),
                mapping=c.get("mapping") or None,
                valid_roi=c.get("valid_roi") or None,
            ))
        except Exception:
            logger.exception("[vsource] 패키지 카메라 무시: %s/%s", pkg.get("id"), cam)
    return out


def virtual_floors(pkg: dict) -> list[Floor]:
    """매니페스트 floors[] → 가상 Floor 목록. 도면 이미지가 있으면 MapSpec 까지."""
    root = Path(pkg["_root"])
    out = []
    own = _own_floor_ids(pkg)
    for f in pkg.get("floors", []):
        fid = f.get("id")
        if not fid or fid not in own:        # 도면 없는 항목 = 사이트 층 빙의 선언 → 가상 층 아님
            continue
        spec = None
        img = f.get("image")
        if img and (root / img).is_file():
            try:
                import cv2                      # 지연 import — CLI/테스트에서 불필요
                arr = cv2.imread(str(root / img))
                if arr is not None:
                    spec = MapSpec(image=img, w=arr.shape[1], h=arr.shape[0],
                                   m_per_px=f.get("m_per_px"),
                                   source=f.get("source"))
            except Exception:
                logger.exception("[vsource] 패키지 도면 읽기 실패: %s", img)
        out.append(Floor(id=floor_id_of(fid, pkg),
                         name=(f.get("name") or fid) + " (리허설)", map=spec))
    return out


def floorplan_path(pkg: dict, runtime_floor_id: str) -> Path | None:
    """가상 층 id(rh_f10) → 패키지 안의 도면 이미지 파일."""
    fid = runtime_floor_id[len(FLOOR_PREFIX):] \
        if runtime_floor_id.startswith(FLOOR_PREFIX) else runtime_floor_id
    root = Path(pkg["_root"])
    for f in pkg.get("floors", []):
        if f.get("id") == fid and f.get("image"):
            p = root / f["image"]
            return p if p.is_file() else None
    return None


# ------------------------------------------------------------------ 매핑 저장 (P5)
def save_camera(pkg_id: str, cam_id: str, patch: dict) -> bool:
    """UI가 잡은 매핑을 **매니페스트에** 저장 (원자적 교체).

    cam_id 는 런타임 id(`rh_cam8`) — 매니페스트의 cam(`cam8`)으로 변환해 쓴다.
    patch: {mapping, valid_roi, floor} (floor 는 매니페스트 공간 id, 접두 없음).
    """
    pkg = get(pkg_id)
    if not pkg:
        return False
    cam = cam_id[len(CAM_PREFIX):] if cam_id.startswith(CAM_PREFIX) else cam_id
    f = Path(pkg["_root"]) / MANIFEST
    d = json.loads(f.read_text(encoding="utf-8"))     # 캐시 말고 원본에서
    hit = None
    for c in d.get("cameras", []):
        if c.get("cam") == cam:
            hit = c
            break
    if hit is None:
        return False
    for k in ("mapping", "valid_roi", "floor", "analyze_fps", "map_wh"):
        if k in patch:
            hit[k] = patch[k]
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(f)
    _cache.pop(str(f), None)
    logger.info("[vsource] 패키지 매핑 저장: %s / %s", pkg_id, cam)
    return True


# ------------------------------------------------------------------ 요약 (UI 목록)
def summary(pkg: dict) -> dict:
    """리허설 탭 목록용 요약 — 매핑 진행도·prep 판정 포함."""
    cams = pkg.get("cameras", [])
    prep = pkg.get("prep") or {}
    return {
        "id": pkg.get("id"), "name": pkg.get("name"),
        "root": pkg.get("_root"),
        # mode: "site" = ① 맵설정의 사이트 층을 빌려 씀(기본) / "own" = 패키지 자립 도면
        "floors": [{"id": floor_id_of(f.get("id"), pkg), "name": f.get("name"),
                    "has_map": bool(f.get("image")),
                    "mode": "own" if f.get("image") else "site"}
                   for f in pkg.get("floors", [])],
        "cameras_total": len(cams),
        "cameras_mapped": sum(1 for c in cams if c.get("mapping")),
        "scenario_ids": scenario_ids(pkg),
        "prep_ok": prep.get("ok"),
        "prep_checked_at": prep.get("checked_at"),
        "prep_fails": prep.get("fails") or [],
        "prep_warns": prep.get("warns") or [],
    }
