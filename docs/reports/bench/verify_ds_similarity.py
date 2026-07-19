#!/usr/bin/env python
"""기존 ffmpeg(cv2)+직렬 TRT 경로 vs DeepStream(system/ingest_ds) 경로의
e2e 단계별 출력 유사도 정량 검증.

두 경로는 전처리가 다르다:
  기존   : cv2 디코드(BGR, 원본 해상도) → dataset.preproc(cv2 uint8 bilinear
           letterbox) → TRT 검출 → CPU crop(cv2) ReID → BoostTrack
  DS     : NVDEC 디코드 → nvstreammux 1920x1080 stretch → RGBA → GPU float
           bilinear letterbox → TRT(dynamic batch) 검출 → GPU crop ReID
           → BoostTrack

사용 (모두 레포 루트 기준):

  # 1) 기존 경로 덤프 (호스트 conda boosttrack, GPU1)
  CUDA_VISIBLE_DEVICES=1 conda run -n boosttrack python \
      docs/reports/bench/verify_ds_similarity.py baseline \
      --video assets/sample1.mp4 --out /tmp/verify/base --max-age 50 --dump-frames 5

  # 2) DeepStream 경로 덤프 (컨테이너 — worker.py --verify-dump)
  docker run --rm --network host --gpus device=1 -v "$PWD:/workspace" \
      -v /tmp/verify:/verify -w /workspace macs-deepstream:9.0 \
      python3 -m system.ingest_ds.worker --cams /verify/cams_file.json \
      --verify-dump /verify/ds --lossless --dump-frames 5 --max-age 50
  # cams_file.json: [{"cam_id":"sample1","rtsp":"file:///workspace/assets/sample1.mp4","analyze_fps":0}]
  # analyze_fps=0 → fps 게이트 비활성(전 프레임), --lossless → drop 없이 EOS까지

  # 3) 비교 리포트
  conda run -n boosttrack python docs/reports/bench/verify_ds_similarity.py compare \
      --base /tmp/verify/base --ds /tmp/verify/ds/sample1 --json /tmp/verify/metrics.json

npz 스키마(프레임별 det_{seq:06d}.npz, 양쪽 공통):
  pred      (N,5)  검출 [x1,y1,x2,y2,conf] — 모델 입력(896x1600) 좌표
  scale_r   float  letterbox 스케일 (pred/scale_r = 트래킹 px)
  src_w/h   int    원본 카메라 해상도
  mux_w/h   int    트래킹 좌표계 해상도 (기존 경로 = src, DS = 1920x1080)
  emb_bbox  (M,4)  임베더에 전달된 bbox (트래킹 px)
  emb       (M,D)  L2 정규화 임베딩
  targets   (K,6)  트래커 원출력 [x1,y1,x2,y2,id,conf] (트래킹 px)
frame_{seq:06d}.npy: 기존 경로 = BGR(H,W,3) 원본 px / DS = RGBA(1080,1920,4) mux px
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

import numpy as np

_REPO = str(Path(__file__).resolve().parents[3])
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


# ─────────────────────────────────────────────── baseline (기존 경로 덤프)

def run_baseline(args: argparse.Namespace) -> None:
    import cv2
    import torch
    from tqdm import tqdm

    from dataset import preproc
    from default_settings import GeneralSettings
    from src.inference_gpu import GPUEmbeddingComputer
    from src.inference_trt import TRTDetector, TRTReID
    from tracker.boost_track import BoostTrack, KalmanBoxTracker

    # system/ingest_ds/worker.py 와 동일한 트래커 전역 설정
    GeneralSettings.values["dataset"] = "mot20"
    GeneralSettings.values["test_dataset"] = True
    GeneralSettings.values["use_embedding"] = True
    GeneralSettings.values["use_ecc"] = False       # 고정 CCTV — worker와 동일
    GeneralSettings.values["det_thresh"] = args.det_thresh

    detector = TRTDetector(args.yolox_engine)
    KalmanBoxTracker.count = 0
    tracker = BoostTrack(per_instance_ids=True, max_age=args.max_age)

    trt_reid = TRTReID(args.reid_engine)
    embedder = GPUEmbeddingComputer(trt_reid, crop_size=(128, 384))
    rec: dict = {}

    def _recorded(img, bbox, tag):
        emb = embedder.compute_embedding(img, bbox, tag)
        rec["bbox"] = np.asarray(bbox, np.float32).copy()
        rec["emb"] = np.asarray(emb).copy()
        return emb

    assert tracker.embedder is not None
    tracker.embedder.compute_embedding = _recorded
    tracker.embedder.model = trt_reid

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise SystemExit(f"영상을 열 수 없음: {args.video}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    os.makedirs(args.out, exist_ok=True)

    seq = 0
    pbar = tqdm(total=total, desc="baseline", unit="f")
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        seq += 1
        if args.max_frames and seq > args.max_frames:
            break
        h, w = frame.shape[:2]
        padded, r = preproc(frame, (896, 1600), None, None)
        tensor = torch.from_numpy(padded).unsqueeze(0).cuda()

        pred = detector.detect(tensor)                       # (N,5) 모델 좌표 or None
        rec.clear()
        targets = tracker.update(pred, tensor, frame, f"base:{seq}")

        if seq <= args.dump_frames:
            np.save(os.path.join(args.out, f"frame_{seq:06d}.npy"), frame)

        pred_np = (pred.cpu().numpy().astype(np.float32)
                   if pred is not None else np.zeros((0, 5), np.float32))
        targets_np = np.asarray(targets, np.float32).reshape(
            -1, targets.shape[1] if getattr(targets, "size", 0) else 6)
        np.savez_compressed(
            os.path.join(args.out, f"det_{seq:06d}.npz"),
            seq=seq, ts=0.0, scale_r=r, src_w=w, src_h=h, mux_w=w, mux_h=h,
            pred=pred_np,
            emb_bbox=rec.get("bbox", np.zeros((0, 4), np.float32)),
            emb=rec.get("emb", np.zeros((0, 0), np.float32)).astype(np.float32),
            targets=targets_np,
            out_xyxy=targets_np[:, :4], out_ids=targets_np[:, 4].astype(np.int64),
            out_conf=targets_np[:, 5] if targets_np.shape[1] > 5 else
            np.zeros(len(targets_np), np.float32))
        pbar.update(1)
    pbar.close()
    cap.release()
    print(f"baseline 덤프 완료: {seq}프레임 → {args.out}")


# ─────────────────────────────────────────────── compare (유사도 산출)

def _iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """(Na,4) x (Nb,4) xyxy IoU 행렬."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    aa = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    ab = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(aa[:, None] + ab[None, :] - inter, 1e-9)


