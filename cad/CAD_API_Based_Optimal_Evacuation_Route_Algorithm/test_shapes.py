"""apply_shapes 단위 검증 — 뚫기·막기·적용순서.

10m×10m 공간을 가운데 칸막이로 둘로 나눈 뒤,
뚫기(선/사각형)로 합쳐지는지, 막기가 뚫기를 이기는지 확인한다.
"""
import numpy as np
from scipy.ndimage import label

from evac import core

WALLS = np.array([
    [0, 0, 10000, 0], [0, 10000, 10000, 10000],
    [0, 0, 0, 10000], [10000, 0, 10000, 10000],
    [5000, 0, 5000, 10000],                      # 가운데 칸막이
], float)
CELL = 100.0
N = 100


def diag(shapes):
    g = core.build_obstacle_grid(WALLS, 0, 0, N, N, CELL, core.CLEARANCE, ())
    if shapes:
        core.apply_shapes(g, shapes, 0, 0, CELL)
    lab, n = label(~g)
    return n, int((~g).sum())


def main():
    n0, f0 = diag(None)
    print(f"① 편집 없음            : 조각 {n0}개 · 통행셀 {f0}")
    assert n0 == 2, "칸막이로 두 조각이어야 함"

    line = [{"op": "open", "kind": "line", "pts": [5000, 4000, 5000, 6000], "w": 900}]
    n1, f1 = diag(line)
    print(f"② 뚫기(선)             : 조각 {n1}개 · 통행셀 {f1}")
    assert n1 == 1, "선으로 뚫으면 하나로 합쳐져야 함"

    rect = [{"op": "open", "kind": "rect", "pts": [4500, 3000, 5500, 7000]}]
    n2, f2 = diag(rect)
    print(f"③ 뚫기(사각형)         : 조각 {n2}개 · 통행셀 {f2}")
    assert n2 == 1 and f2 > f1, "사각형이 선보다 넓게 뚫려야 함"

    both = rect + [{"op": "block", "kind": "rect", "pts": [4500, 3000, 5500, 7000]}]
    n3, f3 = diag(both)
    print(f"④ 뚫고 같은 자리 막기   : 조각 {n3}개 · 통행셀 {f3}")
    assert n3 == 2, "막기가 뚫기를 이겨 다시 분리돼야 함 (적용 순서)"

    poly = [{"op": "block", "kind": "poly",
             "pts": [1000, 1000, 4000, 1000, 4000, 4000, 1000, 4000]}]
    n4, f4 = diag(poly)
    print(f"⑤ 차단(다각형)         : 조각 {n4}개 · 통행셀 {f4}")
    assert f4 < f0, "다각형 차단으로 통행셀이 줄어야 함"

    blk_line = [{"op": "block", "kind": "line",
                 "pts": [0, 5000, 4900, 5000], "w": 900}]
    n5, f5 = diag(blk_line)
    print(f"⑥ 차단(선)             : 조각 {n5}개 · 통행셀 {f5}")
    assert n5 > n0, "왼쪽 방을 가로로 막으면 조각이 늘어야 함"

    legacy = core.legacy_openings_to_shapes([[5000, 4000, 5000, 6000]])
    n6, f6 = diag(legacy)
    print(f"⑦ 구형식 openings 승격  : 조각 {n6}개 · 통행셀 {f6}")
    assert (n6, f6) == (n1, f1), "레거시 승격이 선 뚫기와 동일해야 함"

    print("\n전부 통과")


if __name__ == "__main__":
    main()
