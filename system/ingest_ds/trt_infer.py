"""TRT 엔진 래퍼 + 배치 검출기·ReID — 컨테이너 전용.

src/inference_trt.py 의 TRTEngine·TRTDetector·TRTReID 를 이식했다.
원본 모듈은 yolox 패키지를 import하므로 컨테이너에서 그대로 쓸 수 없어,
엔진 래퍼는 코드 동일하게 가져오고 검출기만 dynamic-batch 버전으로 바꿨다
(검출 파라미터 conf 0.1 / nms 0.7 / 클래스 1개는 원본과 동일).
"""
from __future__ import annotations

import tensorrt as trt
import torch

from system.ingest_ds.yolox_post import postprocess


class TRTEngine:
    """Minimal TensorRT engine wrapper (src/inference_trt.py 동일)."""

    def __init__(self, engine_path: str):
        self.logger = trt.Logger(trt.Logger.WARNING)
        with open(engine_path, "rb") as f:
            runtime = trt.Runtime(self.logger)
            self.engine = runtime.deserialize_cuda_engine(f.read())
        self.context = self.engine.create_execution_context()

        self.input_names = []
        self.output_names = []
        self.output_shapes = {}
        self.dynamic_batch = False
        self.max_batch = 0            # dynamic 엔진의 프로파일 max 배치 (정적이면 고정 배치)

        for i in range(self.engine.num_io_tensors):
            name = self.engine.get_tensor_name(i)
            mode = self.engine.get_tensor_mode(name)
            shape = self.engine.get_tensor_shape(name)
            if mode == trt.TensorIOMode.INPUT:
                self.input_names.append(name)
                if shape[0] == -1:
                    self.dynamic_batch = True
                    # 최적화 프로파일 0의 (min, opt, max) 중 max의 배치 차원 —
                    # 엔진마다 다른 상한(b16/b32 등)을 하드코딩 없이 읽는다.
                    _mn, _opt, _mx = self.engine.get_tensor_profile_shape(name, 0)
                    self.max_batch = int(_mx[0])
                else:
                    self.max_batch = int(shape[0])
            else:
                self.output_names.append(name)
                self.output_shapes[name] = tuple(shape)

        self.stream = torch.cuda.Stream()

    def __call__(self, input_tensor: torch.Tensor) -> list:
        if self.dynamic_batch:
            self.context.set_input_shape(self.input_names[0], tuple(input_tensor.shape))

        self.context.set_tensor_address(self.input_names[0], input_tensor.data_ptr())

        outputs = []
        for name in self.output_names:
            shape = self.context.get_tensor_shape(name)
            out = torch.empty(tuple(shape), dtype=torch.float32, device="cuda")
            self.context.set_tensor_address(name, out.data_ptr())
            outputs.append(out)

        self.context.execute_async_v3(self.stream.cuda_stream)
        self.stream.synchronize()
        return outputs


class BatchDetector:
    """dynamic-batch YOLOX 검출 — TRTDetector.detect 의 배치 버전."""

    def __init__(self, engine_path: str, num_classes=1, conf_thresh=0.1, nms_thresh=0.7):
        self.engine = TRTEngine(engine_path)
        self.num_classes = num_classes
        self.conf_thresh = conf_thresh
        self.nms_thresh = nms_thresh

    @property
    def max_batch(self) -> int:
        """엔진 프로파일의 배치 상한 — worker가 --batch-size를 이 값으로 클램프."""
        return self.engine.max_batch

    def detect(self, batch: torch.Tensor) -> list:
        """입력 (B, 3, 896, 1600) CUDA 텐서 → 이미지별 (N, 5) [x1,y1,x2,y2,conf]
        텐서 목록 (검출 없으면 None). 좌표는 모델 입력(896x1600) 기준."""
        raw = self.engine(batch.float())[0]
        preds = postprocess(raw, self.num_classes, self.conf_thresh, self.nms_thresh)
        out = []
        for pred in preds:
            if pred is not None:
                out.append(torch.cat((pred[:, :4], (pred[:, 4] * pred[:, 5])[:, None]), dim=1))
            else:
                out.append(None)
        return out


class TRTReID(torch.nn.Module):
    """FastReID TRT 엔진 (src/inference_trt.py TRTReID 동일 — dynamic batch 지원)."""

    def __init__(self, engine_path: str):
        super().__init__()
        self.engine = TRTEngine(engine_path)
        self.pH, self.pW = 384, 128

    def forward(self, batch: torch.Tensor):
        outputs = self.engine(batch.float())
        return outputs[0]
