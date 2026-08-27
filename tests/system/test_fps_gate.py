"""analyze_fps 게이트 — "1초를 N으로 나눈 간격으로 샘플링"이 실제로 되는가.

worker.py 는 cupy(DeepStream 컨테이너 전용)를 임포트해 여기선 못 올린다.
게이트 클래스만 소스에서 떼어 실행한다 — 로직 자체는 순수 파이썬이다.
"""
import statistics
from collections import Counter

_SRC = open("system/ingest_ds/worker.py").read()
_NS: dict = {}
exec(_SRC[_SRC.index("class _CamGate"):_SRC.index("class _OldestDropQueue")], _NS)
CamGate = _NS["_CamGate"]


def _sample(src_fps: float, want: float = 5.0, secs: float = 20.0):
    """소스 프레임이 등간격으로 들어올 때 게이트가 고르는 시각들."""
    g = CamGate("c", want)
    start, step = 1000.0, 1.0 / src_fps
    return [start + k * step
            for k in range(int(secs * src_fps))
            if g.due(start + k * step)]


class TestRate:
    def test_exact_target_rate(self):
        """소스가 몇 fps든 목표 개수를 정확히 뽑는다."""
        for src in (15, 24, 25, 30, 60):
            picked = _sample(src, want=5.0, secs=20.0)
            assert len(picked) == 100, f"{src}fps → {len(picked)}장 (100 기대)"

    def test_other_targets(self):
        for want, exp in ((1.0, 20), (2.0, 40), (10.0, 200)):
            assert len(_sample(30, want=want, secs=20.0)) == exp


class TestInterval:
    def test_median_is_the_grid(self):
        """간격의 중앙값은 1/N 초 — 격자가 유지된다."""
        for src in (15, 24, 25, 30, 60):
            p = _sample(src)
            d = [(b - a) * 1000 for a, b in zip(p, p[1:])]
            assert abs(statistics.median(d) - 200.0) < 10, f"{src}fps: {statistics.median(d)}"

    def test_jitter_bounded_by_source_frame_period(self):
        """지터는 소스 프레임 주기 이내 — 없는 프레임은 고를 수 없으니 불가피하다."""
        for src in (15, 24, 25, 30, 60):
            p = _sample(src)
            d = [(b - a) * 1000 for a, b in zip(p, p[1:])]
            period = 1000.0 / src
            assert max(d) - 200.0 <= period + 1e-6, f"{src}fps: 최대 {max(d)}ms"
            assert 200.0 - min(d) <= period + 1e-6, f"{src}fps: 최소 {min(d)}ms"

    def test_no_drift_over_time(self):
        """_next_due 를 누적 가산하므로 시간이 지나도 격자가 밀리지 않는다."""
        p = _sample(30, secs=120.0)
        assert abs((p[-1] - p[0]) - (len(p) - 1) * 0.2) < 0.05


class TestResync:
    def test_resyncs_after_long_gap(self):
        """스트림이 끊겼다 붙으면 밀린 만큼 몰아서 뽑지 않는다.

        실측 세션에 최대 2초 공백이 있었다. 재동기화가 없으면 복귀 직후
        10장이 한꺼번에 통과해 순간 fps 가 튄다.
        """
        g = CamGate("c", 5.0)
        t = 1000.0
        g.due(t)
        t += 2.0                      # 2초 공백
        assert g.due(t) is True       # 복귀 첫 프레임은 통과
        burst = sum(1 for k in range(1, 6) if g.due(t + k / 30.0))
        assert burst == 0, "공백 뒤 몰아치기가 발생했다"

    def test_fps_zero_passes_everything(self):
        g = CamGate("c", 0.0)
        assert all(g.due(1000.0 + k / 30.0) for k in range(10))
