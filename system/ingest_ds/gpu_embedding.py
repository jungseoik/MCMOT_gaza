"""GPU 상주 ReID 임베더 — src/inference_gpu.py GPUEmbeddingComputer 의 GPU 크롭 버전.

원본은 CPU np 프레임에서 cv2 crop → cv2.resize(기본 bilinear) → float32(0~255)
버퍼 → GPU 전송이었다. DeepStream 경로는 프레임이 GPU 텐서(RGB, uint8)로만
존재하므로 같은 절차를 GPU에서 재현한다:

  텐서 슬라이스 crop → float 변환 → F.interpolate(bilinear, align_corners=False,
  (384,128)) → 배치 → TRT FastReID → F.normalize

수치 주의: cv2.resize는 uint8 고정소수점 보간(반올림), GPU는 float 보간이라
픽셀당 ±1/255 수준의 미세 차이가 있다 (임베딩 코사인 유사도에는 무시 가능).

BoostTrack.update 가 embedder.compute_embedding(img, bbox, tag)로 넘기는 img는
shape 참조용 더미이며(ECC off 전제 — 이미지 내용은 임베딩에만 쓰임),
실제 픽셀은 매 프레임 set_frame()으로 등록한 GPU 텐서에서 읽는다.
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F


class DsGpuEmbeddingComputer:
    """tracker.embedder.compute_embedding 패치용 — GPU 텐서에서 직접 crop."""

    def __init__(self, model, crop_size=(128, 384), max_batch=256):
        self.model = model                      # TRTReID (dynamic batch)
        self.crop_w, self.crop_h = crop_size    # 원본 컨벤션: (W, H) = (128, 384)
        self.max_batch = max_batch
        self._frame: torch.Tensor | None = None  # (3, H, W) uint8 RGB CUDA

    def set_frame(self, rgb_u8: torch.Tensor) -> None:
        """이번 프레임의 GPU RGB 텐서 등록 — tracker.update 직전에 호출."""
        self._frame = rgb_u8

    def compute_embedding(self, img, bbox: np.ndarray, tag: str) -> np.ndarray:
        """원본 GPUEmbeddingComputer.compute_embedding 과 동일 계약.

        img 인자는 무시한다(shape 더미) — 픽셀은 set_frame() 텐서에서 읽는다.
        """
        if bbox.shape[0] == 0:
            return np.ones((0, 1))
        assert self._frame is not None, "set_frame() 미호출 — tracker.update 전에 등록할 것"
        frame = self._frame
        _, h, w = frame.shape

        # bbox 정규화 — 원본과 동일 (round → int → 프레임 경계 clip)
        bbox = np.round(bbox).astype(np.int32)
        bbox[:, 0] = bbox[:, 0].clip(0, w)
        bbox[:, 1] = bbox[:, 1].clip(0, h)
        bbox[:, 2] = bbox[:, 2].clip(0, w)
        bbox[:, 3] = bbox[:, 3].clip(0, h)

        n = bbox.shape[0]
        crops = []
        for i in range(n):
            x1, y1, x2, y2 = bbox[i]
            if x2 <= x1 or y2 <= y1:
                # 원본의 빈 crop 처리(버퍼 0 채움)와 동일
                crops.append(torch.zeros((1, 3, self.crop_h, self.crop_w),
                                         dtype=torch.float32, device=frame.device))
                continue
            crop = frame[:, y1:y2, x1:x2].float().unsqueeze(0)  # (1,3,ch,cw) 0~255
            crops.append(F.interpolate(crop, size=(self.crop_h, self.crop_w),
                                       mode="bilinear", align_corners=False))
        batch = torch.cat(crops, dim=0)

        embs = []
        for idx in range(0, n, self.max_batch):
            with torch.no_grad():
                embs.append(self.model(batch[idx:idx + self.max_batch]))
        out = F.normalize(torch.cat(embs, dim=0), dim=-1)
        return out.cpu().numpy()
