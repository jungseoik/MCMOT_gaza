#!/usr/bin/env python3
"""
make_clip2.py — [추천2] DXF 압출 3D + z-buffer 멀티 카메라 클립.
추천1과 '같은 월드/같은 군중'을 공유하되:
  - 벽을 3D로 압출해 카메라별 정적 깊이버퍼를 굽고 → 사람이 벽 뒤로 가려진다(진짜 occlusion).
  - 카메라 2대(camA, camB)가 같은 장면을 다른 각도에서 촬영 → 출구 부근 겹침.
  - 통합 GT에 에이전트별 '카메라별 가시성'을 기록 → Multi-Camera ID 병합 검증용 정답.

출력(cad/synth/out/):
  clip2_camA.mp4 / clip2_camB.mp4
  clip2_multicam_gt.json   (두 카메라 파라미터 + 호모그래피 + 프레임별·카메라별 GT)
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


def main():
    os.makedirs(OUT, exist_ok=True)
    segs, ext = world.build_scene()
    cams = world.build_cameras(ext)
    sim = world.build_crowd()
    sprites = load_sprites(SPRITES)
    assert sprites, "스프라이트 없음"
    assign = assign_sprites(sim["n_agents"], sprites, seed=1)  # 카메라 간 동일 배정 → 동일 외형

    pos, present = sim["pos"], sim["present"]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")

    # 카메라별 배경 + 정적 깊이버퍼(벽 가림용)
    cam_ctx = {}
    for name, cam in cams.items():
        bg, zbuf = render_background(cam, segs, ext)
        cam_ctx[name] = (cam, bg, zbuf,
                         cv2.VideoWriter(os.path.join(OUT, f"clip2_{name}.mp4"),
                                         fourcc, sim["fps"], (world.W, world.H)))

    per_frame = []
    for f in range(sim["n_frames"]):
        t = f / sim["fps"]
        rec = {"frame": f, "t": round(t, 3), "cams": {}}
        for name, (cam, bg, zbuf, wr) in cam_ctx.items():
            frame, dets = composite_frame(bg, cam, pos[f], present[f],
                                          sprites, assign, scene_zbuf=zbuf)
            cv2.putText(frame, f"SYNTH {name}  t={t:5.2f}s  f={f:03d}", (16, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
            wr.write(frame)
            rec["cams"][name] = [{**d, "xy_m": [round(float(pos[f, d["id"], 0]), 3),
                                                round(float(pos[f, d["id"], 1]), 3)]}
                                 for d in dets]
        # ID 병합 GT: 양 카메라에서 동시에 '가시'인 에이전트 id
        visA = {d["id"] for d in rec["cams"]["camA"] if d["visible"]}
        visB = {d["id"] for d in rec["cams"]["camB"] if d["visible"]}
        rec["shared_visible_ids"] = sorted(visA & visB)
        per_frame.append(rec)

    for _, _, _, wr in cam_ctx.values():
        wr.release()

    # 공통 GT(미터공간)
    vel, speed = gt.kinematics(pos, sim["fps"])
    counts, dens = gt.zone_density(pos, world.ZONE_POLY, world.ZONE_AREA_M2)
    onset = gt.evac_onset(speed, sim["fps"])
    cross = gt.line_crossings(pos, world.VLINE, sim["fps"])
    summ = gt.summarize(sim, vel, speed, counts, dens, onset, cross,
                        world.ZONE_POLY, world.VLINE, world.ZONE_AREA_M2)

    out = {
        "method": "추천2: DXF 압출 3D + z-buffer 멀티캠(벽 가림 O, 카메라 2대)",
        "cameras": {n: c.extrinsics_dict() for n, c in cams.items()},
        "id_merge_note": "shared_visible_ids = 같은 프레임에서 두 카메라 모두에 보이는 동일 에이전트 id(=ID 병합 정답)",
        "ground_truth_summary": summ,
        "per_frame": per_frame,
    }
    jpath = os.path.join(OUT, "clip2_multicam_gt.json")
    with open(jpath, "w") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    nshare = sum(len(r["shared_visible_ids"]) for r in per_frame)
    print(f"[clip2] {OUT}/clip2_camA.mp4  {OUT}/clip2_camB.mp4")
    print(f"[clip2] {jpath}")
    print(f"[clip2] 두 카메라 공통가시 (frame·agent) 쌍 합계 = {nshare}  "
          f"(ID 병합 테스트용 겹침 존재)")


if __name__ == "__main__":
    main()
