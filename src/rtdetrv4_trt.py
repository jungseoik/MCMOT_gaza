"""RT-DETRv4-S TensorRT 검출기 — 본 환경(boosttrack) 자체 구현, RT-DETRv4 라이브러리 불필요.

엔진 빌드(ONNX export)는 격리 venv에서 1회(tools/setup_rtdetrv4.sh), 추론은 이 파일 +
TRT 엔진만으로 동작한다. 가중치는 HF Awiros/person_and_head_detection 의
RT-DETRv4-S + C-RADIOv4 distillation (CrowdHuman visible-person FT, 2클래스).
C-RADIO 교사는 **학습 때만** 쓰이므로 추론 비용은 순정 RT-DETRv4-S 와 같다.

전처리 (공식 configs/base/dataloader.yml val 파이프라인과 동일):
    RGB → Resize(640,640) → ConvertPILImage(scale=True, 0~1)
  ※ RF-DETR 과 달리 **ImageNet 정규화가 없다** — 넣으면 검출이 무너진다.

후처리: **모델 안에 postprocessor 가 포함**돼 있다(export_onnx.py 가 deploy 모드로 묶음).
    출력 labels(1,300) · boxes(1,300,4: xyxy, orig_target_sizes 스케일) · scores(1,300)
  따라서 여기서는 person 클래스 + conf 필터만 하면 된다.

공통 인터페이스 (YOLOX/YOLO26/RF-DETR 과 동일):
    detect_frame(bgr) -> (dets[N,5] xyxy+conf(원본좌표), scale_ref_tensor)
  dets 가 이미 원본좌표이므로 ref=shape (1,3,H,W) 를 주면 scale=1 (좌표 그대로).
  ※ YOLOX 는 letterbox 좌표 + CUDA 텐서로 내보내 스케일 복원이 필요하다 —
     여기서는 원본좌표로 맞춰 AnalyzerThread._frame_dets 가 그대로 처리하게 한다.
"""
from __future__ import annotations

import cv2
import numpy as np
import tensorrt as trt
import torch

from src.inference_trt import TRTEngine

# CrowdHuman FT 2클래스 — 0: person(visible body), 1: head
PERSON_CLASS = 0


