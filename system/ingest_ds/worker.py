"""DeepStream 인제스트+추론 워커 — 컨테이너(macs-deepstream:9.0) 전용 메인.

데이터 흐름:
  nvurisrcbin(카메라 N, RTSP 자동 재접속) → queue(leaky) → nvstreammux(배치)
  → nvvideoconvert → RGBA(NVMM, unified) → appsink 콜백
      · NvDsBatchMeta 순회, pad_index → cam_id 매핑
      · 카메라별 analyze_fps 시간 게이트 (디코드는 풀레이트, 추론만 게이트)
      · pyds.get_nvds_buf_surface_gpu → cupy UnownedMemory → torch zero-copy
      · GPU letterbox 전처리(dataset.preproc 재현) + ReID용 RGB 텐서 확보
  → 추론 스레드: 배치 YOLOX(TRT dynamic) → 카메라별 BoostTrack
      (per_instance_ids, ECC off, GPU crop TRT ReID)
  → TrackedObject 계약(system/contracts.py)과 동일 필드의 dict를 ZMQ PUSH.

프레임 픽셀은 프로세스 밖으로 절대 내보내지 않는다 (트랙 메타만 전송).
호스트 측 수신은 system/ingest_ds/bridge.py.

실행(컨테이너 안, /workspace = 레포 마운트):
  python3 -m system.ingest_ds.worker \
      --cams system/ingest_ds/configs/cams_4ch.json \
      --det-engine external/weights/trt_ds/yolox_mot20_fp16_dyn_b16.engine \
      --reid-engine external/weights/trt_ds/fastreid_sbs_s50_fp16_dyn_b256.engine
호스트에서는 system/ingest_ds/run_worker.sh 래퍼 사용.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import logging
import queue
import signal
import sys
import threading
import time
import types
from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------- 의존성 스텁
# tracker/ 패키지는 import 시 torchreid(embedding.py)·fast_reid(fastreid_adaptor)
# ·assets(huggingface_hub)를 끌어오지만, 이 워커는 ReID를 TRT 엔진으로 대체하므로
# 해당 코드는 실행되지 않는다 — 컨테이너에 무거운 학습 프레임워크를 설치하는
# 대신 import 전에 스텁을 심는다 (system/tracking/analyzer.py의 TRT 패치와
# 같은 원리, 컨테이너 사정에 맞춘 확장).
sys.modules.setdefault("torchreid", types.ModuleType("torchreid"))
sys.modules.setdefault("assets", types.ModuleType("assets"))
_fr_stub = types.ModuleType("external.adaptors.fastreid_adaptor")


class _FastReIDStub:  # noqa: D401 — 컨테이너에서는 torch FastReID를 로드하지 않는다
    def __init__(self, *a, **k):
        raise RuntimeError("컨테이너에서는 FastReID torch 모델 대신 TRT ReID를 쓴다")


_fr_stub.FastReID = _FastReIDStub
sys.modules.setdefault("external.adaptors.fastreid_adaptor", _fr_stub)

import cupy as cp                     # noqa: E402
import torch                          # noqa: E402
import torch.nn.functional as F       # noqa: E402
import zmq                            # noqa: E402

import gi                             # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst   # noqa: E402

import pyds                           # noqa: E402

from default_settings import GeneralSettings                     # noqa: E402
from system.ingest_ds.gpu_embedding import DsGpuEmbeddingComputer  # noqa: E402
from system.ingest_ds.trt_infer import BatchDetector, TRTReID     # noqa: E402
from tracker.boost_track import BoostTrack                        # noqa: E402

logger = logging.getLogger("ingest_ds.worker")

MUX_W, MUX_H = 1920, 1080            # nvstreammux 출력 (모든 소스 스케일 통일)
INPUT_H, INPUT_W = 896, 1600         # YOLOX 입력 (기존 경로와 동일)
TRACK_BUFFER_SEC = 2.0               # max_age 시간 환산 (analyzer.py와 동일)
STATS_INTERVAL_SEC = 5

_PyCapsule_GetPointer = ctypes.pythonapi.PyCapsule_GetPointer
_PyCapsule_GetPointer.restype = ctypes.c_void_p
_PyCapsule_GetPointer.argtypes = [ctypes.py_object, ctypes.c_char_p]


# ------------------------------------------------------------------ 자료구조


@dataclass
class _Item:
    """appsink 콜백 → 추론 스레드로 넘기는 프레임 1건 (전부 GPU 텐서)."""
    cam_id: str
    ts: float
    seq: int
    det: torch.Tensor    # (3, 896, 1600) float32 — letterbox 전처리 완료
    rgb: torch.Tensor    # (3, 1080, 1920) uint8 RGB — ReID crop 원본
    scale_r: float
    src_w: int           # 카메라 원본 해상도 — 트랙 좌표를 원본 px로 역스케일
    src_h: int           #   (TrackedObject 계약: 좌표는 '카메라 프레임 px')


class _CamGate:
    """analyze_fps 시간 게이트 — 등간격 유지, 밀리면 재동기화."""

    def __init__(self, cam_id: str, fps: float):
        self.cam_id = cam_id
        self.fps = fps
        self.interval = 1.0 / fps if fps > 0 else 0.0
        self._next_due = 0.0
        self.selected = 0
        self.seq = 0

    def due(self, now: float) -> bool:
        if self.interval <= 0:
            return True
        if now < self._next_due:
            return False
        self._next_due += self.interval
        if self._next_due < now:       # 스트림 끊김 등으로 크게 밀림 → 재동기화
            self._next_due = now + self.interval
        return True


class _OldestDropQueue:
    """가득 차면 가장 오래된 항목을 버리는 큐 (system/ingest FrameQueue 패턴)."""

    def __init__(self, maxsize: int = 64):
        self.q: queue.Queue[_Item] = queue.Queue(maxsize=maxsize)
        self.dropped = 0

    def put(self, item: _Item) -> None:
        while True:
            try:
                self.q.put_nowait(item)
                return
            except queue.Full:
                try:
                    self.q.get_nowait()
                    self.dropped += 1
                except queue.Empty:
                    pass

    def get(self, timeout: float) -> _Item | None:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None


# ------------------------------------------------------------------ 워커 본체


class DsWorker:
    def __init__(self, cams: list[dict], args: argparse.Namespace):
        self.cams = cams
        self.batch_size = args.batch_size
        self.gather_sec = args.gather_ms / 1000.0
        self.det_thresh = args.det_thresh
        self.use_reid = not args.no_reid
        self.queue = _OldestDropQueue(maxsize=args.queue_size)
        self._stop_evt = threading.Event()
        self._use_gpu_map = not args.copy_mode   # 실패 시 CPU 복사 폴백으로 전환

        # --- ZMQ PUSH (논블로킹 — 수신자 없으면 버리고 카운트) ---
        self._zmq_ctx = zmq.Context.instance()
        self._sock = self._zmq_ctx.socket(zmq.PUSH)
        self._sock.setsockopt(zmq.SNDHWM, 1000)
        self._sock.setsockopt(zmq.LINGER, 500)
        self._sock.bind(args.zmq_bind)
        self._codec = args.codec
        if self._codec == "msgpack":
            import msgpack  # 컨테이너에 설치됨 — 호스트 bridge는 양쪽 자동 판별
            self._pack = lambda o: msgpack.packb(o, use_bin_type=True)
        else:
            self._pack = lambda o: json.dumps(o).encode()

        # --- 공유 TRT 엔진 (프로세스 1회 로드, 추론 스레드가 직렬 사용) ---
        self.detector = BatchDetector(args.det_engine)
        self._embedder: DsGpuEmbeddingComputer | None = None
        if self.use_reid:
            self._trt_reid = TRTReID(args.reid_engine)
            self._embedder = DsGpuEmbeddingComputer(self._trt_reid, crop_size=(128, 384))

        # --- 트래커 전역 설정 (system/tracking/analyzer.py와 동일) ---
        GeneralSettings.values["dataset"] = "mot20"
        GeneralSettings.values["test_dataset"] = True
        GeneralSettings.values["use_embedding"] = self.use_reid
        GeneralSettings.values["use_ecc"] = False          # 고정 CCTV — ECC 비활성
        GeneralSettings.values["det_thresh"] = self.det_thresh
        self._trackers: dict[str, BoostTrack] = {}
        self._cam_fps = {c["cam_id"]: float(c.get("analyze_fps", 5.0)) for c in cams}
        # BoostTrack.update의 img_numpy 인자는 shape 참조용으로만 쓰인다
        # (ECC off + 임베더가 GPU 텐서 사용) — 더미 1개를 공유한다.
        self._dummy_np = np.empty((MUX_H, MUX_W, 3), dtype=np.uint8)

        # --- 통계 ---
        self._st_lock = threading.Lock()
        self._st_analyzed: dict[str, int] = {}    # 카메라별 추론 완료 프레임
        self._st_selected: dict[str, int] = {}    # 카메라별 게이트 통과 프레임
        self._st_batches = 0
        self._st_batch_frames = 0
        self._st_infer_ms = 0.0
        self._st_zmq_drops = 0
        self._st_gpu_map_fail = 0
        self._st_prev: dict | None = None

        self._gates: list[_CamGate] = [
            _CamGate(c["cam_id"], float(c.get("analyze_fps", 5.0))) for c in cams]

        Gst.init(None)
        self._build_pipeline()
        self._infer_thread = threading.Thread(target=self._infer_loop,
                                              name="infer", daemon=True)

    # ------------------------------------------------------- GStreamer 구성
    @staticmethod
    def _set_if(elem, prop: str, value) -> None:
        """엘리먼트에 프로퍼티가 있을 때만 설정 (DS 버전별 차이 흡수)."""
        if elem.find_property(prop) is not None:
            elem.set_property(prop, value)
        else:
            logger.warning("%s: 프로퍼티 없음 — 건너뜀: %s", elem.get_name(), prop)

    def _build_pipeline(self) -> None:
        self.pipeline = Gst.Pipeline.new("ds-ingest")

        mux = Gst.ElementFactory.make("nvstreammux", "mux")
        mux.set_property("batch-size", len(self.cams))
        mux.set_property("batched-push-timeout", 40000)
        mux.set_property("width", MUX_W)
        mux.set_property("height", MUX_H)
        mux.set_property("live-source", 1)
        mux.set_property("nvbuf-memory-type", 3)   # NVBUF_MEM_CUDA_UNIFIED (dGPU 필수)
        self.pipeline.add(mux)

        for i, cam in enumerate(self.cams):
            src = Gst.ElementFactory.make("nvurisrcbin", f"src_{i}")
            src.set_property("uri", cam["rtsp"])
            # RTSP 안정화 — Edge-Device reconnect.py의 역할을 DS 내장 기능으로 대체
            self._set_if(src, "rtsp-reconnect-interval", 10)
            self._set_if(src, "rtsp-reconnect-attempts", -1)   # 무한 재시도
            self._set_if(src, "select-rtp-protocol", 4)        # TCP 고정
            self._set_if(src, "drop-on-latency", True)

            q = Gst.ElementFactory.make("queue", f"q_{i}")
            q.set_property("leaky", 2)               # downstream(oldest) drop
            q.set_property("max-size-buffers", 4)

            self.pipeline.add(src)
            self.pipeline.add(q)

            sinkpad = mux.request_pad_simple(f"sink_{i}")   # pad_index == i 고정
            q.get_static_pad("src").link(sinkpad)
            src.connect("pad-added", self._on_pad_added, q)

        conv = Gst.ElementFactory.make("nvvideoconvert", "conv")
        conv.set_property("nvbuf-memory-type", 3)
        capsf = Gst.ElementFactory.make("capsfilter", "caps_rgba")
        capsf.set_property(
            "caps", Gst.Caps.from_string("video/x-raw(memory:NVMM), format=RGBA"))
        sink = Gst.ElementFactory.make("appsink", "sink")
        sink.set_property("emit-signals", True)
        sink.set_property("sync", False)
        sink.set_property("max-buffers", 8)
        sink.set_property("drop", True)
        sink.connect("new-sample", self._on_new_sample)

        for e in (conv, capsf, sink):
            self.pipeline.add(e)
        mux.link(conv)
        conv.link(capsf)
        capsf.link(sink)

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

    def _on_pad_added(self, src, pad, q) -> None:
        caps = pad.get_current_caps() or pad.query_caps()
        name = caps.get_structure(0).get_name() if caps.get_size() else ""
        if not name.startswith("video"):
            return
        sinkpad = q.get_static_pad("sink")
        if sinkpad.is_linked():
            return
        ret = pad.link(sinkpad)
        if ret != Gst.PadLinkReturn.OK:
            logger.error("%s: 소스 pad 링크 실패 (%s)", src.get_name(), ret)

    def _on_bus_message(self, bus, msg) -> None:
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            # nvurisrcbin이 내부 재접속을 계속하므로 파이프라인은 유지한다
            logger.error("GST ERROR from %s: %s (%s)", msg.src.get_name(), err, dbg)
        elif t == Gst.MessageType.EOS:
            logger.warning("GST EOS — 라이브 소스에서는 발생하지 않아야 함 (송출 중단?)")

    # ------------------------------------------------------- appsink 콜백
    def _on_new_sample(self, sink) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(buf))
        if batch_meta is None:
            return Gst.FlowReturn.OK

        now = time.time()
        pushed = False
        l_frame = batch_meta.frame_meta_list
        while l_frame is not None:
            try:
                fm = pyds.NvDsFrameMeta.cast(l_frame.data)
            except StopIteration:
                break
            gate = self._gates[fm.pad_index]
            if gate.due(now):
                try:
                    rgba = self._map_frame(buf, fm)
                    det, rgb, r = self._preproc(rgba)
                    gate.seq += 1
                    self.queue.put(_Item(gate.cam_id, now, gate.seq, det, rgb, r,
                                         fm.source_frame_width,
                                         fm.source_frame_height))
                    pushed = True
                    with self._st_lock:
                        self._st_selected[gate.cam_id] = \
                            self._st_selected.get(gate.cam_id, 0) + 1
                except Exception:
                    logger.exception("[%s] 프레임 매핑/전처리 실패", gate.cam_id)
            try:
                l_frame = l_frame.next
            except StopIteration:
                break

        if pushed:
            # NvBufSurface는 콜백 반환 후 재사용된다 — 복사 커널 완료를 보장
            torch.cuda.current_stream().synchronize()
        return Gst.FlowReturn.OK

    def _map_frame(self, buf, fm) -> torch.Tensor:
        """NvBufSurface → torch (H, W, 4) uint8 CUDA 텐서 (기본 zero-copy)."""
        if self._use_gpu_map:
            try:
                dtype, shape, strides, dataptr, size = \
                    pyds.get_nvds_buf_surface_gpu(hash(buf), fm.batch_id)
                ptr = _PyCapsule_GetPointer(dataptr, None)
                mem = cp.cuda.UnownedMemory(ptr, size, None)
                arr = cp.ndarray(shape=shape, dtype=dtype,
                                 memptr=cp.cuda.MemoryPointer(mem, 0),
                                 strides=strides, order="C")
                arr = cp.ascontiguousarray(arr)   # pitch 패딩 대비 (대개 no-op)
                # DLPack은 불가 — unified memory가 kDLCUDAManaged(13)로 수출되어
                # torch가 거부한다. __cuda_array_interface__ 경유(as_tensor)는
                # 일반 디바이스 포인터로 노출되므로 zero-copy 공유가 된다.
                return torch.as_tensor(arr, device="cuda")
            except Exception:
                with self._st_lock:
                    self._st_gpu_map_fail += 1
                logger.exception("get_nvds_buf_surface_gpu 실패 — CPU 복사 폴백으로 전환")
                self._use_gpu_map = False
        # 폴백: unified memory를 np로 읽어 복사 (성능 저하 — 통계로 관찰)
        view = pyds.get_nvds_buf_surface(hash(buf), fm.batch_id)
        return torch.from_numpy(np.array(view, copy=True)).cuda()

    def _preproc(self, rgba: torch.Tensor):
        """dataset.preproc(letterbox 114, top-left, /255) GPU 재현.

        원본은 uint8 cv2.resize(bilinear) 후 float 변환, 여기는 float 보간이라
        ±1/255 수준 차이만 존재. RGBA 소스라 BGR→RGB 플립은 불필요.
        """
        rgb = rgba[..., :3].permute(2, 0, 1).contiguous()   # (3,H,W) u8 — 버퍼와 분리
        h, w = rgb.shape[1], rgb.shape[2]
        r = min(INPUT_H / h, INPUT_W / w)
        rh, rw = int(h * r), int(w * r)
        resized = F.interpolate(rgb.unsqueeze(0).float(), size=(rh, rw),
                                mode="bilinear", align_corners=False)[0]
        canvas = torch.full((3, INPUT_H, INPUT_W), 114.0,
                            dtype=torch.float32, device=rgb.device)
        canvas[:, :rh, :rw] = resized
        canvas /= 255.0
        return canvas, rgb, r

    # ------------------------------------------------------- 추론 스레드
    def _infer_loop(self) -> None:
        logger.info("추론 스레드 시작 (batch<=%d, gather=%.0fms)",
                    self.batch_size, self.gather_sec * 1000)
        while not self._stop_evt.is_set():
            first = self.queue.get(timeout=0.5)
            if first is None:
                continue
            items = [first]
            deadline = time.monotonic() + self.gather_sec
            while len(items) < self.batch_size:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    break
                nxt = self.queue.get(timeout=remain)
                if nxt is None:
                    break
                items.append(nxt)

            t0 = time.perf_counter()
            try:
                batch = torch.stack([it.det for it in items])
                preds = self.detector.detect(batch)
                for it, pred in zip(items, preds):
                    self._track_one(it, pred)
            except Exception:
                logger.exception("배치 추론 실패 (%d프레임)", len(items))
                continue
            infer_ms = (time.perf_counter() - t0) * 1000.0
            with self._st_lock:
                self._st_batches += 1
                self._st_batch_frames += len(items)
                self._st_infer_ms += infer_ms
        logger.info("추론 스레드 종료")

    def _tracker_for(self, cam_id: str) -> BoostTrack:
        tracker = self._trackers.get(cam_id)
        if tracker is not None:
            return tracker
        fps = self._cam_fps.get(cam_id, 5.0)
        max_age = max(int(round(fps * TRACK_BUFFER_SEC)), 3)
        tracker = BoostTrack(per_instance_ids=True, max_age=max_age)
        if self._embedder is not None and tracker.embedder is not None:
            tracker.embedder.compute_embedding = self._embedder.compute_embedding
            tracker.embedder.model = self._trt_reid    # lazy torch 로드 차단
        assert tracker.ecc is None, "고정 CCTV 전제 — ECC는 비활성이어야 함"
        self._trackers[cam_id] = tracker
        logger.info("[%s] 트래커 생성 (fps=%.3g → max_age=%d)", cam_id, fps, max_age)
        return tracker

    def _track_one(self, item: _Item, pred) -> None:
        tracker = self._tracker_for(item.cam_id)
        if self._embedder is not None:
            self._embedder.set_frame(item.rgb)

        # BoostTrack.update는 img_tensor/img_numpy를 shape 계산에만 쓴다
        # (ECC off, 임베더는 set_frame 텐서 사용) — det 텐서 뷰와 더미로 대체.
        targets = tracker.update(pred, item.det.unsqueeze(0), self._dummy_np,
                                 f"{item.cam_id}:{item.seq}")

        # 트랙 conf = 원본 검출과 IoU 매칭한 실제 점수 (analyzer.py와 동일 규칙)
        det_xyxy, det_scores = self._frame_dets(pred, item.scale_r)

        # nvstreammux가 모든 소스를 MUX_W×MUX_H로 스케일하므로 트래킹 좌표는
        # mux px다 — 계약(카메라 프레임 px)에 맞게 원본 해상도로 역스케일.
        kx = item.src_w / MUX_W if item.src_w else 1.0
        ky = item.src_h / MUX_H if item.src_h else 1.0

        tracks = []
        for t in np.asarray(targets).reshape(
                -1, targets.shape[1] if targets.size else 6):
            x1, y1, x2, y2, tid = t[0], t[1], t[2], t[3], int(t[4])
            conf = self._matched_score(
                np.array([x1, y1, x2, y2], np.float64), det_xyxy, det_scores)
            x1, x2 = float(x1) * kx, float(x2) * kx
            y1, y2 = float(y1) * ky, float(y2) * ky
            tracks.append({
                "local_track_id": tid,
                "foot_uv": [(x1 + x2) / 2.0, y2],
                "bbox_xyxy": [x1, y1, x2, y2],
                "conf": conf,
            })

        self._send({"cam_id": item.cam_id, "ts": item.ts, "tracks": tracks})
        with self._st_lock:
            self._st_analyzed[item.cam_id] = self._st_analyzed.get(item.cam_id, 0) + 1

    @staticmethod
    def _frame_dets(pred, scale_r: float):
        """검출 결과 → (원본 px xyxy[N,4], 점수[N]) — analyzer.py 이식."""
        if pred is None:
            return np.zeros((0, 4)), np.zeros(0)
        p = pred.cpu().numpy() if hasattr(pred, "cpu") else np.asarray(pred)
        p = p.reshape(-1, p.shape[-1]) if p.size else p.reshape(0, 6)
        if not len(p):
            return np.zeros((0, 4)), np.zeros(0)
        xyxy = p[:, :4] / scale_r
        scores = p[:, 4] * p[:, 5] if p.shape[1] >= 6 else p[:, 4]
        return xyxy, scores

    @staticmethod
    def _matched_score(box, det_xyxy, det_scores, iou_th: float = 0.5) -> float:
        """트랙 박스와 최대 IoU 검출의 점수 (analyzer.py 이식)."""
        if not len(det_xyxy):
            return 0.0
        x1 = np.maximum(box[0], det_xyxy[:, 0]); y1 = np.maximum(box[1], det_xyxy[:, 1])
        x2 = np.minimum(box[2], det_xyxy[:, 2]); y2 = np.minimum(box[3], det_xyxy[:, 3])
        inter = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
        a1 = (box[2] - box[0]) * (box[3] - box[1])
        a2 = (det_xyxy[:, 2] - det_xyxy[:, 0]) * (det_xyxy[:, 3] - det_xyxy[:, 1])
        iou = inter / np.maximum(a1 + a2 - inter, 1e-9)
        k = int(np.argmax(iou))
        return float(det_scores[k]) if iou[k] >= iou_th else 0.0

    def _send(self, payload: dict) -> None:
        try:
            self._sock.send(self._pack(payload), flags=zmq.DONTWAIT)
        except zmq.Again:
            with self._st_lock:
                self._st_zmq_drops += 1

    # ------------------------------------------------------- 통계/수명주기
    def _log_stats(self) -> bool:
        with self._st_lock:
            cur = {
                "analyzed": dict(self._st_analyzed),
                "selected": dict(self._st_selected),
                "batches": self._st_batches,
                "batch_frames": self._st_batch_frames,
                "infer_ms": self._st_infer_ms,
                "zmq_drops": self._st_zmq_drops,
            }
        prev = self._st_prev or {"analyzed": {}, "selected": {},
                                 "batches": 0, "batch_frames": 0,
                                 "infer_ms": 0.0, "zmq_drops": 0}
        self._st_prev = cur

        d_batches = cur["batches"] - prev["batches"]
        d_frames = cur["batch_frames"] - prev["batch_frames"]
        d_ms = cur["infer_ms"] - prev["infer_ms"]
        fps = {c: (cur["analyzed"].get(c, 0) - prev["analyzed"].get(c, 0))
               / STATS_INTERVAL_SEC for c in self._cam_fps}
        logger.info(
            "STATS fps=%s | batch_avg=%.2f infer_avg=%.1fms | q=%d qdrop=%d "
            "zmqdrop=%d gpumap=%s",
            {k: round(v, 2) for k, v in fps.items()},
            (d_frames / d_batches) if d_batches else 0.0,
            (d_ms / d_batches) if d_batches else 0.0,
            self.queue.q.qsize(), self.queue.dropped,
            cur["zmq_drops"], "on" if self._use_gpu_map else "OFF(copy)")
        return True   # GLib timeout 유지

    def run(self, duration: float = 0.0) -> None:
        self._infer_thread.start()
        self.pipeline.set_state(Gst.State.PLAYING)
        loop = GLib.MainLoop()
        GLib.timeout_add_seconds(STATS_INTERVAL_SEC, self._log_stats)
        if duration > 0:
            GLib.timeout_add(int(duration * 1000), lambda: (loop.quit(), False)[1])
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: loop.quit())
        logger.info("파이프라인 PLAYING — %d 카메라, zero-copy=%s",
                    len(self.cams), self._use_gpu_map)
        try:
            loop.run()
        finally:
            logger.info("종료 중 …")
            self.pipeline.set_state(Gst.State.NULL)
            self._stop_evt.set()
            self._infer_thread.join(timeout=5)
            self._log_stats()
            self._sock.close()


# ------------------------------------------------------------------ 엔트리


def main() -> None:
    ap = argparse.ArgumentParser(description="MACS DeepStream 인제스트+추론 워커")
    ap.add_argument("--cams", required=True,
                    help="카메라 JSON — [{cam_id, rtsp, analyze_fps}, ...]")
    ap.add_argument("--gpu", type=int, default=0,
                    help="컨테이너 내 GPU 순번 (--gpus device=N으로 격리 시 0)")
    ap.add_argument("--batch-size", type=int, default=8,
                    help="추론 배치 상한 (엔진 max 16)")
    ap.add_argument("--zmq-bind", default="tcp://*:5701")
    ap.add_argument("--det-engine",
                    default="external/weights/trt_ds/yolox_mot20_fp16_dyn_b16.engine")
    ap.add_argument("--reid-engine",
                    default="external/weights/trt_ds/fastreid_sbs_s50_fp16_dyn_b256.engine")
    ap.add_argument("--det-thresh", type=float, default=0.4)
    ap.add_argument("--no-reid", action="store_true")
    ap.add_argument("--gather-ms", type=float, default=100.0,
                    help="배치 모으기 대기 (ms) — 지연 vs 배치효율 트레이드오프")
    ap.add_argument("--queue-size", type=int, default=64)
    ap.add_argument("--codec", choices=("json", "msgpack"), default="json")
    ap.add_argument("--copy-mode", action="store_true",
                    help="zero-copy 대신 CPU 복사 경로 강제 (디버그/비교용)")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="N초 후 자동 종료 (0=무한, 테스트용)")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    with open(args.cams) as f:
        cams = json.load(f)
    if not cams:
        raise SystemExit("카메라 목록이 비어 있음")

    torch.cuda.set_device(args.gpu)
    cp.cuda.Device(args.gpu).use()

    worker = DsWorker(cams, args)
    worker.run(duration=args.duration)


if __name__ == "__main__":
    main()
