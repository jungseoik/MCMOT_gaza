"""설정 스키마 (계약 동결 — 변경은 CONTRACT.md 절차로).

data/sites/<site_id>/site.json + cameras/<cam_id>.json 의 pydantic 모델.
좌표 규약: 공간 요소(경로·구역·병목·출입구)는 전부 **맵 원본 px**,
카메라 측 좌표(valid_roi, cctv_pts)만 **카메라 프레임 px**.
실단위(m) 환산은 spatial 층에서 m_per_px 하나로만 수행한다.
"""
from __future__ import annotations

import math

from pydantic import BaseModel, Field, model_validator

from system.config.shapes import sector_polygon

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
    # 출처(표시 전용) — 자동 축척을 어디서 얻었는지 사용자가 확인할 수 있게.
    # 이게 없으면 "이 숫자를 믿어도 되나" 싶어 불필요한 2점 축척을 다시 긋게 된다.
    source: str | None = None          # 원본 도면 파일명 (예: 17F_v2.dwg)
    unit: str | None = None            # 도면 단위 ($INSUNITS, 예: "mm")

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


class AreaShape(BaseModel):
    """영역을 만든 도형 파라미터 (재편집용, v1.12).

    polygon이 계약상 정본이고 이건 "어떻게 만들었나"의 기록이다. kind가
    "sector"면 로드·저장 시 polygon을 이 파라미터로 다시 생성한다 → 반경·
    각도만 고쳐도 영역이 정확히 갱신된다(다시 찍을 필요 없음).
    kind가 "polygon"(기본)이면 polygon을 그대로 둔다 = 기존 자유 다각형.
    """
    kind: str = "polygon"            # polygon | sector
    center: Point | None = None      # 부채꼴 꼭짓점 (맵 px)
    radius: float | None = None      # 외곽 반경 (맵 px)
    radius_in: float = 0.0           # 내부 반경 (0=꽉 찬 부채꼴)
    a0: float | None = None          # 시작각 (rad)
    sweep: float | None = None       # 스윕각 (rad, 부호=회전방향)
    segments: int = Field(default=24, ge=3, le=180)

    def is_sector(self) -> bool:
        return (self.kind == "sector" and self.center is not None
                and self.radius is not None and self.radius > self.radius_in
                and self.a0 is not None and self.sweep not in (None, 0))

    def build_polygon(self) -> list[Point] | None:
        """파라미터 → polygon. sector가 아니거나 값이 부족하면 None."""
        if not self.is_sector():
            return None
        return sector_polygon(self.center, self.radius, self.a0, self.sweep,
                              self.segments, self.radius_in)


class Bottleneck(BaseModel):
    """병목 후보영역 polygon (맵 px) + 임계밀도·가중치.

    polygon은 자유 다각형이거나 부채꼴(`shape.kind="sector"`)에서 생성된
    것이다 — 실제 병목은 문 앞에서 부채꼴로 생기므로(v1.12).
    `group`은 여러 병목을 하나로 묶어 보기 위한 라벨 — 전체 평균만으로는
    "어느 문이 더 중요한가"가 묻히기 때문(CBS 그룹 집계, v1.12).
    """
    id: str
    name: str = ""
    polygon: list[Point] = Field(min_length=3)
    rho_crit: float = Field(default=2.0, gt=0)  # 명/m²
    weight: float = Field(default=1.0, gt=0)
    shape: AreaShape | None = None      # 생성 도형(부채꼴 등). None=자유 다각형
    group: str = ""                     # 집계 그룹 라벨 (빈 문자열=미분류)

    @model_validator(mode="after")
    def _rebuild_from_shape(self):
        """부채꼴이면 polygon을 파라미터에서 재생성 (파라미터가 진실)."""
        if self.shape is not None:
            pts = self.shape.build_polygon()
            if pts is not None:
                self.polygon = pts
        return self