class RTDETRv4TRTDetector:
    """TensorRT RT-DETRv4-S person detector (drop-in with BoostTrackGPUInference)."""

    def __init__(self, engine_path: str, res: int | None = None,
                 conf_thresh: float = 0.4, person_class: int = PERSON_CLASS,
                 letterbox: bool = True):
        self.engine = TRTEngine(engine_path)
        if res is None:                          # 엔진 바인딩에서 입력 해상도 자동 감지
            ishape = tuple(self.engine.engine.get_tensor_shape(self.engine.input_names[0]))
            res = int(ishape[2]) if len(ishape) >= 4 and ishape[2] > 0 else 640
        self.res = int(res)
        self.conf = float(conf_thresh)
        self.person = int(person_class)
        # 공식 val 전처리는 종횡비를 무시한 단순 resize 다. 그런데 우리 입력은 16:9
        # (1920x1080) 라 1:1 로 찌그러지면 사람이 가로로 뭉개진다 — YOLO26 은
        # center_pad 로 종횡비를 보존한다. letterbox=True 면 같은 조건으로 맞춘다.
        self.letterbox = bool(letterbox)
        out = self.engine.output_shapes
        # 출력 매핑 — 마지막 차원 4 = boxes, 나머지 둘은 이름으로 구분
        self._box = next((n for n, s in out.items() if len(s) == 3 and s[-1] == 4), None)
        self._lab = next((n for n in out if "label" in n.lower()), None)
        self._scr = next((n for n in out if "score" in n.lower()), None)
        if not (self._box and self._lab and self._scr):
            raise RuntimeError(f"RT-DETRv4 엔진 출력 매핑 실패: {out}")

    def _preprocess(self, bgr: np.ndarray):
        """→ (입력텐서, (r, dx, dy)). letterbox 가 아니면 r/dx/dy 는 None."""
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        if not self.letterbox:
            img = cv2.resize(rgb, (self.res, self.res), interpolation=cv2.INTER_LINEAR)
            meta = None
        else:
            H, W = rgb.shape[:2]
            r = min(self.res / H, self.res / W)
            nh, nw = int(round(H * r)), int(round(W * r))
            dx, dy = (self.res - nw) // 2, (self.res - nh) // 2
            img = np.full((self.res, self.res, 3), 114, np.uint8)     # YOLO 관행 회색
            img[dy:dy + nh, dx:dx + nw] = cv2.resize(rgb, (nw, nh),
                                                     interpolation=cv2.INTER_LINEAR)
            meta = (r, dx, dy)
        t = torch.from_numpy(img).permute(2, 0, 1).float().div_(255.0)
        return t.unsqueeze(0).contiguous().cuda(), meta

    @torch.no_grad()
    def _run(self, images: torch.Tensor, sizes: torch.Tensor) -> dict:
        """입력이 2개(images·orig_target_sizes)라 TRTEngine.__call__(1입력)을 못 쓴다 —
        같은 컨텍스트·스트림 규약으로 직접 바인딩한다(inference_trt.TRTEngine 참고).
        labels 는 int64 바인딩이라 출력 dtype 을 엔진에 맞춰 잡아야 한다."""
        e = self.engine
        e.context.set_tensor_address(e.input_names[0], images.data_ptr())
        e.context.set_tensor_address(e.input_names[1], sizes.data_ptr())
        outs = {}
        for name in e.output_names:
            shape = tuple(e.context.get_tensor_shape(name))
            dt = torch.int64 if e.engine.get_tensor_dtype(name) == trt.DataType.INT64 else torch.float32
            o = torch.empty(shape, dtype=dt, device="cuda")
            e.context.set_tensor_address(name, o.data_ptr())
            outs[name] = o
        e.stream.wait_stream(torch.cuda.current_stream())
        e.context.execute_async_v3(e.stream.cuda_stream)
        e.stream.synchronize()
        return outs

    @torch.no_grad()
    def detect_frame(self, bgr: np.ndarray):
        H, W = bgr.shape[:2]
        tensor, meta = self._preprocess(bgr)
        # orig_target_sizes 로 postprocessor 가 박스를 되돌린다.
        #  - 단순 resize: (W,H) 를 주면 바로 원본 좌표
        #  - letterbox : 패딩된 정사각 좌표가 필요하므로 (res,res) 를 주고 여기서 역변환
        ow, oh = (W, H) if meta is None else (self.res, self.res)
        sizes = torch.tensor([[ow, oh]], dtype=torch.int64, device="cuda")
        omap = self._run(tensor, sizes)
        labels = omap[self._lab][0]
        boxes = omap[self._box][0]                     # (300,4) xyxy
        scores = omap[self._scr][0]
        keep = (labels == self.person) & (scores > self.conf)
        dets = torch.cat([boxes[keep], scores[keep, None]], dim=1)
        dets = dets.float().cpu().numpy().astype(np.float32)
        if meta is not None and len(dets):
            r, dx, dy = meta
            dets[:, [0, 2]] = (dets[:, [0, 2]] - dx) / r
            dets[:, [1, 3]] = (dets[:, [1, 3]] - dy) / r
        if len(dets):
            # 프레임 경계로 클리핑 — **두 경로 모두**. postprocessor 는 박스를
            # orig_target_sizes 로 되돌릴 뿐 경계를 보장하지 않아 밖으로 나간다.
            # ⚠ dets[:, [0,2]] 는 팬시 인덱싱이라 **복사본**이다 — np.clip(..., out=)
            #   으로 쓰면 복사본에 써서 버려진다(실측: y1=1084 가 그대로 통과해
            #   임베더 clip 후 높이 0 → cv2.cvtColor 크래시). 열마다 대입한다.
            dets[:, 0] = np.clip(dets[:, 0], 0, W)
            dets[:, 2] = np.clip(dets[:, 2], 0, W)
            dets[:, 1] = np.clip(dets[:, 1], 0, H)
            dets[:, 3] = np.clip(dets[:, 3], 0, H)
            # 축퇴 박스 제거 — 폭·높이가 0 이면 ReID 크롭이 빈 배열이라
            # cv2.cvtColor 에서 죽는다(tracker/embedding.py:130). 여기서 막는다.
            # 임베더가 np.round 후 다시 clip 하므로 여유를 2px 둔다.
            wh = dets[:, 2:4] - dets[:, 0:2]
            dets = dets[(wh[:, 0] >= 2) & (wh[:, 1] >= 2)]
        ref = torch.empty((1, 3, H, W), device="meta")  # scale=1 (원본좌표)
        return dets, ref
