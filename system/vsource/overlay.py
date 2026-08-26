"""리허설 전용 카메라 설정 오버레이 — production 을 건드리지 않고 갈아끼운다.

**왜 필요한가.** 리허설은 새로 준비한 영상을 넣는 게 기본이다. 새 영상은 시점이
달라 매핑을 다시 잡아야 하는데, `PUT /api/cameras/{id}/mapping` 은 카메라 파일에
바로 쓴다. 그대로 두면 **리허설 매핑이 현장 실 RTSP용 매핑을 영구히 덮어쓴다.**

**어떻게.** 매핑을 카메라가 아니라 시나리오 옆(`<id>.cams.json`)에 저장하고,
`RT.cameras()` 한 곳에서 리허설이 도는 동안만 얹는다.

- production JSON 은 **읽기만** 한다 → 리허설이 죽어도 원본이 멀쩡하다
- 오버레이가 없는 카메라는 production 값을 그대로 쓴다 (디폴트 = 상속)
- 리허설을 끄면 얹기를 멈추는 것으로 끝 — 되돌릴 상태가 없다

설계: docs/architecture/08-훈련영상-동기송출-설계.md §5-2
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SCENARIO_DIR = Path("data/scenarios")

# 오버레이가 덮을 수 있는 필드 — 카메라의 "어디를 어떻게 보는가"에 해당하는 것만.
# rtsp·enabled 처럼 연결 자체를 바꾸는 건 제외한다(리허설이 배선을 바꾸면 안 된다).
FIELDS = ("mapping", "valid_roi", "floor_id")


def path_for(scenario_id: str) -> Path:
    return SCENARIO_DIR / f"{scenario_id}.cams.json"


def load(scenario_id: str) -> dict[str, dict]:
    """{cam_id: {mapping, valid_roi, floor_id}} — 없으면 빈 dict."""
    f = path_for(scenario_id)
    if not f.is_file():
        return {}
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("[vsource] 오버레이 읽기 실패: %s", f)
        return {}
    cams = d.get("cameras") if isinstance(d, dict) else None
    return cams if isinstance(cams, dict) else {}


def save_cam(scenario_id: str, cam_id: str, patch: dict) -> None:
    """카메라 1대분 오버레이를 갱신 (원자적 교체)."""
    cams = load(scenario_id)
    cur = dict(cams.get(cam_id) or {})
    for k in FIELDS:
        if k in patch:
            cur[k] = patch[k]
    cams[cam_id] = cur
    f = path_for(scenario_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"scenario_id": scenario_id, "cameras": cams},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)
    logger.info("[vsource] 리허설 매핑 저장: %s / %s", scenario_id, cam_id)


def clear_cam(scenario_id: str, cam_id: str) -> bool:
    """오버레이 제거 → 그 카메라는 다시 production 값을 쓴다."""
    cams = load(scenario_id)
    if cam_id not in cams:
        return False
    del cams[cam_id]
    f = path_for(scenario_id)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"scenario_id": scenario_id, "cameras": cams},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)
    return True


def apply(cams: list, overlay: dict[str, dict]) -> list:
    """카메라 목록에 오버레이를 얹은 **새 목록**을 돌려준다.

    원본 객체를 그대로 두는 게 핵심이다 — production 설정은 읽기 전용으로 다룬다.
    """
    if not overlay:
        return cams
    out = []
    for c in cams:
        patch = overlay.get(getattr(c, "cam_id", None))
        if not patch:
            out.append(c)
            continue
        try:
            out.append(type(c).model_validate({**c.model_dump(), **{
                k: v for k, v in patch.items() if k in FIELDS}}))
        except Exception:
            logger.exception("[vsource] 오버레이 적용 실패: %s", getattr(c, "cam_id", "?"))
            out.append(c)
    return out
