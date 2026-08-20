"""YOLO26(Ultralytics, end2end) TensorRT 검출기 — ultralytics 라이브러리 불필요.

PIASPACE 제공 `yolo26l_v6.3.onnx`(사람 전용 파인튜닝)를 TRT로 구워 직접 돌린다.
원본 참조 구현은 `ultralytics.YOLO(engine).predict()`를 쓰지만, 그렇게 하면
AGPL 패키지와 torch 백엔드가 운영 경로(DS 컨테이너 포함)에 끌려 들어온다.
RF-DETR과 같은 방식(`src/rfdetr_trt.py`)으로 전·후처리를 복제한다.

  전처리 : BGR→RGB → letterbox(imgsz, pad 114, **중앙 배치**) → /255 → NCHW float32
  후처리 : end2end(NMS 내장) 출력 (B,300,6)=[x1,y1,x2,y2,conf,cls] — letterbox
           좌표계 → 패딩 제거·스케일 복원 → 원본 좌표

  * 중앙 배치: Ultralytics LetterBox 기본값(center=True). YOLOX 계열(좌상단
    배치)과 다르므로 패딩 오프셋을 빼줘야 좌표가 맞는다.

공통 인터페이스(BoostTrackGPUInference 투트랙과 동일):
    detect_frame(bgr) -> (dets[N,5] xyxy+conf **원본좌표**, scale_ref_tensor)
BoostTrack.update는 ref의 shape로만 스케일을 내므로 ref=(1,3,H,W)면 scale=1.

배치 경로(DS 워커용):
    preprocess_batch(list[bgr]) -> (tensor(B,3,S,S), metas)
    decode_batch(raw, metas)    -> list[dets[N,5]]
"""
from __future__ import annotations

import cv2
import numpy as np
import torch

from src.inference_trt import TRTEngine

PAD_VALUE = 114.0


class YOLO26TRTDetector:
    """TensorRT YOLO26 사람 검출기 (BoostTrack 투트랙 드롭인)."""

    def __init__(self, engine_path: str, imgsz: int | None = None,
                 conf_thresh: float = 0.4, min_box_size: int = 16,
                 person_class: int = 0):
        self.engine = TRTEngine(engine_path)
        # 입력 해상도 자동 감지 — 엔진 바인딩이 고정(640)이면 그 값을 쓴다.
        if imgsz is None:
            ishape = tuple(self.engine.engine.get_tensor_shape(self.engine.input_names[0]))
            imgsz = int(ishape[2]) if len(ishape) >= 4 and ishape[2] > 0 else 640
        self.imgsz = int(imgsz)
        self.conf = float(conf_thresh)
        self.min_box = int(min_box_size)
        self.person = int(person_class)

    # ------------------------------------------------------------ 전처리
    def _letterbox(self, bgr: np.ndarray):
        """→ (chw float32 0~1 RGB, r, pad_x, pad_y). Ultralytics LetterBox(center=True) 복제."""
        h, w = bgr.shape[:2]
        s = self.imgsz
        r = min(s / h, s / w)
        nh, nw = int(round(h * r)), int(round(w * r))
        img = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_LINEAR) if (nw, nh) != (w, h) else bgr
        canvas = np.full((s, s, 3), PAD_VALUE, dtype=np.float32)
        dx, dy = (s - nw) // 2, (s - nh) // 2
        canvas[dy:dy + nh, dx:dx + nw] = img[:, :, ::-1]         # BGR→RGB
        chw = canvas.transpose(2, 0, 1) / 255.0
        return np.ascontiguousarray(chw, dtype=np.float32), r, dx, dy

    def preprocess_batch(self, frames: list[np.ndarray]):
        """여러 프레임 → (cuda tensor (B,3,S,S), metas[(r,dx,dy,W,H)])"""
        chws, metas = [], []
        for f in frames:
            chw, r, dx, dy = self._letterbox(f)
            chws.append(chw)
            metas.append((r, dx, dy, f.shape[1], f.shape[0]))
        batch = torch.from_numpy(np.stack(chws)).cuda()
        return batch, metas

    # ------------------------------------------------------------ 후처리
    def _decode(self, rows: torch.Tensor, meta) -> np.ndarray:
        """(300,6) letterbox 좌표 → 원본좌표 dets[N,5]."""
        r, dx, dy, W, H = meta
        out = rows.detach().float().cpu().numpy()
        if out.size == 0:
            return np.empty((0, 5), dtype=np.float32)
        conf, cls = out[:, 4], out[:, 5]
        keep = (conf >= self.conf) & (np.rint(cls) == self.person)
        if not keep.any():
            return np.empty((0, 5), dtype=np.float32)
        box = out[keep, :4].copy()
        box[:, [0, 2]] = (box[:, [0, 2]] - dx) / r
        box[:, [1, 3]] = (box[:, [1, 3]] - dy) / r
        box[:, [0, 2]] = box[:, [0, 2]].clip(0, W)
        box[:, [1, 3]] = box[:, [1, 3]].clip(0, H)
        if self.min_box > 0:                    # 너무 작은 박스는 트래커 노이즈
            side = np.minimum(box[:, 2] - box[:, 0], box[:, 3] - box[:, 1])
            ok = side >= self.min_box
            box, conf = box[ok], conf[keep][ok]
        else:
            conf = conf[keep]
        return np.concatenate([box, conf[:, None]], axis=1).astype(np.float32)

    def decode_batch(self, raw: torch.Tensor, metas) -> list[np.ndarray]:
        return [self._decode(raw[i], metas[i]) for i in range(len(metas))]

    # ------------------------------------------------------------ 추론
    @torch.no_grad()
    def detect_frame(self, bgr: np.ndarray):
        H, W = bgr.shape[:2]
        chw, r, dx, dy = self._letterbox(bgr)
        tensor = torch.from_numpy(chw).unsqueeze(0).cuda()
        raw = self.engine(tensor)[0]                       # (1,300,6)
        dets = self._decode(raw[0], (r, dx, dy, W, H))
        ref = torch.empty((1, 3, H, W), device="meta")     # scale=1 (원본좌표)
        return dets, ref

    @torch.no_grad()
    def detect_prepared(self, batch: torch.Tensor, metas) -> list[np.ndarray]:
        """이미 전처리된 배치(B,3,S,S)를 추론 — DS 워커 배치 경로."""
        raw = self.engine(batch.float())[0]
        return self.decode_batch(raw, metas)
