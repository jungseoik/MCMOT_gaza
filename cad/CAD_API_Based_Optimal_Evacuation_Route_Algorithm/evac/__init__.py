"""
evac — CAD 기반 최적 피난경로 산출 모듈 (삼성 TravelDistance_Analyzer 파이썬 포팅).

서브모듈
  core   : 순수 알고리즘(격자화·멀티소스 다익스트라·경로평활화). CAD/플롯 의존 없음.
  cad    : DXF 입출력(장애물/Exit/Occupant 수집, Exit 역주입, ref 경로 로드).
  render : 평면도/경로/연결성 렌더(미터격자·스케일바 스타일).
  pick   : Exit 대화식 지정기(GUI).
  cli    : 커맨드라인(route/pick/connect).

빠른 사용(코드):
    from evac import cad, core, render
    dxf = cad.load_dxf("plan.dxf")
    an  = core.analyze(dxf.obstacles, dxf.exits, dxf.bounds,
                       starts=dxf.occupants, mode="occupant")
    render.render_routes("out.png", dxf.obstacles, dxf.bounds, an, exits=dxf.exits)
"""
from . import core, cad, render   # noqa: F401

__all__ = ["core", "cad", "render", "pick", "cli"]
__version__ = "0.1.0"
