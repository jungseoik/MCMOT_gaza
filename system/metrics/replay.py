"""세션 리플레이·지표 재계산 — 녹화 db(<session_id>.db)를 헤드리스
MetricsEngine에 다시 흘려보내 (1) 2D 재생 프레임과 (2) 재산출 지표를 만든다.

설계: docs/architecture/05-세션-녹화-리플레이-지표재계산-설계.md

핵심: 엔진은 결정적이므로, 녹화 시점과 같은 공간요소(도면·구역·경로·출구)를
meta 스냅샷에서 복원하고 트랙을 call_seq 순서로 재생하면 원본과 동일 결과가
나온다. thresholds 오버라이드를 주면 그 임계값으로 4대 지표가 재산출된다
(도면·호모그래피는 그대로 — '역방향 재파라미터화').
"""
from __future__ import annotations

from system.config.schema import CameraConfig, SiteConfig
from system.metrics import recorder
from system.metrics.engine import MetricsEngine


def _apply_overrides(site: SiteConfig, ov: dict) -> None:
    """오버라이드를 복원된 site에 적용 (in-place). 미지정 필드는 스냅샷 유지."""
    th = ov.get("thresholds") or {}
    for k, v in th.items():
        if v is not None and hasattr(site.thresholds, k):
            setattr(site.thresholds, k, v)
    # 편의: 전역 rho_crit 하나로 모든 병목 임계밀도 일괄 조정
    g_rho = ov.get("rho_crit")
    if g_rho is not None:
        for b in site.bottlenecks:
            b.rho_crit = float(g_rho)
    # 병목별 세부 오버라이드
    for bid, patch in (ov.get("bottlenecks") or {}).items():
        b = next((b for b in site.bottlenecks if b.id == bid), None)
        if b is None:
            continue
        if patch.get("rho_crit") is not None:
            b.rho_crit = float(patch["rho_crit"])
        if patch.get("weight") is not None:
            b.weight = float(patch["weight"])
    # 출구별 오버라이드 — 유효폭·q_design 을 바꾸면 C_j를 다시 파생하고,
    # design_capacity를 직접 주면 그것이 최종값이다(파생보다 우선, v1.12).
    mpp = site.map.resolve_m_per_px() if site.map else None
    for eid, patch in (ov.get("exits") or {}).items():
        e = next((e for e in site.exits if e.id == eid), None)
        if e is None:
            continue
        if patch.get("width_m") is not None:
            e.width_m = float(patch["width_m"]) or None
        if patch.get("q_design") is not None:
            e.q_design = float(patch["q_design"]) or None
        if patch.get("width_m") is not None or patch.get("q_design") is not None:
            cap = e.resolve_capacity(mpp, site.thresholds.q_design)
            if cap is not None:
                e.design_capacity = cap
        if patch.get("design_capacity") is not None:
            e.design_capacity = int(patch["design_capacity"])
    # 전역 q_design 변경도 C_j에 반영해야 한다 — thresholds만 바꾸고 파생을
    # 안 돌리면 SEI가 옛 C_j로 계산돼 "재계산했는데 안 바뀐다"가 된다.
    if (ov.get("thresholds") or {}).get("q_design") is not None:
        for e in site.exits:
            if _capacity_overridden(ov, e.id):
                continue                      # 직접 지정한 C_j는 건드리지 않음
            cap = e.resolve_capacity(mpp, site.thresholds.q_design)
            if cap is not None:
                e.design_capacity = cap


def _capacity_overridden(ov: dict, eid: str) -> bool:
    """이 출구에 design_capacity 직접 오버라이드가 있었나."""
    p = (ov.get("exits") or {}).get(eid) or {}
    return p.get("design_capacity") is not None


def _lite_frame(ms) -> dict:
    """MapState → 재생용 경량 프레임 (좌표 반올림·필요 필드만)."""
    def r(v, n):
        return None if v is None else round(v, n)
    sess = ms.session
    return {
        "ts": round(ms.ts, 3),
        "objects": [{
            "gid": o.gid, "cam_id": o.cam_id,
            "x": round(o.x, 1), "y": round(o.y, 1),
            "vx": round(o.vx, 3), "vy": round(o.vy, 3),
            "speed_mps": r(o.speed_mps, 2), "align": r(o.align, 2),
            "zone_id": o.zone_id, "evac_ok": o.evac_ok, "exited": o.exited,
            "epfi_live": r(o.epfi_live, 0), "dev_m": r(o.dev_m, 2),
        } for o in ms.objects],
        "zones": [{"id": z.id, "count": z.count, "density": z.density}
                  for z in ms.zones],
        "bottlenecks": [{"id": b.id, "count": b.count, "density": b.density,
                         "over": b.over, "cbs": b.cbs} for b in ms.bottlenecks],
        "exits": [{"id": e.id, "in_count": e.in_count, "out_count": e.out_count}
                  for e in ms.exits],
        "sess": None if sess is None else {
            "sei": sess.sei, "cbs_total": round(sess.cbs_total, 2),
            "epfi_avg": sess.epfi_avg, "zones_started": sess.zones_started,
            "zones_total": sess.zones_total,
            "elapsed_sec": round(sess.elapsed_sec, 1),
        },
    }


def run_replay(db_path, overrides: dict | None = None, fps: float = 5.0):
    """녹화 db를 재생 → (result, timeline, frames, meta).

    frames: fps 격자로 샘플된 경량 MapState 리스트 (2D 재생용).
    overrides: {thresholds:{v_th,...}, rho_crit, bottlenecks:{id:{rho_crit,weight}},
                exits:{id:{width_m,q_design,design_capacity}}}
    """
    meta = recorder.load_meta(db_path)
    site = SiteConfig.model_validate(meta["site_view"])
    cams = [CameraConfig.model_validate(c) for c in meta.get("cameras", [])]
    _apply_overrides(site, overrides or {})

    eng = MetricsEngine(site, cams)
    origins = [tuple(o) for o in (meta.get("alarm_origins") or [])] or None
    eng.start_session(t_alarm=float(meta["alarm_ts"]), alarm_origins=origins)

    frames: list[dict] = []
    dt = 1.0 / max(0.5, float(fps))
    last: float | None = None
    for cam_id, ts, tracks in recorder.iter_calls(db_path):
        eng.on_tracks(cam_id, ts, tracks)
        if last is None or ts - last >= dt:
            frames.append(_lite_frame(eng.snapshot()))
            last = ts

    result = eng.stop_session()
    timeline = eng.session_timeline()
    return result, timeline, frames, meta
