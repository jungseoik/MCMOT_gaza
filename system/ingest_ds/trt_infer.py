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

        # torch.cuda.Stream()은 non-blocking 스트림 — default 스트림과 암묵 동기화가
        # 없다. 입력 텐서를 만드는 커널(torch.stack·letterbox·ReID crop)은 전부
        # default 스트림에 쌓이므로, 대기 없이 실행하면 GPU 부하 시 TRT가 복사
        # 미완료 메모리를 읽어 배치 전체가 쓰레기 검출(수천 개)로 깨진다
        # (운영 3ch 라이브에서 프레임 ~10% 상시 재현·수정으로 소멸 — 2026-07-20).
        self.stream.wait_stream(torch.cuda.current_stream())
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


class BatchYOLO26Detector:
    """dynamic-batch YOLO26(end2end) 검출 — src/yolo26_trt.py 의 배치 버전.

    원본 모듈은 src.inference_trt(→ yolox 패키지)를 import하므로 컨테이너에서
    쓸 수 없다. 후처리 수식만 이식한다(yolox_post.py 벤더링과 같은 이유).
    end2end 모델이라 NMS가 그래프 안에 있어 후처리는 필터링뿐이다.
    """

    def __init__(self, engine_path: str, conf_thresh: float = 0.4,
                 min_box_size: int = 0, person_class: int = 0):
        # min_box_size 기본 0 — 이 단계의 좌표는 letterbox 스케일(1080p→640이면
        # 1/3)이라 프레임 px 기준값을 그대로 걸면 3배 엄격해진다. 크기 필터는
        # 역스케일 뒤(worker._unletterbox)에서 건다.
        self.engine = TRTEngine(engine_path)
        self.conf_thresh = conf_thresh
        self.min_box = min_box_size
        self.person = person_class

    @property
    def max_batch(self) -> int:
        return self.engine.max_batch

    def detect(self, batch: torch.Tensor) -> list:
        """입력 (B,3,S,S) → 이미지별 (N,5)[x1,y1,x2,y2,conf] 텐서 목록.
        좌표는 **모델 입력(letterbox) 기준** — 패딩 제거·역스케일은 호출자 몫."""
        raw = self.engine(batch.float())[0]          # (B, 300, 6)
        out = []
        for i in range(raw.shape[0]):
            rows = raw[i]
            keep = (rows[:, 4] >= self.conf_thresh) & (rows[:, 5].round() == self.person)
            if self.min_box > 0:
                side = torch.minimum(rows[:, 2] - rows[:, 0], rows[:, 3] - rows[:, 1])
                keep &= side >= self.min_box
            sel = rows[keep]
            out.append(torch.cat([sel[:, :4], sel[:, 4:5]], dim=1) if sel.shape[0] else None)
        return out


class CLIPReID(torch.nn.Module):
    """CLIP-ReID TRT 엔진 (src/clipreid_trt.py 이식 — 컨테이너용).

    임베더는 0~255 RGB 배치를 넘기는 계약이므로 CLIP 전처리(/255 + mean/std)를
    이 안에서 끝낸다. crop 크기는 엔진 바인딩에서 읽는다(person 256×128).
    """

    _MEAN = (0.48145466, 0.4578275, 0.40821073)
    _STD = (0.26862954, 0.26130258, 0.27577711)

    def __init__(self, engine_path: str):
        super().__init__()
        self.engine = TRTEngine(engine_path)
        ishape = tuple(self.engine.engine.get_tensor_shape(self.engine.input_names[0]))
        self.pH = int(ishape[2]) if len(ishape) >= 4 and ishape[2] > 0 else 256
        self.pW = int(ishape[3]) if len(ishape) >= 4 and ishape[3] > 0 else 128
        self._mean = torch.tensor(self._MEAN, device="cuda").view(1, 3, 1, 1) * 255.0
        self._std = torch.tensor(self._STD, device="cuda").view(1, 3, 1, 1) * 255.0

    @property
    def crop_size(self) -> tuple[int, int]:
        return (self.pW, self.pH)

    def forward(self, batch: torch.Tensor):
        x = (batch.float() - self._mean) / self._std
        return self.engine(x.contiguous())[0]
