#!/usr/bin/env python3
"""
crowd.py — 도면 좌표(미터) 위에서 피난 보행자 궤적을 시뮬레이션한다.
순수 미터공간 산출이라 카메라와 독립이며, 이것이 5대 추출정보의 '정답(GT)'이 된다.

간이 social-force: 출구로 향하는 추진력 + 이웃 분리력(병목/밀도 생성).
출구 도달 시 제거. 시차 출발(피난 개시 GT)을 부여한다.
"""
import numpy as np


def simulate(n_agents=10, fps=15, duration_s=22.0,
             spawn_rect=(5, 5, 22, 20), exit_xy=(26, 27),
             speed_mean=1.25, speed_std=0.18, agent_r=0.45,
             onset_spread_s=5.0, seed=0):
    """반환 dict: fps, n_frames, n_agents, pos(F,N,2) m(NaN=부재), present(F,N) bool,
       speed_pref(N), onset_frame(N), exit_xy, spawn_rect."""
    rng = np.random.default_rng(seed)
    F = int(round(duration_s * fps))
    x0, y0, x1, y1 = spawn_rect
    start = np.column_stack([rng.uniform(x0, x1, n_agents),
                             rng.uniform(y0, y1, n_agents)])
    v_pref = rng.normal(speed_mean, speed_std, n_agents).clip(0.7, 1.9)
    onset = (rng.uniform(0, onset_spread_s, n_agents) * fps).astype(int)
    exit_p = np.array(exit_xy, float)

    pos = np.full((F, n_agents, 2), np.nan)
    present = np.zeros((F, n_agents), bool)
    cur = start.copy()
    done = np.zeros(n_agents, bool)
    dt = 1.0 / fps

    for f in range(F):
        active = (f >= onset) & (~done)
        # 분리력(이웃 반발)
        for i in np.where(active)[0]:
            to_exit = exit_p - cur[i]
            d = np.linalg.norm(to_exit)
            if d < 0.6:                      # 출구 도달 → 통과 완료
                done[i] = True
                continue
            desired = to_exit / d * v_pref[i]
            # 이웃 분리
            sep = np.zeros(2)
            for j in np.where(active)[0]:
                if j == i:
                    continue
                diff = cur[i] - cur[j]
                dist = np.linalg.norm(diff)
                if 1e-3 < dist < 2 * agent_r + 0.6:
                    sep += diff / dist * (2 * agent_r + 0.6 - dist) * 1.6
            vel = desired + sep
            sp = np.linalg.norm(vel)
            if sp > v_pref[i] * 1.4:         # 속도 상한
                vel = vel / sp * v_pref[i] * 1.4
            cur[i] = cur[i] + vel * dt
        for i in np.where((f >= onset) & (~done))[0]:
            pos[f, i] = cur[i]; present[f, i] = True

    return dict(fps=fps, n_frames=F, n_agents=n_agents, pos=pos, present=present,
                speed_pref=v_pref, onset_frame=onset, exit_xy=list(exit_xy),
                spawn_rect=list(spawn_rect))