class ExitLine(BaseModel):
    """출입구/비상구 방향성 통과선 (맵 px). inside가 '안쪽' 반평면 지정.

    **카메라 화면 통과선(선택)** — count_cam·cam_line·cam_inside 를 채우면
    카운트를 맵 좌표가 아니라 그 카메라의 **화면 px** 에서 판정한다.
    문 앞은 대응점 헐 밖이라 맵 투영이 안 되는 경우가 많은데(화각이 눕는
    구간이라 헐을 억지로 늘리면 좌표 오차가 폭증), 카운트는 좌표가 아니라
    "넘었나"만 필요하므로 화면에서 재면 캘리브레이션 없이 정확하다.

    셋이 비어 있으면 기존 동작(맵 선으로 카운트)과 완전히 동일하다.
    line/inside 는 채워져 있어도 계속 쓰인다 — 화면 표시와 폭(W_eff→C_j) 산출용.
    """
    id: str
    name: str = ""
    line: tuple[Point, Point]
    inside: Point
    # --- SEI 설계 통과용량 C_j = W_eff × q_design (v1.12) ---
    # W_eff는 도면 축척으로 자동 산출되지만(선 길이 × m_per_px) 도면과 실제
    # 문 폭이 다를 수 있어 사람이 덮어쓸 수 있어야 한다. q_design도 문마다
    # 다르다(계단 vs 주출입구) — 사이트 전역값만으로는 표현이 안 된다.
    width_m: float | None = Field(default=None, gt=0)   # 유효폭 수동값 [m].
                                        # None=도면 자동(선 길이×m_per_px)
    q_design: float | None = Field(default=None, gt=0)  # 이 문의 단위폭당
                                        # 설계 통과기준 [인/분/m]. None=사이트값
    design_capacity: int | None = None  # C_j [인/분] — 위 둘에서 **파생**.
                                        # SiteConfig 검증기가 로드·저장마다
                                        # 다시 채운다(직접 편집 대상 아님)
    count_cam: str | None = None        # 카운트 담당 카메라 id (없으면 맵 카운트)
    cam_line: tuple[Point, Point] | None = None   # 그 카메라 화면 px 통과선
    cam_inside: Point | None = None               # 그 카메라 화면 px '안쪽' 점
    # 화면 **영역** 방식 — 문이 프레임 가장자리라 선 통과가 성립하지 않을 때
    # (사람이 문으로 들어가며 화면에서 사라져 선 반대편에 안 나타남).
    # 영역 밖에서 본 적 있는 트랙이 영역 안으로 들어와 머물면 통과로 센다.
    cam_zone: list[Point] | None = None           # 그 카메라 화면 px 다각형(3점 이상)
    cam_zone_dwell: int = 2                       # 진입 후 이 프레임 수만큼 머물면 집계
                                                  # (5fps 실측: 3이면 발끝 잘린 궤적을
                                                  #  놓친다 — 접촉이 2프레임뿐)

    def auto_width_m(self, m_per_px: float | None) -> float | None:
        """도면 축척 기준 유효폭 — 통과선 길이(px) × m_per_px."""
        if not m_per_px:
            return None
        d = math.dist(tuple(self.line[0]), tuple(self.line[1]))
        return d * m_per_px if d > 0 else None

    def effective_width_m(self, m_per_px: float | None) -> float | None:
        """실제 계산에 쓰는 유효폭 — 수동값이 있으면 그것이 우선."""
        if self.width_m is not None and self.width_m > 0:
            return float(self.width_m)
        return self.auto_width_m(m_per_px)

    def effective_q_design(self, q_default: float) -> float:
        """이 문의 단위폭당 설계 통과기준 — 미지정이면 사이트 전역값."""
        return float(self.q_design) if self.q_design else float(q_default)

    def resolve_capacity(self, m_per_px: float | None,
                         q_default: float) -> int | None:
        """C_j = W_eff × q_design [인/분]. 폭을 못 구하면 None(SEI 분포 제외)."""
        w = self.effective_width_m(m_per_px)
        q = self.effective_q_design(q_default)
        if w is None or w <= 0 or q <= 0:
            return None
        return max(1, int(round(w * q)))

    def counts_in_camera(self) -> bool:
        """카운트를 화면 좌표에서 하는가 (선 또는 영역)."""
        return bool(self.count_cam) and (
            bool(self.cam_line and self.cam_inside)
            or bool(self.cam_zone and len(self.cam_zone) >= 3))

    def camera_zone_mode(self) -> bool:
        """화면 방식 중 **영역**을 쓰는가 (영역이 선보다 우선)."""
        return bool(self.count_cam and self.cam_zone and len(self.cam_zone) >= 3)


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

    @model_validator(mode="after")
    def _derive_exit_capacity(self):
        """출구 C_j = W_eff × q_design 을 로드·저장 시점에 파생 산출 (v1.12).

        C_j를 사람이 직접 넣던 필드에서 **파생값**으로 바꾼다 — 예전에는
        프론트 save()가 계산해 넣어서, API로 직접 넣은 설정이나 축척이 나중에
        잡힌 층은 C_j가 비어 SEI 분포에서 조용히 빠졌다. 사람이 조절하는 값은
        폭(width_m)과 기준(q_design)이고 C_j는 그 결과다.
        폭을 못 구하는 층(축척 미지정 + 수동폭 없음)은 기존 값을 건드리지 않는다.
        """
        q_default = self.thresholds.q_design
        groups = [(self.exits, self.map)] + [(fl.exits, fl.map) for fl in self.floors]
        for exits, mp in groups:
            mpp = mp.resolve_m_per_px() if mp else None
            for ex in exits:
                cap = ex.resolve_capacity(mpp, q_default)
                if cap is not None:
                    ex.design_capacity = cap
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
