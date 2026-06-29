#!/usr/bin/env python3
"""
camera.py — 핀홀 카메라 모델 (cctv_synth.py의 투영 수학을 재사용/확장).

좌표계
  - 월드: DXF extmin을 원점으로 한 mm 좌표 (3D, z=바닥 0).
  - 로컬 미터: 평면도 남서코너=(0,0) m. world_mm = extmin + local_m*1000.
핵심 산출
  - project(P_mm): 월드 3D점 → 픽셀 + 카메라공간 깊이(mm).
  - H_world2img / H_img2world: 바닥평면(z=0) 호모그래피 3x3.
    → 테스트 대상 엔진이 "픽셀→도면좌표" 변환을 검증할 때 쓰는 정답(GT) 행렬.
"""
import math
import numpy as np


class Camera:
    def __init__(self, ext, cam_m, cam_h_m, hfov_deg, W, H,
                 look_m=None, tilt_deg=None, yaw_deg=None):
        """ext=(xmin,ymin,xmax,ymax) mm. cam_m=(x,y) 로컬미터. 높이 cam_h_m.
        조준은 (look_m) 또는 (yaw_deg+tilt_deg) 중 하나로 지정."""
        self.ext = ext
        self.W, self.H = W, H
        self.hfov = hfov_deg
        xmin, ymin = ext[0], ext[1]
        self.Cx = xmin + cam_m[0] * 1000.0
        self.Cy = ymin + cam_m[1] * 1000.0
        self.Cz = cam_h_m * 1000.0
        self.cam_m = tuple(cam_m)
        self.cam_h_m = cam_h_m

        if look_m is not None:
            lx = xmin + look_m[0] * 1000.0
            ly = ymin + look_m[1] * 1000.0
            yaw = math.atan2(ly - self.Cy, lx - self.Cx)
            horiz = math.hypot(lx - self.Cx, ly - self.Cy)
            tilt = math.atan2(self.Cz, horiz)        # 바닥점을 보는 하방각
        else:
            yaw = math.radians(yaw_deg)
            tilt = math.radians(tilt_deg)
        self.yaw, self.tilt = yaw, tilt

        # 광축 방향: 수평으로 yaw, 아래로 tilt
        self.fwd = np.array([math.cos(yaw) * math.cos(tilt),
                             math.sin(yaw) * math.cos(tilt),
                             -math.sin(tilt)])
        self.fwd /= np.linalg.norm(self.fwd)
        world_up = np.array([0, 0, 1.0])
        self.right = np.cross(self.fwd, world_up); self.right /= np.linalg.norm(self.right)
        self.up = np.cross(self.right, self.fwd)
        self.C = np.array([self.Cx, self.Cy, self.Cz])

        self.fx = (W / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
        self.fy = self.fx
        self._build_homography()

    def project(self, P, near=1.0):
        """P:(N,3) mm → (px:(N,2), depth:(N,)). 카메라 뒤/near이내는 NaN."""
        P = np.atleast_2d(np.asarray(P, float))
        rel = P - self.C
        xc = rel @ self.right
        yc = rel @ self.up
        zc = rel @ self.fwd
        px = np.full((len(P), 2), np.nan)
        valid = zc > near
        px[valid, 0] = self.W / 2.0 + self.fx * xc[valid] / zc[valid]
        px[valid, 1] = self.H / 2.0 - self.fy * yc[valid] / zc[valid]
        return px, zc

    def _build_homography(self):
        """바닥(z=0) world_mm(X,Y) ↔ pixel 호모그래피."""
        r, u, f = self.right, self.up, self.fwd
        C = self.C
        # [xc;yc;zc] = M2 @ [X;Y;1]   (z=0 평면)
        M2 = np.array([
            [r[0], r[1], -(r @ C)],
            [u[0], u[1], -(u @ C)],
            [f[0], f[1], -(f @ C)],
        ])
        Kp = np.array([
            [self.fx, 0,        self.W / 2.0],
            [0,       -self.fy, self.H / 2.0],
            [0,       0,        1.0],
        ])
        self.H_world2img = Kp @ M2
        self.H_img2world = np.linalg.inv(self.H_world2img)

    def img_to_floor_m(self, px, py):
        """픽셀 → 바닥 로컬미터 좌표 (호모그래피 역변환). 검증용."""
        v = self.H_img2world @ np.array([px, py, 1.0])
        Xmm, Ymm = v[0] / v[2], v[1] / v[2]
        return ((Xmm - self.ext[0]) / 1000.0, (Ymm - self.ext[1]) / 1000.0)

    def extrinsics_dict(self):
        return {
            "cam_xy_m": [round(self.cam_m[0], 3), round(self.cam_m[1], 3)],
            "cam_height_m": round(self.cam_h_m, 3),
            "yaw_deg": round(math.degrees(self.yaw), 2),
            "tilt_down_deg": round(math.degrees(self.tilt), 2),
            "hfov_deg": self.hfov, "W": self.W, "H": self.H,
            "fx_px": round(self.fx, 2), "fy_px": round(self.fy, 2),
            "H_world_mm_to_img": self.H_world2img.tolist(),
            "H_img_to_world_mm": self.H_img2world.tolist(),
        }
