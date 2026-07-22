"""설정 스키마 (계약 동결 — 변경은 CONTRACT.md 절차로).

data/sites/<site_id>/site.json + cameras/<cam_id>.json 의 pydantic 모델.
좌표 규약: 공간 요소(경로·구역·병목·출입구)는 전부 **맵 원본 px**,
카메라 측 좌표(valid_roi, cctv_pts)만 **카메라 프레임 px**.
실단위(m) 환산은 spatial 층에서 m_per_px 하나로만 수행한다.
"""
from __future__ import annotations

import math

from pydantic import BaseModel, Field, model_validator

Point = tuple[float, float]

DEFAULT_FLOOR_ID = "default"   # 층 미지정·기존 단일도면 사이트의 기본 층 id (하위호환)


class MapScale(BaseModel):
    """맵 위 2점 + 실거리(m) → m/px 축척."""
    p1: Point
    p2: Point
    meters: float = Field(gt=0)

    @property
    def m_per_px(self) -> float:
        d = math.dist(self.p1, self.p2)
        if d <= 0:
            raise ValueError("축척 기준 2점이 동일 좌표")
        return self.meters / d


class MapSpec(BaseModel):
    """공통 2D 맵. image는 site 디렉토리 상대 파일명.

    축척 미지정(scale·m_per_px 둘 다 None) 상태 허용 — UI 플로우가
    "업로드 → 축척 지정" 순서이기 때문(계약 개정 2026-07-13).
    미지정 시 실단위 지표(속도·밀도)는 None으로 산출되며, 운영 전
    축척 지정은 UI가 강제한다.
    """
    image: str
    w: int = Field(gt=0)
    h: int = Field(gt=0)
    scale: MapScale | None = None      # 수동 축척(2점)
    m_per_px: float | None = None      # cad-convert 메타 자동 시 직접 지정

    def resolve_m_per_px(self) -> float | None:
        if self.m_per_px is not None:
            return self.m_per_px
        return self.scale.m_per_px if self.scale is not None else None


class Route(BaseModel):
    """권장 피난 경로 — 자유곡선 polyline (맵 px)."""
    id: str
    name: str = ""
    points: list[Point] = Field(min_length=2)


class Zone(BaseModel):
    """분석 구역 polygon (맵 px)."""
    id: str
    name: str = ""
    polygon: list[Point] = Field(min_length=3)
    node_id: str | None = None  # IDR 공간그래프 연결 노드 (없으면 중심점에서 최근접 노드)


class Bottleneck(BaseModel):
    """병목 후보영역 polygon (맵 px) + 임계밀도·가중치."""
    id: str
    name: str = ""
    polygon: list[Point] = Field(min_length=3)
    rho_crit: float = Field(default=2.0, gt=0)  # 명/m²
    weight: float = Field(default=1.0, gt=0)


class ExitLine(BaseModel):
    """출입구/비상구 방향성 통과선 (맵 px). inside가 '안쪽' 반평면 지정."""
    id: str
    name: str = ""
    line: tuple[Point, Point]
    inside: Point
    design_capacity: int | None = None  # 설계 총인원(명) — SEI용 예약


class GraphNode(BaseModel):
    """IDR 공간그래프 노드 (맵 px). 수동 정의 (요구사항 D-5)."""
    id: str
    xy: Point


class SpatialGraph(BaseModel):
    """구역 간 이동 공간그래프 — 경보위치→구역 최단거리(IDR)용.
    엣지 가중치는 노드 간 유클리드 거리(m 환산)."""
    nodes: list[GraphNode] = []
    edges: list[tuple[str, str]] = []


class AlarmOrigin(BaseModel):
    """경보 발생원 위치 (맵 px) — IDR 거리 기준점 (N개 지정 가능)."""
    id: str
    name: str = ""
    xy: Point


class GridConfig(BaseModel):
    """IDR 격자 BFS용 배경 격자 설정.
    Zone polygon 내 셀 centroid들의 BFS 평균거리 → D(zone, origin)."""
    cell_size_m: float = Field(default=2.0, gt=0)  # 격자 셀 한 변 실거리 (m)


class Thresholds(BaseModel):
    """판정 임계값 — 전부 UI 설정 가능, 하드코딩 금지 (요구사항 D-6). 실단위."""
    v_th: float = 0.5      # 피난개시 속도 임계 (m/s)
    a_th: float = 0.7      # 방향정렬도 임계 (cosine)
    r_th: float = 0.5      # 동시만족 객체비율 임계 (0~1)
    dt_hold: float = 3.0   # 연속 유지시간 (s)
    d_allow: float = 2.0   # EPFI 허용 이탈거리 (m)
    min_conf: float = 0.35  # 표출·지표 최소 검출 신뢰도 — 저신뢰 오탐(예: 의자)이
    q_design: float = Field(default=60.0, gt=0)  # SEI: 단위 유효폭당 설계 통과기준 [인/분/m]
                            # BYTE 저신뢰 연관으로 연명한 트랙 관측을 지표 층에서 차단


class Floor(BaseModel):
    """도면(층) 1개 — 독립 좌표계의 공간요소 묶음 (다중 도면 지원 v1.7).

    한 사이트는 N개 층을 가진다. 층마다 자기 맵·경로·구역·병목·출입구·
    그래프·경보원·격자를 갖고, 층간 좌표계·Re-ID는 서로 독립이다.
    판정 임계값(Thresholds)은 사이트 공용 — Floor에 두지 않는다.
    필드 타입·기본값은 SiteConfig의 동명 필드와 동일(하위호환).
    """
    id: str
    name: str = ""
    map: MapSpec | None = None
    routes: list[Route] = []
    zones: list[Zone] = []
    bottlenecks: list[Bottleneck] = []
    exits: list[ExitLine] = []
    graph: SpatialGraph = SpatialGraph()
    alarm_origins: list[AlarmOrigin] = []
    grid: GridConfig = GridConfig()


