"""RF-DETR(base) TensorRT 검출기 — 본 환경(boosttrack) 자체 구현, rfdetr 라이브러리 불필요.

엔진 빌드(ONNX export)는 격리 venv에서 1회 수행하고(tools/setup_rfdetr.sh),
추론은 이 파일 + TRT 엔진 파일만으로 동작한다. 전/후처리는 RF-DETR 공식
(rfdetr.export._onnx / benchmark.post_process)과 동일하게 복제:
  - 전처리: RGB → to_tensor(0~1) → resize(res,res, bilinear, antialias=False) → ImageNet 정규화
  - 후처리: sigmoid(logits) → top-k(num_select) → cxcywh→xyxy → *(W,H,W,H) → person(conf) 필터

BoostTrackGPUInference의 두 검출기(YOLOX/RF-DETR) 공통 인터페이스:
    detect_frame(bgr_frame) -> (dets[N,5] xyxy+conf(원본좌표), scale_ref_tensor)
BoostTrack.update(dets, scale_ref_tensor, frame, tag)에서 scale_ref_tensor는 shape로만
쓰여 스케일=min(ref.h/frame.h, ref.w/frame.w)를 낸다. RF-DETR dets는 이미 원본좌표이므로
ref=shape (1,3,H,W)를 주면 scale=1 (좌표 그대로).
"""
from __future__ import annotations

import cv2
import numpy as np
import torch
import torchvision.transforms.functional as F

from src.inference_trt import TRTEngine

_IMAGENET_MEAN = [0.485, 0.456, 0.406]
_IMAGENET_STD = [0.229, 0.224, 0.225]


class RFDETRTRTDetector:
    """TensorRT RF-DETR person detector (drop-in with BoostTrackGPUInference)."""

    def __init__(self, engine_path: str, res: int = 560, conf_thresh: float = 0.1,
                 person_class: int = 1, num_select: int = 300):
        self.engine = TRTEngine(engine_path)
        self.res = res
        self.conf = conf_thresh
        self.person = person_class
        self.num_select = num_select
        # 출력 매핑: 마지막 차원 4 = dets(cxcywh), 그 외 = labels(logits)
        self._det_name = self._lab_name = None
        for name, shp in self.engine.output_shapes.items():
            if shp[-1] == 4:
                self._det_name = name
            else:
                self._lab_name = name
        if self._det_name is None or self._lab_name is None:
            raise RuntimeError(f"RF-DETR 엔진 출력 매핑 실패: {self.engine.output_shapes}")

    def _preprocess(self, bgr: np.ndarray) -> torch.Tensor:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        t = torch.from_numpy(rgb).permute(2, 0, 1).float().div_(255.0)   # to_tensor
        t = F.resize(t, [self.res, self.res], antialias=False)           # predict()와 동일
        t = F.normalize(t, _IMAGENET_MEAN, _IMAGENET_STD)
        return t.unsqueeze(0).contiguous().cuda()

    @torch.no_grad()
    def detect_frame(self, bgr: np.ndarray):
        H, W = bgr.shape[:2]
        outs = self.engine(self._preprocess(bgr))
        omap = {n: o for n, o in zip(self.engine.output_names, outs)}
        boxes = omap[self._det_name][0]          # (Q,4) cxcywh (0~1)
        logits = omap[self._lab_name][0]         # (Q,C)
        scores = logits.sigmoid()
        flat = scores.reshape(-1)
        k = min(self.num_select, flat.shape[0])
        topv, topi = torch.topk(flat, k)
        C = scores.shape[1]
        q = torch.div(topi, C, rounding_mode="floor")
        lab = topi % C
        b = boxes[q]                             # (k,4) cxcywh
        xyxy = torch.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2,
                            b[:, 0] + b[:, 2] / 2, b[:, 1] + b[:, 3] / 2], dim=1)
        xyxy = xyxy * torch.tensor([W, H, W, H], device=b.device, dtype=b.dtype)
        keep = (lab == self.person) & (topv > self.conf)
        dets = torch.cat([xyxy[keep], topv[keep, None]], dim=1).cpu().numpy().astype(np.float32)
        ref = torch.empty((1, 3, H, W), device="meta")   # scale=1 (원본좌표)
        return dets, ref
