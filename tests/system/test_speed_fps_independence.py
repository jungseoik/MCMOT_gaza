"""속도 계산이 샘플링 fps 에 좌우되지 않는가.

analyze_fps 를 5에서 바꾸면 지표가 달라지느냐는 물음에 대한 근거.
두 가지가 함께 성립해야 fps 독립이다:
  ① 윈도가 **시간 기준**(1.0초)이다 — 프레임 개수면 fps 마다 폭이 달라진다
  ② dt 가 **실제 ts 차이**다 — 1/fps 를 가정하면 지터가 그대로 오차가 된다
"""
import random
import statistics

from system.metrics.engine import MetricsEngine
from tests.system.test_session import M_PER_PX, make_cam, make_site, tr


def _read_at(fps: float, xfun, at_sec: float):
    """t=at_sec 이 정확히 샘플 시각이 되도록 넣고, 그 순간의 속도를 읽는다."""
    eng = MetricsEngine(make_site(), [make_cam()])

    for k in range(int(at_sec * fps) + 1):
        t = 1000.0 + k / fps
        eng.on_tracks("cam01", t, [tr("cam01", 1, xfun(t - 1000.0), 500.0, t)])
    objs = eng.snapshot().objects
    st = list(eng._objects.values())[0]
    return objs[0].speed_mps, st.hist[-1][0] - st.hist[0][0]


RATES = (2, 5, 10, 15, 30)


class TestWindowIsTimeBased:
    def test_window_sec_not_frame_count(self):
        eng = MetricsEngine(make_site(), [make_cam()])
        assert eng.window_sec == 1.0
        # 프레임 개수 상한(maxlen)으로 자르면 fps 마다 폭이 달라진다

        for k in range(200):
            t = 1000.0 + k * 0.01           # 100fps 로 2초
            eng.on_tracks("cam01", t, [tr("cam01", 1, 100.0 + k, 500.0, t)])
        st = list(eng._objects.values())[0]
        assert st.hist.maxlen is None, "개수 상한이 있으면 시간 기준이 아니다"
        assert (st.hist[-1][0] - st.hist[0][0]) <= 1.0 + 1e-9


class TestConstantVelocity:
    def test_same_speed_at_every_rate(self):
        """등속이면 어떤 fps 로 샘플링해도 정확히 같은 값."""
        xf = lambda s: 100.0 + (1.4 / M_PER_PX) * s
        vals = [_read_at(f, xf, 5.0)[0] for f in RATES]
        for v in vals:
            assert abs(v - 1.4) < 1e-6, f"{vals}"


class TestAcceleratingMotion:
    def test_same_window_average_at_every_rate(self):
        """가속 중이어도 같은 시각·같은 윈도면 같은 값이 나온다.

        1초 윈도는 순간속도가 아니라 **구간 평균**을 잰다(등가속 0.25m/s²,
        t=5s 에서 [4,5] 평균 = 1.125 m/s). 그 값이 fps 와 무관해야 한다.
        """
        A = 0.25 / M_PER_PX
        xf = lambda s: 100.0 + 0.5 * A * s * s
        for f in RATES:
            v, dt = _read_at(f, xf, 5.0)
            assert abs(dt - 1.0) < 1e-9, f"{f}fps 윈도 dt={dt}"
            assert abs(v - 1.125) < 1e-6, f"{f}fps → {v}"


class TestNoiseIsNotReducedByHigherFps:
    def test_endpoints_only_so_noise_persists(self):
        """fps 를 올려도 속도 잡음은 안 줄어든다 — 윈도 **양끝점만** 쓰기 때문.

        중간 표본은 평균에 쓰이지 않는다. 잡음을 줄이려면 fps 가 아니라
        윈도를 늘리거나 평활을 넣어야 한다는 뜻이라, 기대치를 못 박아 둔다.
        """
        def sd(fps):
            random.seed(11)
            eng = MetricsEngine(make_site(), [make_cam()])
            out = []
            for k in range(int(8 * fps)):
                t = 1000.0 + k / fps
                x = 100.0 + (1.4 / M_PER_PX) * (t - 1000.0) + random.uniform(-3, 3)
                eng.on_tracks("cam01", t, [tr("cam01", 1, x, 500.0, t)])
                if t - 1000.0 > 1.2:
                    objs = eng.snapshot().objects
                    if objs and objs[0].speed_mps is not None:
                        out.append(objs[0].speed_mps)
            return statistics.pstdev(out)

        s5, s30 = sd(5), sd(30)
        assert s30 > s5 * 0.5, ("fps 를 6배 올려도 잡음이 절반 아래로는 안 준다 — "
                                f"5fps σ={s5:.4f}, 30fps σ={s30:.4f}")
