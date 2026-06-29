#!/usr/bin/env python3
"""
gt.py — 시뮬 궤적(미터)에서 North Star 5대 추출정보의 '정답(GT)'을 계산.
모두 평면도 미터좌표계 위에서 산출(카메라 독립). 테스트 대상 엔진의 출력과
대조하면 호모그래피/속도/밀도/이벤트 정확도를 정량 평가할 수 있다.
"""
import numpy as np


def kinematics(pos, fps):
    """pos(F,N,2) m → vel(F,N,2) m/s, speed(F,N) m/s. 중앙차분."""
    F, N, _ = pos.shape
    vel = np.full((F, N, 2), np.nan)
    for i in range(N):
        p = pos[:, i, :]
        valid = ~np.isnan(p[:, 0])
        idx = np.where(valid)[0]
        if len(idx) >= 2:
            g = np.gradient(p[idx], axis=0) * fps
            vel[idx, i, :] = g
    speed = np.linalg.norm(vel, axis=2)
    return vel, speed


def point_in_poly(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]; xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def zone_density(pos, poly_m, area_m2):
    """구역(폴리곤) 내 프레임별 재실자 수 + 밀도(명/m²)."""
    F, N, _ = pos.shape
    counts = np.zeros(F, int)
    for f in range(F):
        c = 0
        for i in range(N):
            if not np.isnan(pos[f, i, 0]) and point_in_poly(pos[f, i], poly_m):
                c += 1
        counts[f] = c
    dens = counts / area_m2
    return counts, dens


def evac_onset(speed, fps, thr=0.3, sustain_s=0.5):
    """에이전트별 피난 개시 프레임: 속도가 thr 이상으로 sustain 연속 유지된 최초 시점."""
    F, N = speed.shape
    need = max(1, int(sustain_s * fps))
    onset = np.full(N, -1, int)
    for i in range(N):
        run = 0
        for f in range(F):
            s = speed[f, i]
            if not np.isnan(s) and s >= thr:
                run += 1
                if run >= need:
                    onset[i] = f - need + 1
                    break
            else:
                run = 0
    return onset


def line_crossings(pos, line, fps):
    """가상선 통과: 각 에이전트가 선분을 처음 넘는 프레임. line=((ax,ay),(bx,by)) m.
    반환 cross_frame(N) (-1=미통과). 부호 변경(선의 한쪽→반대쪽)으로 판정."""
    (ax, ay), (bx, by) = line
    dx, dy = bx - ax, by - ay

    def side(p):
        return (p[0] - ax) * dy - (p[1] - ay) * dx

    def seg_overlap(p):  # 선분 범위 안쪽 통과만 인정(투영 t∈[0,1])
        t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy + 1e-12)
        return -0.1 <= t <= 1.1

    F, N, _ = pos.shape
    cross = np.full(N, -1, int)
    for i in range(N):
        prev = None
        for f in range(F):
            if np.isnan(pos[f, i, 0]):
                continue
            s = side(pos[f, i])
            if prev is not None and prev * s < 0 and seg_overlap(pos[f, i]):
                cross[i] = f          # 방향 무관 첫 통과
                break
            prev = s
    return cross


def summarize(sim, vel, speed, counts, dens, onset, cross, zone_poly, line, area_m2):
    fps = sim["fps"]
    uniq = int((cross >= 0).sum())
    return {
        "fps": fps, "n_frames": sim["n_frames"], "n_agents": sim["n_agents"],
        "zone_poly_m": zone_poly, "zone_area_m2": area_m2,
        "virtual_line_m": [list(line[0]), list(line[1])],
        "exit_xy_m": sim["exit_xy"],
        "category2_floor_coords": "per-frame pos(m) 제공(아래 frames)",
        "category3_kinematics": {
            "peak_zone_mean_speed_mps": round(float(np.nanmax(
                [np.nanmean(speed[f][counts_mask(sim['pos'], zone_poly, f)]) if counts[f] else np.nan
                 for f in range(sim['n_frames'])])), 3) if counts.max() else None,
        },
        "category4_density": {
            "peak_count": int(counts.max()),
            "peak_density_per_m2": round(float(dens.max()), 3),
        },
        "category5_events": {
            "evac_onset_frame_per_agent": onset.tolist(),
            "line_cross_frame_per_agent": cross.tolist(),
            "unique_exits_through_line": uniq,
        },
    }


def counts_mask(pos, poly, f):
    N = pos.shape[1]
    m = np.zeros(N, bool)
    for i in range(N):
        if not np.isnan(pos[f, i, 0]) and point_in_poly(pos[f, i], poly):
            m[i] = True
    return m