def _hungarian_match(iou: np.ndarray, thresh: float = 0.5):
    """헝가리안 최대 IoU 매칭 → [(i, j, iou)] (iou>thresh만)."""
    from scipy.optimize import linear_sum_assignment
    if iou.size == 0:
        return []
    ri, ci = linear_sum_assignment(-iou)
    return [(i, j, iou[i, j]) for i, j in zip(ri, ci) if iou[i, j] > thresh]


def _load_dir(d: str) -> dict[int, dict]:
    out = {}
    for p in sorted(glob.glob(os.path.join(d, "det_*.npz"))):
        m = re.search(r"det_(\d+)\.npz$", p)
        if not m:
            continue
        out[int(m.group(1))] = dict(np.load(p))
    if not out:
        raise SystemExit(f"det_*.npz 없음: {d}")
    return out


def _to_src_px(rec: dict, arr: np.ndarray) -> np.ndarray:
    """트래킹 px(mux) bbox → 원본 카메라 px."""
    if len(arr) == 0:
        return arr.reshape(0, arr.shape[1] if arr.ndim > 1 else 4)
    kx = float(rec["src_w"]) / float(rec["mux_w"])
    ky = float(rec["src_h"]) / float(rec["mux_h"])
    out = arr.astype(np.float64).copy()
    out[:, 0] *= kx
    out[:, 2] *= kx
    out[:, 1] *= ky
    out[:, 3] *= ky
    return out


def _pred_to_src_px(rec: dict) -> np.ndarray:
    """모델 좌표 pred → 원본 px [x1,y1,x2,y2,conf]."""
    pred = rec["pred"].astype(np.float64)
    if len(pred) == 0:
        return pred.reshape(0, 5)
    pred = pred.copy()
    pred[:, :4] /= float(rec["scale_r"])
    pred[:, :4] = _to_src_px(rec, pred[:, :4])
    return pred


def _pct(x: float) -> str:
    return f"{100 * x:.2f}%"


