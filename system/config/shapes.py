"""영역(polygon) 생성 도형 — 부채꼴 등 (맵 px, 순수 기하).

병목 영역은 실제로 문·계단 앞에서 **부채꼴**로 형성된다(문을 꼭짓점으로
사람이 퍼지는 모양). 그런 영역을 자유 다각형으로 손으로 찍으면 매번 다른
모양이 나오고 반경·각도를 나중에 못 고친다. 그래서 도형 파라미터
(중심·반경·각도)를 저장하고, polygon은 **거기서 생성한 결과**로 둔다.

polygon이 계약상 정본이다(엔진은 polygon만 본다). 이 모듈은 그 polygon을
파라미터로부터 결정적으로 만드는 유일한 곳 — 프론트도 같은 식으로 그리되,
저장 시 백엔드가 이 함수로 다시 만들어 덮으므로 값의 진실은 여기다.

의존성 없음(math만) — `system.config.schema`가 import 하므로
`system.spatial`(schema를 import한다)을 여기서 참조하면 순환이 된다.
"""
from __future__ import annotations

import math

Point = tuple[float, float]

SECTOR_MIN_SEG = 3          # 호 분할 최소
SECTOR_MAX_SEG = 180        # 상한 (저장 용량·렌더 비용)


def sector_polygon(center: Point, radius: float, a0: float, sweep: float,
                   segments: int = 24, radius_in: float = 0.0) -> list[Point]:
    """부채꼴 근사 polygon (맵 px).

    center   부채꼴 꼭짓점 (문·계단 입구 위치)
    radius   외곽 반경 (px)
    a0       시작각 (rad, atan2(dy,dx) — 맵 좌표계 y는 아래로 증가)
    sweep    스윕각 (rad, 부호가 회전 방향)
    segments 호 분할 수 (많을수록 원에 가까움)
    radius_in 내부 반경 (>0이면 도넛 부채꼴 — 문 바로 앞을 비울 때)

    반환: 외곽 호(a0→a0+sweep) + 내부 호(역방향) 또는 꼭짓점.
    """
    cx, cy = float(center[0]), float(center[1])
    r = float(radius)
    ri = max(0.0, float(radius_in))
    n = max(SECTOR_MIN_SEG, min(SECTOR_MAX_SEG, int(segments)))
    if r <= 0 or ri >= r or sweep == 0:
        raise ValueError("부채꼴 파라미터 오류 (radius>radius_in>=0, sweep≠0)")
    arc = [(cx + r * math.cos(a0 + sweep * i / n),
            cy + r * math.sin(a0 + sweep * i / n)) for i in range(n + 1)]
    if ri > 0:
        inner = [(cx + ri * math.cos(a0 + sweep * i / n),
                  cy + ri * math.sin(a0 + sweep * i / n))
                 for i in range(n, -1, -1)]
        return arc + inner
    return [(cx, cy)] + arc


def sector_area_px2(radius: float, sweep: float, radius_in: float = 0.0) -> float:
    """부채꼴 해석 면적 (px²) — 근사 polygon 검증용."""
    return abs(sweep) / 2.0 * (float(radius) ** 2 - float(radius_in) ** 2)
