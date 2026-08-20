"""맵 좌표계 순수 기하 유틸 (M4).

모든 좌표는 공통 2D 맵 원본 px (계약 §1). 실단위(m) 환산 축척은
`MapSpec.resolve_m_per_px()` 하나로만 얻는다 — 본 모듈은 그 값을
인자로 받아 쓸 뿐, 자체 축척 계산을 두지 않는다.

- point_in_polygon / polygon_area : cv2 기반 (shapely 등 신규 의존성 금지)
- nearest_on_polyline : 점→polyline 최근접거리 + 최근접 구간 tangent
  (후속 EPFI 거리적분·IDR 방향정렬도가 그대로 쓰는 공용 기하)
- DirectionalLine : 방향성 선분 crossing 판정 — webui/counter.py의
  LineCounter 부호판정 로직을 맵 좌표·키(gid) 기반·다중 인스턴스로 이식.
  카운팅·debounce 정책은 metrics 층 소유.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

Point = tuple[float, float]


def _contour(polygon) -> np.ndarray:
    """cv2가 요구하는 (N,1,2) float32 contour로 변환."""
    return np.asarray(polygon, dtype=np.float32).reshape(-1, 1, 2)


def point_in_polygon(pt: Point, polygon) -> bool:
    """점의 polygon 포함 여부 (경계 포함, cv2.pointPolygonTest >= 0)."""
    return cv2.pointPolygonTest(
        _contour(polygon), (float(pt[0]), float(pt[1])), False) >= 0


def polygon_area_px2(polygon) -> float:
    """polygon 면적 (맵 px²)."""
    return float(abs(cv2.contourArea(_contour(polygon))))


def polygon_area_m2(polygon, m_per_px: float) -> float:
    """polygon 면적 (m²) — 축척은 호출자가 resolve_m_per_px()로 공급."""
    return polygon_area_px2(polygon) * float(m_per_px) ** 2


# ------------------------------------------------------ 점 → polyline 최근접


@dataclass(frozen=True)
class PolylineHit:
    """점→polyline 최근접 결과."""
    dist_px: float                 # 최근접거리 (맵 px)
    tangent: tuple[float, float]   # 최근접 구간 진행방향 단위벡터 (points 순서 기준)
    seg_idx: int                   # 최근접 구간 인덱스 (points[i]→points[i+1])
    point: tuple[float, float]     # polyline 위 최근접점 (맵 px)


def nearest_on_polyline(pt: Point, points) -> PolylineHit:
    """점에서 polyline까지 최근접거리·최근접 구간 tangent.

    tangent는 경로 진행방향(points 등록 순서)의 단위벡터 —
    IDR 방향정렬도(경로 tangent와 cosine)·EPFI 이탈거리의 공용 기하.
    """
    P = np.asarray(pt, dtype=np.float64)
    V = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if len(V) < 2:
        raise ValueError("polyline은 2점 이상 필요")
    A, B = V[:-1], V[1:]
    AB = B - A
    L2 = (AB ** 2).sum(axis=1)
    safe = np.where(L2 <= 0.0, 1.0, L2)
    t = np.clip(((P - A) * AB).sum(axis=1) / safe, 0.0, 1.0)
    t = np.where(L2 <= 0.0, 0.0, t)          # 퇴화 구간(길이 0)은 시작점으로
    C = A + t[:, None] * AB                   # 구간별 최근접점
    d2 = ((P - C) ** 2).sum(axis=1)
    i = int(np.argmin(d2))
    L = math.sqrt(float(L2[i]))
    tangent = (float(AB[i, 0] / L), float(AB[i, 1] / L)) if L > 0 else (0.0, 0.0)
    return PolylineHit(dist_px=math.sqrt(float(d2[i])), tangent=tangent,
                       seg_idx=i, point=(float(C[i, 0]), float(C[i, 1])))


# ------------------------------------------------------ 방향성 선분 crossing


class DirectionalLine:
    """방향성 통과선 crossing 판정 (webui/counter.py LineCounter 이식).

    선(2점)이 평면을 둘로 나누고, `inside` 점이 '안쪽' 반평면을 지정한다.
    키(gid)별 마지막 안정 부호를 기억했다가 부호가 뒤집히면 crossing —
    안쪽으로 뒤집히면 "in", 바깥쪽이면 "out"을 반환한다.

    - margin_px: 선 근처 데드밴드(지터 방지) — 이 안의 관측은 무시
    - segment_only: True면 선분 범위(±seg_pad 여유) 안에서 넘은 것만 인정
    - 카운트·왕복 debounce는 하지 않는다(metrics 층 정책)
    """

    def __init__(self, line: tuple[Point, Point], inside: Point,
                 margin_px: float = 0.0, segment_only: bool = True,
                 seg_pad: float = 0.06):
        self.A = np.asarray(line[0], dtype=np.float64)
        self.B = np.asarray(line[1], dtype=np.float64)
        self.AB = self.B - self.A
        self.L = float(np.hypot(self.AB[0], self.AB[1])) or 1.0
        self.margin = float(margin_px)
        self.segment_only = bool(segment_only)
        self.seg_pad = float(seg_pad)
        self.inside_sign = self._side(np.asarray(inside, dtype=np.float64))
        self._side_of: dict[str, int] = {}   # key -> 마지막 안정 부호 (+1/-1)

    def _signed_dist(self, P: np.ndarray) -> float:
        cross = self.AB[0] * (P[1] - self.A[1]) - self.AB[1] * (P[0] - self.A[0])
        return float(cross / self.L)

    def _side(self, P: np.ndarray) -> int:
        return 1 if self._signed_dist(P) >= 0 else -1

    def _near_segment(self, P: np.ndarray) -> bool:
        t = float(np.dot(P - self.A, self.AB) / (self.L ** 2))
        return -self.seg_pad <= t <= 1.0 + self.seg_pad

    def observe(self, key: str, pt: Point) -> str | None:
        """키의 현재 위치(맵 px)를 관측 — 이번에 선을 넘었으면 "in"/"out"."""
        P = np.asarray(pt, dtype=np.float64)
        d = self._signed_dist(P)
        if abs(d) < self.margin:              # 선 근처 지터 — 판정 보류
            return None
        cur = 1 if d > 0 else -1
        prev = self._side_of.get(key)
        self._side_of[key] = cur
        if prev is None or cur == prev:       # 최초 관측이거나 같은 편 유지
            return None
        if self.segment_only and not self._near_segment(P):
            return None                       # 선분 밖에서 반평면만 넘음
        return "in" if cur == self.inside_sign else "out"

    def forget(self, key: str) -> None:
        """소실된 키의 부호 상태 제거."""
        self._side_of.pop(key, None)


class ZoneGate:
    """화면 영역 진입 판정 — 문이 화면 가장자리라 선 통과가 불가능할 때.

    선(DirectionalLine)은 "선 이쪽 관측 → 저쪽 관측" 두 번이 필요하다. 그런데
    출입문이 프레임 가장자리에 있으면 사람이 문으로 들어가며 화면에서 사라지지
    **선 반대편에 나타나지 않는다** — 그래서 아무리 잘 그어도 카운트가 안 된다.

    이 클래스는 "저쪽 관측"을 요구하지 않는다:
        영역 **밖**에서 한 번이라도 본 키가 → 영역 **안**으로 들어와
        dwell 프레임 이상 머물면 → "out" 1회.

    포함 판정은 발끝점만 보지 않는다. 문 앞에서는 발이 문틀·프레임 경계에
    잘려 발끝점이 튀므로, bbox 면적의 일정 비율 이상이 겹쳐도 안으로 본다.
    """

    def __init__(self, polygon, dwell: int = 3, overlap: float = 0.3):
        self.poly = [tuple(p) for p in polygon]
        self.dwell = max(1, int(dwell))
        self.overlap = float(overlap)
        self._seen_outside: set = set()   # 밖에서 본 적 있는 키
        self._streak: dict = {}           # 키 -> 연속 진입 프레임 수

    def _inside(self, pt, bbox=None) -> bool:
        if pt is not None and point_in_polygon(pt, self.poly):
            return True                    # 발끝이 안에 있으면 확정
        if bbox is None or self.overlap <= 0:
            return False
        # bbox 를 5x5 로 샘플링해 겹침 비율 추정 (신규 의존성 없이)
        x1, y1, x2, y2 = (float(v) for v in bbox)
        if x2 <= x1 or y2 <= y1:
            return False
        n = 5
        hit = 0
        for i in range(n):
            for j in range(n):
                px = x1 + (x2 - x1) * (i + 0.5) / n
                py = y1 + (y2 - y1) * (j + 0.5) / n
                if point_in_polygon((px, py), self.poly):
                    hit += 1
        return hit / (n * n) >= self.overlap

    def observe(self, key: str, pt=None, bbox=None) -> str | None:
        """이번 관측으로 '나감'이 성립하면 "out", 아니면 None."""
        inside = self._inside(pt, bbox)
        if not inside:
            self._seen_outside.add(key)    # 밖에서 접근한 이력
            self._streak.pop(key, None)
            return None
        if key not in self._seen_outside:
            return None                    # 처음부터 안에 있던 것(문 안쪽에서 등장)
        n = self._streak.get(key, 0) + 1
        self._streak[key] = n
        return "out" if n == self.dwell else None   # dwell 도달 순간 1회만

    def forget(self, key: str) -> None:
        self._seen_outside.discard(key)
        self._streak.pop(key, None)