def run_compare(args: argparse.Namespace) -> None:
    base = _load_dir(args.base)
    ds = _load_dir(args.ds)
    seqs = sorted(set(base) & set(ds))
    only_b, only_d = sorted(set(base) - set(ds)), sorted(set(ds) - set(base))
    print(f"프레임: baseline={len(base)} ds={len(ds)} 공통={len(seqs)} "
          f"(baseline만={len(only_b)}, ds만={len(only_d)})")

    conf_th = args.conf_thresh
    # ── 1) 검출 bbox ────────────────────────────────────────────────
    n_b = n_d = n_match = 0
    ious, dconfs = [], []
    per_frame_rates = []
    # ── 2) 임베딩 ──────────────────────────────────────────────────
    cos_all = []
    # ── 3) 트랙 ────────────────────────────────────────────────────
    trk_b = trk_d = trk_match = 0
    trk_ious = []
    life_b: dict[int, int] = {}
    life_d: dict[int, int] = {}

    for s in seqs:
        rb, rd = base[s], ds[s]
        pb, pd = _pred_to_src_px(rb), _pred_to_src_px(rd)
        pb = pb[pb[:, 4] >= conf_th]
        pd = pd[pd[:, 4] >= conf_th]
        matches = _hungarian_match(_iou_matrix(pb[:, :4], pd[:, :4]), args.iou_thresh)
        n_b += len(pb)
        n_d += len(pd)
        n_match += len(matches)
        denom = max(len(pb), len(pd))
        if denom:
            per_frame_rates.append(len(matches) / denom)
        for i, j, v in matches:
            ious.append(v)
            dconfs.append(abs(pb[i, 4] - pd[j, 4]))

        eb = _to_src_px(rb, rb["emb_bbox"].astype(np.float64))
        ed = _to_src_px(rd, rd["emb_bbox"].astype(np.float64))
        if len(eb) and len(ed) and rb["emb"].size and rd["emb"].size:
            for i, j, _ in _hungarian_match(_iou_matrix(eb, ed), args.iou_thresh):
                cos_all.append(float(rb["emb"][i] @ rd["emb"][j]))

        tb = _to_src_px(rb, rb["targets"][:, :4].astype(np.float64)) \
            if len(rb["targets"]) else np.zeros((0, 4))
        td = _to_src_px(rd, rd["targets"][:, :4].astype(np.float64)) \
            if len(rd["targets"]) else np.zeros((0, 4))
        tmatch = _hungarian_match(_iou_matrix(tb, td), args.iou_thresh)
        trk_b += len(tb)
        trk_d += len(td)
        trk_match += len(tmatch)
        trk_ious.extend(v for _, _, v in tmatch)
        for tid in rb["targets"][:, 4].astype(int):
            life_b[tid] = life_b.get(tid, 0) + 1
        for tid in rd["targets"][:, 4].astype(int):
            life_d[tid] = life_d.get(tid, 0) + 1

    ious = np.array(ious)
    dconfs = np.array(dconfs)
    cos_all = np.array(cos_all)
    trk_ious = np.array(trk_ious)
    lb, ld = np.array(list(life_b.values())), np.array(list(life_d.values()))

    det_rate = 2 * n_match / max(n_b + n_d, 1)
    trk_rate = 2 * trk_match / max(trk_b + trk_d, 1)

    # ── 4) 디코드 프레임 PSNR ──────────────────────────────────────
    psnr_rows = []
    try:
        import cv2
        for pb_ in sorted(glob.glob(os.path.join(args.base, "frame_*.npy"))):
            s = int(re.search(r"frame_(\d+)\.npy$", pb_).group(1))
            pd_ = os.path.join(args.ds, f"frame_{s:06d}.npy")
            if not os.path.exists(pd_):
                continue
            fb = np.load(pb_)                     # BGR (H,W,3) 원본 px
            fd = np.load(pd_)[..., :3]            # RGB (1080,1920,3) mux px
            fb_rgb = cv2.cvtColor(fb, cv2.COLOR_BGR2RGB)
            up = cv2.resize(fb_rgb, (fd.shape[1], fd.shape[0]),
                            interpolation=cv2.INTER_LINEAR)
            down = cv2.resize(fd, (fb.shape[1], fb.shape[0]),
                              interpolation=cv2.INTER_LINEAR)

            def _psnr(a, b):
                mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
                return float("inf") if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)

            psnr_rows.append({"seq": s, "psnr_up": _psnr(up, fd),
                              "psnr_down": _psnr(down, fb_rgb)})
    except ImportError:
        pass

    metrics = {
        "frames": {"baseline": len(base), "ds": len(ds), "common": len(seqs)},
        "detection": {
            "conf_thresh": conf_th, "iou_thresh": args.iou_thresh,
            "n_baseline": n_b, "n_ds": n_d, "n_matched": n_match,
            "match_rate": det_rate,
            "per_frame_match_rate_mean": float(np.mean(per_frame_rates)) if per_frame_rates else None,
            "matched_iou_mean": float(ious.mean()) if len(ious) else None,
            "matched_iou_p5": float(np.percentile(ious, 5)) if len(ious) else None,
            "matched_iou_min": float(ious.min()) if len(ious) else None,
            "conf_absdiff_mean": float(dconfs.mean()) if len(dconfs) else None,
            "conf_absdiff_max": float(dconfs.max()) if len(dconfs) else None,
        },
        "embedding": {
            "n_pairs": len(cos_all),
            "cos_mean": float(cos_all.mean()) if len(cos_all) else None,
            "cos_p5": float(np.percentile(cos_all, 5)) if len(cos_all) else None,
            "cos_p1": float(np.percentile(cos_all, 1)) if len(cos_all) else None,
            "cos_min": float(cos_all.min()) if len(cos_all) else None,
        },
        "tracks": {
            "unique_ids_baseline": len(life_b), "unique_ids_ds": len(life_d),
            "track_frames_baseline": trk_b, "track_frames_ds": trk_d,
            "bbox_match_rate": trk_rate,
            "matched_iou_mean": float(trk_ious.mean()) if len(trk_ious) else None,
            "lifetime_mean_baseline": float(lb.mean()) if len(lb) else None,
            "lifetime_mean_ds": float(ld.mean()) if len(ld) else None,
            "lifetime_median_baseline": float(np.median(lb)) if len(lb) else None,
            "lifetime_median_ds": float(np.median(ld)) if len(ld) else None,
            "lifetime_max_baseline": int(lb.max()) if len(lb) else None,
            "lifetime_max_ds": int(ld.max()) if len(ld) else None,
        },
        "frame_psnr": psnr_rows,
    }
    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w") as f:
            json.dump(metrics, f, indent=2, ensure_ascii=False)
        print(f"metrics → {args.json}")

    d, e, t = metrics["detection"], metrics["embedding"], metrics["tracks"]
    print("\n== 1) 검출 bbox (conf>=%.2f, 헝가리안 IoU>%.2f) ==" % (conf_th, args.iou_thresh))
    print(f"  검출 수      baseline={n_b}  ds={n_d}")
    print(f"  매칭률       {_pct(det_rate)}  (프레임 평균 {_pct(d['per_frame_match_rate_mean'] or 0)})")
    print(f"  매칭 IoU     mean={d['matched_iou_mean']:.4f}  p5={d['matched_iou_p5']:.4f}  min={d['matched_iou_min']:.4f}")
    print(f"  conf 차이    mean={d['conf_absdiff_mean']:.4f}  max={d['conf_absdiff_max']:.4f}")
    print("\n== 2) ReID 임베딩 cosine (매칭쌍 %d) ==" % e["n_pairs"])
    if e["cos_mean"] is not None:
        print(f"  mean={e['cos_mean']:.4f}  p5={e['cos_p5']:.4f}  p1={e['cos_p1']:.4f}  min={e['cos_min']:.4f}")
    print("\n== 3) 트랙 ==")
    print(f"  유니크 ID    baseline={t['unique_ids_baseline']}  ds={t['unique_ids_ds']}")
    print(f"  트랙 프레임  baseline={trk_b}  ds={trk_d}  bbox 매칭률={_pct(trk_rate)}"
          f"  매칭 IoU mean={t['matched_iou_mean']:.4f}")
    print(f"  수명(프레임) mean {t['lifetime_mean_baseline']:.1f}/{t['lifetime_mean_ds']:.1f}"
          f"  median {t['lifetime_median_baseline']:.0f}/{t['lifetime_median_ds']:.0f}"
          f"  max {t['lifetime_max_baseline']}/{t['lifetime_max_ds']}")
    if psnr_rows:
        print("\n== 4) 디코드 프레임 PSNR (baseline↑업스케일 vs DS mux / DS↓다운 vs 원본) ==")
        for r in psnr_rows:
            print(f"  seq={r['seq']}  up={r['psnr_up']:.2f}dB  down={r['psnr_down']:.2f}dB")

    ok_det = det_rate > 0.95 and (d["matched_iou_mean"] or 0) > 0.9
    ok_emb = (e["cos_mean"] or 0) > 0.98
    print(f"\n판정: 검출 {'PASS' if ok_det else 'FAIL'} (기준 매칭률>95%, IoU>0.9) / "
          f"임베딩 {'PASS' if ok_emb else 'FAIL'} (기준 cos>0.98)")


