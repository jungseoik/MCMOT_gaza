#!/usr/bin/env python3
"""
make_clip1.py — [추천1] 2.5D 컴포지팅 단일 카메라 테스트 클립.
도면 배경 + 실제 보행자 스프라이트를 원근/painter's order로 합성.
벽 가림 없음(평면 합성). 빠르고, 프레임마다 완전한 GT를 동봉한다.

출력(cad/synth/out/):
  clip1_camA.mp4         합성 테스트 영상
  clip1_camA_gt.json     카메라 파라미터 + 바닥 호모그래피 + 프레임별 GT(5대 정보)
  clip1_camA_plan.png    탑다운 배치도(카메라/구역/가상선/궤적)
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import cv2

import world, gt
from scene import render_background
from compositor import load_sprites, assign_sprites, composite_frame

OUT = os.path.join(os.path.dirname(__file__), "out")
SPRITES = os.path.join(os.path.dirname(__file__), "assets_sprites")


def draw_plan(path, segs, ext, cam, sim):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    from matplotlib.collections import LineCollection
    import matplotlib.patches as mp
    KRF = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    try:
        fm.fontManager.addfont(KRF)
        plt.rcParams["font.family"] = fm.FontProperties(fname=KRF).get_name()
    except Exception:
        pass
    xmin, ymin, xmax, ymax = ext
    fig = plt.figure(figsize=(12, 12 * (ymax - ymin) / (xmax - xmin)))
    ax = fig.add_axes([0.07, 0.07, 0.9, 0.88])
    lines = [[(s[0], s[1]), (s[2], s[3])] for s in segs]
    ax.add_collection(LineCollection(lines, colors="#bbb", linewidths=0.2))
    ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax); ax.set_aspect("equal")
    to_mm = lambda p: (xmin + p[0] * 1000, ymin + p[1] * 1000)
    # 구역/가상선/출구
    zp = np.array([to_mm(p) for p in world.ZONE_POLY])
    ax.add_patch(mp.Polygon(zp, closed=True, fc="#ff5a36", alpha=0.15, ec="#ff5a36"))
    (a, b) = world.VLINE
    am, bm = to_mm(a), to_mm(b)
    ax.plot([am[0], bm[0]], [am[1], bm[1]], "-", color="#0a0", lw=2.5)
    ex = to_mm(world.EXIT_XY); ax.plot(*ex, "*", color="#0a0", ms=18)
    cm = to_mm(cam.cam_m); ax.plot(*cm, "o", color="#06f", ms=12)
    ax.annotate("camA", cm, color="#06f", fontsize=12, weight="bold")
    # 궤적
    for i in range(sim["n_agents"]):
        p = sim["pos"][:, i, :]
        v = ~np.isnan(p[:, 0])
        ax.plot(xmin + p[v, 0] * 1000, ymin + p[v, 1] * 1000, "-", lw=0.8, alpha=0.7)
    ax.set_title("추천1 단일캠 배치 — camA / 구역(주황) / 가상선(초록) / 출구(별) / 궤적")
    fig.savefig(path, dpi=130); import matplotlib.pyplot as p2; p2.close(fig)


def main():
    os.makedirs(OUT, exist_ok=True)
    segs, ext = world.build_scene()
    cams = world.build_cameras(ext)
    cam = cams["camA"]
    sim = world.build_crowd()
    sprites = load_sprites(SPRITES)
    assert sprites, "스프라이트 없음 — extract_sprites.py 먼저 실행"
    assign = assign_sprites(sim["n_agents"], sprites, seed=1)

    bg, _ = render_background(cam, segs, ext)   # 평면 합성: zbuf 미사용

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vpath = os.path.join(OUT, "clip1_camA.mp4")
    writer = cv2.VideoWriter(vpath, fourcc, sim["fps"], (world.W, world.H))

    pos = sim["pos"]; present = sim["present"]
    frames_gt = []
    for f in range(sim["n_frames"]):
        frame, dets = composite_frame(bg, cam, pos[f], present[f], sprites, assign)
        # HUD: 타임스탬프
        t = f / sim["fps"]
        cv2.putText(frame, f"SYNTH camA  t={t:5.2f}s  f={f:03d}", (16, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        writer.write(frame)
        frames_gt.append({"frame": f, "t": round(t, 3),
                          "agents": [{**d, "xy_m": [round(float(pos[f, d["id"], 0]), 3),
                                                    round(float(pos[f, d["id"], 1]), 3)]}
                                     for d in dets]})
    writer.release()

    # GT 계산(미터공간)
    vel, speed = gt.kinematics(pos, sim["fps"])
    counts, dens = gt.zone_density(pos, world.ZONE_POLY, world.ZONE_AREA_M2)
    onset = gt.evac_onset(speed, sim["fps"])
    cross = gt.line_crossings(pos, world.VLINE, sim["fps"])
    summ = gt.summarize(sim, vel, speed, counts, dens, onset, cross,
                        world.ZONE_POLY, world.VLINE, world.ZONE_AREA_M2)

    gt_json = {
        "method": "추천1: 2.5D 컴포지팅(단일캠, 벽 가림 없음)",
        "camera": cam.extrinsics_dict(),
        "scenario": {"exit_xy_m": world.EXIT_XY, "spawn_rect": world.SPAWN_RECT,
                     "n_agents": sim["n_agents"], "fps": sim["fps"]},
        "ground_truth_summary": summ,
        "per_frame": frames_gt,
    }
    jpath = os.path.join(OUT, "clip1_camA_gt.json")
    with open(jpath, "w") as fp:
        json.dump(gt_json, fp, ensure_ascii=False, indent=1)

    draw_plan(os.path.join(OUT, "clip1_camA_plan.png"), segs, ext, cam, sim)
    print(f"[clip1] {vpath}")
    print(f"[clip1] {jpath}")
    print(f"[clip1] peak_count={counts.max()} peak_density={dens.max():.2f}/m^2 "
          f"unique_exits={int((cross>=0).sum())}/{sim['n_agents']}")


if __name__ == "__main__":
    main()
