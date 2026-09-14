"""완전 zip 파이프라인 실행 — zip의 트래커(BoostTrack++)·글로벌ID·grid 시각화를
**그대로** 돌린다. zip에 없는 것은 검출기·ReID 임베더뿐(모델 패키지 미포함·게이트),
그마저 우리와 동일 모델(YOLO26-L v6.3 + CLIP-ReID)이라 우리 엔진을 zip 인터페이스
뒤에 어댑터로 끼운다. 즉 트래킹·크로스카메라ID·렌더는 100% zip 코드.

  python tools/gid_compare/zip_native.py --scenario scenario_01 --zip-src <zip>/src

출력(results/gid_compare/<scenario>/zip_native/):
  grid.mp4          zip 고유 시각화 — 5-cam 타일, 같은 사람=같은 색·G-<n> (지도 없음)
  <cam>.mp4         카메라별 오버레이
  preds/, global_ids.json, run_summary.json   zip 원본 산출물
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import model_zoo                                             # noqa: E402
from system.tracking.analyzer import AnalyzerThread          # noqa: E402 (정적 _frame_dets 재사용)

REHEARSAL = ROOT / "media/vsource/cj/rehearsal/rehearsal.json"


class _Box:
    """zip to_schema_detection 이 읽는 최소 계약(.bbox/.class_id/.conf)."""
    __slots__ = ("bbox", "class_id", "conf", "class_name")

    def __init__(self, bbox, conf):
        self.bbox = bbox
        self.class_id = 0            # person
        self.conf = conf
        self.class_name = "person"


class DetectorAdapter:
    """우리 model_zoo 검출기 → zip detector 계약(detect(frame)->list[_Box])."""

    def __init__(self, det):
        self._d = det

    def detect(self, frame: np.ndarray):
        pred, ref = self._d.detect_frame(frame)
        h, w = frame.shape[:2]
        scale_r = min(ref.shape[2] / h, ref.shape[3] / w)
        xyxy, scores = AnalyzerThread._frame_dets(pred, scale_r)   # 원본 px xyxy + 점수
        return [_Box((float(a), float(b), float(c), float(d)), float(s))
                for (a, b, c, d), s in zip(xyxy, scores)]


class ReIDAdapter:
    """우리 ReID 모듈(0~255 RGB 배치 계약) → zip ReIDBackend(embed(crops)->(N,D) 정규화)."""

    def __init__(self, model, crop_wh, dim=768):
        self._m = model
        self._cw, self._ch = int(crop_wh[0]), int(crop_wh[1])
        self._dim = dim

    def embed(self, crops_bgr: list[np.ndarray]) -> np.ndarray:
        if not crops_bgr:
            return np.zeros((0, self._dim), np.float32)
        buf = np.empty((len(crops_bgr), 3, self._ch, self._cw), np.float32)
        for i, c in enumerate(crops_bgr):
            if c is None or c.size == 0:
                buf[i] = 0.0
                continue
            c = cv2.cvtColor(c, cv2.COLOR_BGR2RGB)
            c = cv2.resize(c, (self._cw, self._ch), interpolation=cv2.INTER_LINEAR)
            buf[i] = c.transpose(2, 0, 1)
        t = torch.from_numpy(buf).cuda()
        with torch.no_grad():
            e = self._m(t)
        e = F.normalize(e, dim=-1)
        return e.detach().cpu().numpy().astype(np.float32)

    @property
    def embed_dim(self) -> int:
        return self._dim


def _h264(path: Path):
    tmp = str(path) + ".t.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
                        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", "-an", tmp], capture_output=True)
    if r.returncode == 0:
        Path(tmp).replace(path)
    elif Path(tmp).exists():
        Path(tmp).unlink()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="scenario_01")
    ap.add_argument("--profile", default="yolo26_clipreid")
    ap.add_argument("--zip-src", required=True, help="<multi_camera_tracking>/src")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--grid-width", type=int, default=1600)
    args = ap.parse_args()

    zip_src = Path(args.zip_src)
    if str(zip_src) not in sys.path:
        sys.path.insert(0, str(zip_src))
    from pia_tracking import load_config                      # noqa: E402
    from pia_tracking.runners import RunOptions, run_grid, run_multi  # noqa: E402

    # 우리 모델(zip과 동일 스택)을 zip 인터페이스 뒤에 끼운다
    prof = model_zoo.resolve(args.profile)
    detector = DetectorAdapter(model_zoo.build_detector(prof))
    reid_model, crop_wh = model_zoo.build_reid(prof)
    reid = ReIDAdapter(reid_model, crop_wh)
    print(f"[zip-native] profile={prof.label}  crop={crop_wh}  (zip tracker+fusion+grid)")

    # zip 원본 설정(임계·percam_norm·checkpoint 등 그대로)
    cfg_path = zip_src.parent / "config" / "tracking_general.yaml"
    config = load_config(cfg_path, args.device)

    reh = json.loads(REHEARSAL.read_text(encoding="utf-8"))
    sc = next(s for s in reh["scenarios"] if s["id"] == args.scenario)
    videos = [REHEARSAL.parent / st["file"] for st in sc["streams"]]
    print(f"[zip-native] {len(videos)} cams: {[v.name for v in videos]}")

    out_dir = ROOT / "results/gid_compare" / args.scenario / "zip_native"
    out_dir.mkdir(parents=True, exist_ok=True)

    # final_labels=True → 카메라별 MP4·grid 모두 최종(정정 반영) G-id 로 렌더
    opts = RunOptions(max_frames=args.max_frames, render_video=True, final_labels=True,
                      grid_width=args.grid_width)
    print("[zip-native] run_multi (zip 트래커+글로벌ID) ...")
    run_multi(videos, config=config, config_path=cfg_path, device=args.device,
              detector=detector, reid=reid, out_dir=out_dir, opts=opts)

    print("[zip-native] run_grid (zip 고유 grid 시각화) ...")
    grid_opts = RunOptions(max_frames=args.max_frames, grid_width=args.grid_width)
    run_grid(videos, out_dir=out_dir, opts=grid_opts)

    for mp4 in list(out_dir.glob("*.mp4")):
        _h264(mp4)
    gid = json.loads((out_dir / "global_ids.json").read_text())
    print(f"[zip-native] done -> {out_dir}")
    print(f"[zip-native] identities={gid.get('n_identities')} "
          f"multi_camera={gid.get('n_multi_camera')} "
          f"minted={gid.get('minted')} matched={gid.get('matched')} revised={gid.get('revised')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
