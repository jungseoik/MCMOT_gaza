# -*- coding: utf-8 -*-
"""병목(밀집구역) 크기 조정 — **발표 표출 전용**.

현장에서 찍은 병목은 문 바로 앞만 좁게 잡혀 있어, 영상에서 사람이 그 안에
들어가 있는 순간이 짧다. "여기서 밀렸다"가 눈에 안 보인다. 그래서 표출과
v2 지표 산출에서만 **중심 기준으로 반경을 키운다** — site.json 은 건드리지 않는다.

반경을 키우면 면적이 커져 밀도(N/A)가 낮아지므로 CBS 값도 내려간다. 표시만
키우고 계산은 원래대로 두면 화면과 수치가 어긋나므로, 양쪽에 같이 적용한다.
"""
from __future__ import annotations

import os

SCALE = float(os.environ.get("DEMO_BN_SCALE", "1.5"))   # 반경 배수 (기본)
# 병목별 배수 — 지정한 것만 다르게 키운다. 사람이 실제로 지나는 문(좌측 b1·b4)은
# 넓게 봐야 "여기서 밀렸다" 가 잡히고, 아무도 안 지나는 곳(우측 b2·b3)까지 키우면
# 화면만 어지럽다.
PER_ID = {"b1": 2.0, "b4": 2.0}      # 좌측 문 2곳 — 사람이 실제로 지나간다
# 병목 옮기기·크기 — :8940 편집기가 저장한 파일이 정본이다.
#   results/demo/_annotate/bn_edit_<층>.json  {"move":{id:[dx,dy]}, "scale":{id:배수}}
# 부채꼴은 center 를 옮겨야 한다 — polygon 만 고치면 스키마 검증에서 shape 로
# 다시 만들어져 제자리로 돌아간다.
from pathlib import Path as _P
import json as _json

_ANN = _P(__file__).resolve().parents[1] / "results/demo/_annotate"
MOVE: dict = {}
_LOADED = {"floor": None}


def load(floor: str) -> None:
    """층별 편집본을 읽어 MOVE·PER_ID 에 얹는다(없으면 기본값 유지)."""
    global MOVE
    if _LOADED["floor"] == floor:
        return
    _LOADED["floor"] = floor
    p = _ANN / f"bn_edit_{floor}.json"
    if not p.is_file():
        return
    try:
        d = _json.loads(p.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return
    MOVE = {k: tuple(float(v) for v in xy) for k, xy in (d.get("move") or {}).items()}
    for k, v in (d.get("scale") or {}).items():
        PER_ID[k] = float(v)
ONLY_IDS = [x for x in os.environ.get("DEMO_BN_IDS", "").split(",") if x.strip()]
# 임계밀도(명/m²) — 반경을 키우면 면적이 배수의 제곱으로 커져 밀도가 그만큼
# 묽어진다(반경 1.5배 = 면적 2.25배). 임계를 그대로 두면 "저기 저렇게 모였는데
# CBS 0" 이 된다. 같은 혼잡을 같은 값으로 재려면 임계도 면적 배수만큼 낮춘다.
#   ρcrit' = ρcrit / SCALE²   (2.0 / 2.25 ≈ 0.89)
# 자동 환산(ρcrit/SCALE² = 0.89)은 원래 값과 비슷하게 나오지만, 영역이 넓어진
# 만큼 **더 민감하게** 잡고 싶어 0.6 으로 둔다(실측 CBS 12.04 → 16.50).
# 영역을 키우면 면적이 배수의 제곱으로 커져 밀도가 그만큼 묽어진다 — 임계를
# 그대로 두면 "저기 저렇게 모였는데 CBS 0" 이 된다. :8940 에서 맞춘 지금 크기
# (b1 9.9m² · b4 10.7m²)에서 03 CBS 가 원래 수준(12.04)을 넘도록 잡은 값이
# 0.15 다(→ 13.24). 최대 밀도는 1.01 명/m².
RHO_CRIT = os.environ.get("DEMO_BN_RHO", "0.15")  # 빈 문자열이면 자동 환산


def scale_of(bn_id: str) -> float:
    """이 병목에 적용할 반경 배수."""
    if ONLY_IDS and bn_id not in ONLY_IDS:
        return 1.0
    return float(PER_ID.get(bn_id, SCALE))


def scale_shape(bn: dict, scale: float | None = None) -> dict:
    """병목 dict 한 건을 키운다 (제자리 수정).

    **부채꼴(shape.kind="sector")이면 radius 를 키워야 한다** — polygon 은
    스키마 검증에서 shape 파라미터로 다시 만들어지므로(Bottleneck.
    _rebuild_from_shape), polygon 만 고치면 원래 크기로 되돌아간다.
    """
    k = scale_of(bn.get("id", "")) if scale is None else float(scale)
    mv = MOVE.get(bn.get("id", ""))
    # shape 는 얕은 복사로 원본과 공유되기 쉽다 — 여기서 고치면 호출부의 원본까지
    # 바뀌고, 두 번 부르면 배수가 누적된다. 항상 제 것으로 떼어 쓴다.
    if bn.get("shape"):
        bn["shape"] = dict(bn["shape"])
        if bn["shape"].get("center"):
            bn["shape"]["center"] = list(bn["shape"]["center"])
    sh = bn.get("shape") or None
    if mv:
        dx, dy = mv
        if sh and sh.get("center"):
            sh["center"] = [float(sh["center"][0]) + dx, float(sh["center"][1]) + dy]
        if bn.get("polygon"):
            bn["polygon"] = [[float(x) + dx, float(y) + dy] for x, y in bn["polygon"]]
    if sh and sh.get("kind") == "sector" and sh.get("radius"):
        sh["radius"] = float(sh["radius"]) * k
        if sh.get("radius_in"):
            sh["radius_in"] = float(sh["radius_in"]) * k
        # **부채꼴은 꼭짓점(center) 기준으로 키워야 한다.** polygon 무게중심으로
        # 키우면 스키마가 shape 로 다시 만든 것과 자리가 어긋난다 — 영상(raw dict)
        # 과 지표(SiteConfig)가 다른 자리를 보게 된다.
        if bn.get("polygon"):
            cx, cy = float(sh["center"][0]), float(sh["center"][1])
            bn["polygon"] = [[cx + (float(x) - cx) * k, cy + (float(y) - cy) * k]
                             for x, y in bn["polygon"]]
    elif bn.get("polygon"):
        bn["polygon"] = [list(p) for p in scaled(bn["polygon"], k)]
    # 임계밀도도 같이 낮춘다 — 면적이 k² 배 커졌으므로
    if RHO_CRIT:
        bn["rho_crit"] = float(RHO_CRIT)
    elif bn.get("rho_crit"):
        bn["rho_crit"] = round(float(bn["rho_crit"]) / (k * k), 4)
    return bn


def scaled(points, scale: float | None = None):
    """다각형을 **중심 기준**으로 확대. 반경 배수 = scale."""
    k = SCALE if scale is None else float(scale)
    pts = [(float(x), float(y)) for x, y in points]
    if k == 1.0 or len(pts) < 3:
        return pts
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    return [(cx + (x - cx) * k, cy + (y - cy) * k) for x, y in pts]