# ─────────────────────────────────────────────── attrib (임베딩 차이 원인 분해)

def _gpu_crops(frame_chw, bboxes, device):
    """DS DsGpuEmbeddingComputer 방식 crop: 텐서 슬라이스 + float bilinear."""
    import torch
    import torch.nn.functional as F
    _, h, w = frame_chw.shape
    bb = np.round(bboxes).astype(np.int32)
    bb[:, [0, 2]] = bb[:, [0, 2]].clip(0, w)
    bb[:, [1, 3]] = bb[:, [1, 3]].clip(0, h)
    crops = []
    for x1, y1, x2, y2 in bb:
        if x2 <= x1 or y2 <= y1:
            crops.append(torch.zeros((1, 3, 384, 128), dtype=torch.float32,
                                     device=device))
            continue
        c = frame_chw[:, y1:y2, x1:x2].float().unsqueeze(0)
        crops.append(F.interpolate(c, size=(384, 128), mode="bilinear",
                                   align_corners=False))
    return torch.cat(crops, 0)


def _cv2_crops(frame_bgr, bboxes):
    """기존 GPUEmbeddingComputer 방식 crop: cv2 uint8 crop + BGR2RGB + resize."""
    import cv2
    h, w = frame_bgr.shape[:2]
    bb = np.round(bboxes).astype(np.int32)
    bb[:, [0, 2]] = bb[:, [0, 2]].clip(0, w)
    bb[:, [1, 3]] = bb[:, [1, 3]].clip(0, h)
    out = np.zeros((len(bb), 3, 384, 128), np.float32)
    for i, (x1, y1, x2, y2) in enumerate(bb):
        crop = frame_bgr[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        crop = cv2.resize(crop, (128, 384), interpolation=cv2.INTER_LINEAR)
        out[i] = crop.transpose(2, 0, 1).astype(np.float32)
    return out


def run_attrib(args: argparse.Namespace) -> None:
    """frame 덤프가 있는 앞 N프레임에서 임베딩 차이를 요인별로 분해.

      cosA_C: 같은 픽셀(기존 디코드) — crop 방식만 다름 (cv2 uint8 vs GPU float)
      cosA_B: 같은 엔진(호스트)     — 픽셀 소스(NVDEC+mux 업스케일+색변환)+crop 종합
    임베딩 엔진 빌드 차이는 reid-embed 서브커맨드로 별도 격리한다.
    """
    import cv2
    import torch
    import torch.nn.functional as F
    from system.ingest_ds.trt_infer import TRTReID

    model = TRTReID(args.reid_engine)
    base = _load_dir(args.base)
    cosac, cosab, ch_diff = [], [], []
    crops_a_all = []
    for pb_ in sorted(glob.glob(os.path.join(args.base, "frame_*.npy"))):
        s = int(re.search(r"frame_(\d+)\.npy$", pb_).group(1))
        pd_ = os.path.join(args.ds, f"frame_{s:06d}.npy")
        if not os.path.exists(pd_) or s not in base:
            continue
        rb = base[s]
        bb = rb["emb_bbox"].astype(np.float64)          # 기존 경로 px (=원본 px)
        if not len(bb):
            continue
        fb = np.load(pb_)                                # BGR 원본 px
        fd = np.load(pd_)[..., :3]                       # RGB mux px(1920x1080)

        kx = fd.shape[1] / fb.shape[1]
        ky = fd.shape[0] / fb.shape[0]
        bb_mux = bb * np.array([kx, ky, kx, ky])

        a = _cv2_crops(fb, bb)                                        # 기존 방식
        fb_rgb_t = torch.from_numpy(
            cv2.cvtColor(fb, cv2.COLOR_BGR2RGB)).permute(2, 0, 1).cuda()
        c = _gpu_crops(fb_rgb_t, bb, "cuda")                          # crop만 DS식
        fd_t = torch.from_numpy(np.ascontiguousarray(fd)).permute(2, 0, 1).cuda()
        b = _gpu_crops(fd_t, bb_mux, "cuda")                          # DS 픽셀+DS식

        with torch.no_grad():
            ea = F.normalize(model(torch.from_numpy(a).cuda()), dim=-1)
            ec = F.normalize(model(c), dim=-1)
            eb = F.normalize(model(b), dim=-1)
        cosac.extend((ea * ec).sum(-1).cpu().tolist())
        cosab.extend((ea * eb).sum(-1).cpu().tolist())
        crops_a_all.append(a)

        up = cv2.resize(cv2.cvtColor(fb, cv2.COLOR_BGR2RGB),
                        (fd.shape[1], fd.shape[0]), interpolation=cv2.INTER_LINEAR)
        ch_diff.append((fd.astype(np.float64) - up.astype(np.float64))
                       .mean(axis=(0, 1)))

    cosac, cosab = np.array(cosac), np.array(cosab)
    print(f"임베딩 차이 요인 분해 (호스트 엔진 고정, 매칭 crop {len(cosac)}개):")
    print(f"  cos(cv2 crop, GPU crop)  같은 픽셀   mean={cosac.mean():.4f} min={cosac.min():.4f}")
    print(f"  cos(cv2 crop, DS 픽셀)   디코드+mux  mean={cosab.mean():.4f} min={cosab.min():.4f}")
    print("프레임 RGB 채널 평균 오프셋 (DS mux − baseline 업스케일):")
    for i, d in enumerate(ch_diff, 1):
        print(f"  frame{i}: R={d[0]:+.2f} G={d[1]:+.2f} B={d[2]:+.2f}")
    if args.save_crops:
        np.save(args.save_crops, np.concatenate(crops_a_all, 0))
        print(f"crop 배치 저장 → {args.save_crops} (reid-embed로 엔진별 임베딩 비교)")


def run_reid_embed(args: argparse.Namespace) -> None:
    """crop npy 배치를 지정 엔진으로 임베딩 → npy 저장 (컨테이너/호스트 공용).

    호스트·컨테이너 각각 실행해 두 임베딩의 cosine을 재면 TRT 엔진 빌드
    (버전 10.16 vs 10.14, static vs dynamic batch) 차이만 격리된다.
    """
    import torch
    import torch.nn.functional as F
    from system.ingest_ds.trt_infer import TRTReID

    model = TRTReID(args.engine)
    crops = torch.from_numpy(np.load(args.crops)).cuda()
    embs = []
    for i in range(0, len(crops), 256):
        with torch.no_grad():
            embs.append(F.normalize(model(crops[i:i + 256]), dim=-1).cpu())
    out = torch.cat(embs, 0).numpy()
    np.save(args.out, out)
    print(f"{len(out)}개 임베딩 → {args.out}")


# ─────────────────────────────────────────────── CLI

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("baseline", help="기존 경로(cv2+직렬 TRT) 프레임별 덤프")
    b.add_argument("--video", default="assets/sample1.mp4")
    b.add_argument("--out", required=True)
    b.add_argument("--yolox-engine", default="external/weights/trt/yolox_mot20_fp16.engine")
    b.add_argument("--reid-engine", default="external/weights/trt/fastreid_sbs_s50_fp16.engine")
    b.add_argument("--det-thresh", type=float, default=0.4)
    b.add_argument("--max-age", type=int, default=50, help="worker --max-age와 동일하게")
    b.add_argument("--dump-frames", type=int, default=5)
    b.add_argument("--max-frames", type=int, default=0)

    c = sub.add_parser("compare", help="양쪽 덤프 비교 → 유사도 수치")
    c.add_argument("--base", required=True, help="baseline 덤프 디렉토리")
    c.add_argument("--ds", required=True, help="DS 덤프 디렉토리 (cam_id 하위 폴더)")
    c.add_argument("--conf-thresh", type=float, default=0.4)
    c.add_argument("--iou-thresh", type=float, default=0.5)
    c.add_argument("--json", default="", help="메트릭 JSON 저장 경로")

    a = sub.add_parser("attrib", help="임베딩 차이 원인 분해 (frame 덤프 프레임 대상)")
    a.add_argument("--base", required=True)
    a.add_argument("--ds", required=True)
    a.add_argument("--reid-engine", default="external/weights/trt/fastreid_sbs_s50_fp16.engine")
    a.add_argument("--save-crops", default="", help="cv2 crop 배치 npy 저장 (엔진 격리용)")

    r = sub.add_parser("reid-embed", help="crop npy → 임베딩 npy (엔진 빌드 격리)")
    r.add_argument("--engine", required=True)
    r.add_argument("--crops", required=True)
    r.add_argument("--out", required=True)

    args = ap.parse_args()
    if args.cmd == "baseline":
        run_baseline(args)
    elif args.cmd == "attrib":
        run_attrib(args)
    elif args.cmd == "reid-embed":
        run_reid_embed(args)
    else:
        run_compare(args)


if __name__ == "__main__":
    main()
