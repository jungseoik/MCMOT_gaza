"""IDR 분자 D(zone, origin) 가 실제 보행거리를 근사하는가.

`IDR_e = D(e, S_origin) / max(t_e,start − t_alarm, ε)` 이므로 D 의 오차가
그대로 IDR 오차가 된다.

과거 버그 — 4방향 BFS 라 홉 수가 곧 **맨해튼 거리**였다. 대각 방향 구역이
최대 √2(+41%) 부풀고, **도면을 45° 돌리면 IDR 이 바뀌었다.**
"""
import math

from system.spatial.grid import bfs_distances, zone_grid_distance_m

M_PER_PX = 0.05          # 1px = 5cm
CELL_M = 1.0
W = H = 2000.0           # 100m × 100m
ORIGIN = (0.0, 0.0)


def _zone(x_m: float, y_m: float, size_m: float = 2.0):
    cx, cy = x_m / M_PER_PX, y_m / M_PER_PX
    h = size_m / M_PER_PX / 2
    return [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)]


def _d(x_m: float, y_m: float) -> float:
    return zone_grid_distance_m(_zone(x_m, y_m), ORIGIN, W, H, M_PER_PX, CELL_M)


class TestDiagonalCost:
    def test_diagonal_step_costs_sqrt2(self):
        d = bfs_distances(10, 10, (0, 0))
        assert abs(d[(1, 1)] - math.sqrt(2)) < 1e-9, "대각이 1이면 맨해튼이 된다"
        assert abs(d[(0, 1)] - 1.0) < 1e-9
        assert abs(d[(3, 3)] - 3 * math.sqrt(2)) < 1e-9

    def test_returns_cell_distance_not_hops(self):
        d = bfs_distances(5, 5, (0, 0))
        assert isinstance(d[(1, 1)], float)


class TestApproximatesEuclidean:
    """장애물이 없는 지금은 유클리드가 참값이다."""

    ANGLES = [(30, 0), (30, 5), (30, 10), (30, 20), (30, 30),
              (20, 30), (10, 30), (0, 30), (40, 15)]

    def test_within_10_percent_at_every_angle(self):
        for x, y in self.ANGLES:
            eu = math.hypot(x, y)
            err = abs(_d(x, y) - eu) / eu
            assert err < 0.10, f"({x},{y}) 오차 {err*100:.1f}%"

    def test_diagonal_not_inflated_by_manhattan(self):
        """45° 방향이 √2 만큼 부풀지 않는다 — 이게 원래 버그였다."""
        eu = math.hypot(30, 30)                    # 42.43m
        d = _d(30, 30)
        assert d < eu * 1.10, f"맨해튼이면 59m 가 나온다 (실제 {d:.1f}m)"

    def test_rotation_invariance(self):
        """도면 방향이 IDR 을 바꾸면 안 된다.

        같은 거리(30m)의 구역을 여러 각도에 두고 D 를 재면 서로 비슷해야 한다.
        맨해튼이면 0°에서 30m, 45°에서 42m 로 40% 벌어졌다.
        """
        r = 30.0
        vals = [_d(r * math.cos(math.radians(a)), r * math.sin(math.radians(a)))
                for a in (0, 15, 30, 45, 60, 75, 90)]
        spread = (max(vals) - min(vals)) / min(vals)
        assert spread < 0.12, f"각도별 편차 {spread*100:.1f}% — {[round(v,1) for v in vals]}"