class SiteConfig(BaseModel):
    """사이트 설정 루트 — data/sites/<site_id>/site.json.

    다중 도면(v1.7): 공간요소는 `floors`가 정본이다. 기존 단일도면 사이트는
    top-level map/routes/... 필드를 그대로 두고(하위호환), 로드 후 검증기가
    이를 `floors=[Floor(id="default", ...)]` 하나로 승격한다 → 로드 뒤에는
    언제나 floors≥1이 보장된다. 엔진/세션은 층 개념 없이 `as_floor_view()`가
    돌려주는 SiteConfig(한 층의 공간요소를 top-level에 실은 뷰)를 소비한다.
    """
    site_id: str
    version: int = 1
    map: MapSpec | None = None
    routes: list[Route] = []
    zones: list[Zone] = []
    bottlenecks: list[Bottleneck] = []
    exits: list[ExitLine] = []
    graph: SpatialGraph = SpatialGraph()            # IDR용 수동 그래프 (v1.2)
    alarm_origins: list[AlarmOrigin] = []           # 경보 발생원 N개 (v1.6)
    grid: GridConfig = GridConfig()                 # IDR 격자 BFS 설정 (v1.6)
    floors: list[Floor] = []                        # 도면(층) 목록 (v1.7) — 정본
    thresholds: Thresholds = Thresholds()

    @model_validator(mode="after")
    def _ensure_floors(self):
        """floors 미지정(기존 단일도면 사이트) → top-level 공간요소를 'default'
        층 하나로 승격. 이미 floors가 있으면 그대로 둔다."""
        if not self.floors:
            self.floors = [Floor(
                id=DEFAULT_FLOOR_ID, name="기본",
                map=self.map, routes=self.routes, zones=self.zones,
                bottlenecks=self.bottlenecks, exits=self.exits,
                graph=self.graph, alarm_origins=self.alarm_origins,
                grid=self.grid,
            )]
        return self

    def get_floor(self, floor_id: str | None = None) -> Floor:
        """floor_id에 해당하는 층. None이면 'default', 없으면 첫 층.
        (검증기가 floors≥1을 보장하므로 항상 값을 돌려준다.)"""
        fid = floor_id or DEFAULT_FLOOR_ID
        for fl in self.floors:
            if fl.id == fid:
                return fl
        return self.floors[0]

    def floor_id_of_camera(self, cam: "CameraConfig") -> str:
        """카메라 소속 층 id — floor_id None이면 'default', 존재하지 않는
        층을 가리키면 첫 층으로 해석(고아 카메라 방지)."""
        fid = cam.floor_id or DEFAULT_FLOOR_ID
        ids = {fl.id for fl in self.floors}
        return fid if fid in ids else self.floors[0].id

    def as_floor_view(self, floor_id: str | None = None) -> "SiteConfig":
        """한 층의 공간요소를 top-level에 실은 SiteConfig 뷰.

        엔진/세션은 이 뷰를 받아 층 개념 없이 기존 로직 그대로 동작한다
        (내부 로직 무변경). thresholds·version·site_id는 사이트 공용값 유지."""
        fl = self.get_floor(floor_id)
        return SiteConfig(
            site_id=self.site_id,
            version=self.version,
            map=fl.map,
            routes=fl.routes,
            zones=fl.zones,
            bottlenecks=fl.bottlenecks,
            exits=fl.exits,
            graph=fl.graph,
            alarm_origins=fl.alarm_origins,
            grid=fl.grid,
            thresholds=self.thresholds,
        )


class CameraMapping(BaseModel):
    """카메라↔맵 대응점(4점 이상)과 호모그래피.

    H는 서버가 cv2.findHomography(cctv_pts, map_pts)로 산출해 저장
    (row-major 9원소, 카메라 px → 맵 px). 클라이언트는 점만 보낸다.
    """
    cctv_pts: list[Point] = Field(min_length=4)
    map_pts: list[Point] = Field(min_length=4)
    H: list[float] = Field(min_length=9, max_length=9)
    reproj_err_px: list[float] | None = None  # 대응점별 재투영 오차(맵 px) —
                                              # FR-01 기준점 오차 기록 (v1.5)

    @model_validator(mode="after")
    def _same_len(self):
        if len(self.cctv_pts) != len(self.map_pts):
            raise ValueError("cctv_pts와 map_pts 개수 불일치")
        return self


class CameraConfig(BaseModel):
    """카메라 설정 — data/sites/<site_id>/cameras/<cam_id>.json."""
    cam_id: str
    name: str = ""
    rtsp: str
    enabled: bool = True
    analyze_fps: float = Field(default=5.0, gt=0, le=30)
    floor_id: str | None = None            # 소속 층 id (v1.7). None이면 "default"
                                           # 층으로 해석 (하위호환 기본 None).
    mapping: CameraMapping | None = None   # 없으면 맵 투영 불가 → 처리 제외
    valid_roi: list[Point] | None = None   # 카메라 px 유효영역 (없으면 전체)
    min_conf: float | None = Field(default=None, ge=0, le=1)  # 카메라별 최소 검출
                                           # 신뢰도 오버라이드. None이면 사이트
                                           # Thresholds.min_conf 상속, 값 지정 시
                                           # 그 카메라만 오버라이드 (하위호환 기본 None).
