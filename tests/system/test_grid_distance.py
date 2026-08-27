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


class TestWholePlanZones:
    """구역을 도면 전체에 타일처럼 까는 실제 사용 방식."""

    def _tiles(self, side: int):
        step = 100.0 / side
        out = []
        for i in range(side):
            for j in range(side):
                x0, y0 = i * step / M_PER_PX, j * step / M_PER_PX
                x1, y1 = (i + 1) * step / M_PER_PX, (j + 1) * step / M_PER_PX
                out.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        return out

    def test_tiled_zones_still_track_their_own_distance(self):
        """타일 구역은 각자 자기 위치의 거리를 낸다 — 서로 구분돼야 IDR 이 뜻을 갖는다."""
        from system.spatial.grid import clear_distance_cache
        clear_distance_cache()
        ds = [zone_grid_distance_m(z, ORIGIN, W, H, M_PER_PX, CELL_M)
              for z in self._tiles(5)]
        assert min(ds) < 20 and max(ds) > 120, f"구역별 거리가 안 벌어진다: {ds}"
        # 5×5 를 (i,j) 로 읽으면 대각 대칭이라 고유값은 상삼각 개수 15 다.
        # 그보다 적으면 값이 뭉갠 것이고, 많으면 대칭(=회전 불변)이 깨진 것.
        assert len(set(round(d, 1) for d in ds)) == 15, f"{sorted(set(round(d,1) for d in ds))}"

    def test_tiled_zones_are_symmetric(self):
        """(i,j) 와 (j,i) 구역의 거리가 같아야 한다 — 대각 대칭.

        4방향 BFS 시절에도 이건 성립했다. 문제는 대칭이 아니라 **대각 방향의
        값 자체**가 부풀었다는 것이었다(test_diagonal_not_inflated_by_manhattan).
        """
        from system.spatial.grid import clear_distance_cache
        clear_distance_cache()
        side = 5
        ds = self._tiles(side)
        vals = [zone_grid_distance_m(z, ORIGIN, W, H, M_PER_PX, CELL_M) for z in ds]
        grid = [[vals[i * side + j] for j in range(side)] for i in range(side)]
        for i in range(side):
            for j in range(side):
                assert abs(grid[i][j] - grid[j][i]) < 1e-9, f"({i},{j}) 비대칭"

    def test_cache_does_not_change_values(self):
        """캐시를 켜도 끈 것과 같은 값이 나와야 한다."""
        from system.spatial.grid import clear_distance_cache
        zones = self._tiles(3)
        clear_distance_cache()
        cold = [zone_grid_distance_m(z, ORIGIN, W, H, M_PER_PX, CELL_M) for z in zones]
        warm = [zone_grid_distance_m(z, ORIGIN, W, H, M_PER_PX, CELL_M) for z in zones]
        assert cold == warm

    def test_fine_grid_many_zones_is_fast(self):
        """0.5 m 셀 × 25구역 — 세션 시작에 쓰이므로 사람이 기다릴 만해야 한다.

        구역마다 거리장을 다시 돌리고 전체 셀을 점-다각형 판정하던 때는
        1.6초였다(경보 3개면 4.8초).
        """
        import time
        from system.spatial.grid import clear_distance_cache
        zones = self._tiles(5)
        clear_distance_cache()
        t = time.time()
        for z in zones:
            zone_grid_distance_m(z, ORIGIN, W, H, M_PER_PX, 0.5)
        el = time.time() - t
        assert el < 0.6, f"{el:.2f}s — 너무 느리다"

    def test_single_giant_zone_averages_the_area(self):
        """도면 전체를 한 구역으로 잡으면 D 는 '그 구역까지의 거리'가 아니라
        면적 평균이 된다 — 값이 틀린 건 아니지만 IDR 의 구역 구분이 사라진다."""
        from system.spatial.grid import clear_distance_cache
        clear_distance_cache()
        whole = [(0.0, 0.0), (W, 0.0), (W, H), (0.0, H)]
        d = zone_grid_distance_m(whole, ORIGIN, W, H, M_PER_PX, CELL_M)
        assert d > math.hypot(100, 100) / 2, f"면적 평균이어야 한다: {d}"
        assert d > _d(30, 30), "작은 구역보다는 멀게 나온다"
