"""CLIP-ReID(ViT-B/16) TensorRT ReID — FastReID(TRTReID) 드롭인 교체.

PIASPACE 제공 `clipreid_person.onnx`(CLIP-ReID 이미지 인코더 + BN neck,
768-d)를 TRT로 구워 돌린다. 임베딩 소비자(GPUEmbeddingComputer /
DsGpuEmbeddingComputer)는 **0~255 RGB float 배치**를 넘기고 결과를 L2 정규화
하는 계약이라, CLIP 고유의 전처리(/255 + CLIP mean/std)를 이 래퍼 안에서
끝낸다 — 임베더 코드는 crop 크기만 바꾸면 그대로 재사용된다.

  FastReID SBS-S50 : crop (W,H)=(128,384), 입력 0~255 그대로, 2048-d
  CLIP-ReID ViT-B/16: crop (W,H)=(128,256), 입력 /255 후 CLIP 정규화, 768-d

참조 구현(piaspace-clip-reid)은 crop resize에 cv2 INTER_CUBIC을 쓰고 여기는
기존 임베더의 bilinear를 그대로 쓴다 — 코사인 유사도 영향은 무시 가능한
수준이나, 재현 비교 시 알고 있어야 하는 차이.
"""
from __future__ import annotations

import torch

from src.inference_trt import TRTEngine

CLIP_MEAN = (0.48145466, 0.4578275, 0.40821073)
CLIP_STD = (0.26862954, 0.26130258, 0.27577711)


class CLIPReIDTRT(torch.nn.Module):
    """CLIP-ReID TRT 엔진 래퍼 — forward(0~255 RGB 배치) → (N, 768) raw feature."""

    def __init__(self, engine_path: str):
        super().__init__()
        self.engine = TRTEngine(engine_path)
        ishape = tuple(self.engine.engine.get_tensor_shape(self.engine.input_names[0]))
        # 엔진 바인딩에서 crop 크기를 읽는다 — person(256×128)/vehicle(256×256) 무설정 대응
        self.pH = int(ishape[2]) if len(ishape) >= 4 and ishape[2] > 0 else 256
        self.pW = int(ishape[3]) if len(ishape) >= 4 and ishape[3] > 0 else 128
        m = torch.tensor(CLIP_MEAN, device="cuda").view(1, 3, 1, 1)
        s = torch.tensor(CLIP_STD, device="cuda").view(1, 3, 1, 1)
        self.register_buffer("_mean", m * 255.0, persistent=False)   # 0~255 스케일로 미리 환산
        self.register_buffer("_std", s * 255.0, persistent=False)

    @property
    def crop_size(self) -> tuple[int, int]:
        """임베더 컨벤션 (W, H)."""
        return (self.pW, self.pH)

    def forward(self, batch: torch.Tensor) -> torch.Tensor:
        x = (batch.float() - self._mean) / self._std
        return self.engine(x.contiguous())[0]
