"""분석 스레드(단일) — 공유 TRT 검출·ReID + 카메라별 BoostTrack 인스턴스 (M3).

FrameQueue에서 FrameItem을 pull → 공유 TRT 검출 → cam_id별 트래커
update(공유 TRT ReID 임베딩) → TrackedObject 목록을
on_tracks(cam_id, ts, tracks) 콜백으로 전달 (계약 §2).

설계 근거:
- GPU 직렬 사용(스레드 1개)이라 TRT 엔진에 락 불필요 (설계서 §4.2).
- 트래커는 카메라별 독립 인스턴스 + per_instance_ids=True → ID 공간 격리.
- ReID: BoostTrack이 만드는 EmbeddingComputer는 모델을 lazy-load하므로,
  compute_embedding을 공유 GPUEmbeddingComputer(TRT)로 패치하면 카메라 수와
  무관하게 ReID 모델은 1회만 로드된다 (src/inference_gpu.py 패턴).
- 검출기·ReID 조합은 추론 프로파일(model_zoo.py)로 갈아끼운다 — 기본은
  현재 선택값("auto"), 미설정 시 기존 YOLOX+FastReID.
- ECC 비활성(고정 CCTV), max_age는 카메라 analyze_fps 기준 시간 환산
  (원 설정 의도인 '2초 유지'를 프레임 수로 환산: 5fps → 10프레임).

주의: GeneralSettings(프로세스 전역)를 mot20/ECC-off로 설정한다 —
기존 단일영상 PoC(webui/server.py)와 같은 프로세스에서 함께 쓰지 말 것.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import numpy as np

from default_settings import GeneralSettings
import model_zoo
from src.inference_gpu import GPUEmbeddingComputer
from src.inference_trt import TRTDetector, TRTReID
from system.contracts import FrameItem, TrackedObject
from system.ingest.frame_queue import FrameQueue
from tracker.boost_track import BoostTrack

logger = logging.getLogger(__name__)

OnTracks = Callable[[str, float, list[TrackedObject]], None]

DEFAULT_YOLOX_ENGINE = "external/weights/trt/yolox_mot20_fp16.engine"
DEFAULT_REID_ENGINE = "external/weights/trt/fastreid_sbs_s50_fp16.engine"
TRACK_BUFFER_SEC = 2.0   # max_age 시간 환산 기준 (원 설정 fps*2 프레임 = 2초 의도)


class AnalyzerThread(threading.Thread):
    """FrameQueue → 공유 TRT 추론 → 카메라별 트래커 → on_tracks 콜백."""

    def __init__(
        self,
        frame_queue: FrameQueue,
        on_tracks: OnTracks,
        *,
        profile: str | None = "auto",
        yolox_engine: str = DEFAULT_YOLOX_ENGINE,
        reid_engine: str = DEFAULT_REID_ENGINE,
        input_size: tuple[int, int] = (896, 1600),
        det_thresh: float = 0.4,
        use_reid: bool = True,
        camera_fps: dict[str, float] | None = None,
        default_fps: float = 5.0,
        track_buffer_sec: float = TRACK_BUFFER_SEC,
    ) -> None:
        """profile: 추론 프로파일 id (model_zoo.py). 기본 "auto" = 현재
        선택값(:8900 UI 설정 → INFER_PROFILE → 기존 스택). None을 주면
        프로파일을 무시하고 개별 engine 인자를 쓴다(구 동작·테스트용)."""
        super().__init__(daemon=True, name="analyzer")
        self.queue = frame_queue
        self.on_tracks = on_tracks
        self.camera_fps = dict(camera_fps or {})
        self.default_fps = default_fps
        self.track_buffer_sec = track_buffer_sec
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()          # 트래커 dict 접근 보호

        self.profile = model_zoo.resolve(None if profile == "auto" else profile) \
            if profile is not None else None
        if self.profile is not None:
            input_size = self.profile.detector.input_size
            det_thresh = self.profile.tracker.det_thresh
        self.input_size = input_size

        # 전역 설정 — 이 프로세스의 모든 트래커 인스턴스에 공통 적용
        GeneralSettings.values["dataset"] = "mot20"
        GeneralSettings.values["test_dataset"] = True
        GeneralSettings.values["use_embedding"] = use_reid
        GeneralSettings.values["use_ecc"] = False        # 고정 CCTV — ECC 비활성
        GeneralSettings.values["det_thresh"] = det_thresh

        # 공유 TRT 엔진 — 프로세스에 1회 로드, 이 스레드가 직렬 사용
        # (crop 크기는 ReID 모델마다 다르다 — FastReID 128×384, CLIP-ReID 128×256)
        crop = (128, 384)
        if self.profile is not None:
            self.detector = model_zoo.build_detector(self.profile)
        else:
            self.detector = TRTDetector(yolox_engine, input_size=input_size)
        self._trt_reid = None
        self._gpu_embedder: GPUEmbeddingComputer | None = None
        if use_reid:
            if self.profile is not None:
                self._trt_reid, crop = model_zoo.build_reid(self.profile)
            else:
                self._trt_reid = TRTReID(reid_engine)
            self._gpu_embedder = GPUEmbeddingComputer(self._trt_reid, crop_size=crop)
        logger.info("추론 프로파일: %s", self.profile.label if self.profile else "(직접 지정)")

        self._trackers: dict[str, BoostTrack] = {}

        # 실측 통계
        self._stats_lock = threading.Lock()
        self._frames = 0
        self._frames_by_cam: dict[str, int] = {}
        self._infer_ms_sum = 0.0
        self._infer_ms_last = 0.0
        self._lag_sum = 0.0          # 큐 대기 지연 (수신 ts → 분석 시작)

    # ------------------------------------------------------------ 외부 API
    def stop(self) -> None:
        self._stop_evt.set()

    def set_camera_fps(self, cam_id: str, fps: float) -> None:
        """analyze_fps 변경 반영 — 다음 트래커 생성부터 적용."""
        self.camera_fps[cam_id] = fps

    def remove_camera(self, cam_id: str) -> None:
        """카메라 제거 시 트래커 인스턴스 폐기 (ID 공간도 함께 리셋)."""
        with self._lock:
            self._trackers.pop(cam_id, None)

    def stats(self) -> dict:
        with self._stats_lock:
            n = self._frames
            return {
                "frames": n,
                "frames_by_cam": dict(self._frames_by_cam),
                "avg_infer_ms": (self._infer_ms_sum / n) if n else 0.0,
                "last_infer_ms": self._infer_ms_last,
                "avg_queue_lag_s": (self._lag_sum / n) if n else 0.0,
                "queue_size": self.queue.qsize(),
                "queue_dropped": self.queue.dropped,
            }

    # ------------------------------------------------------------ 메인 루프
    def run(self) -> None:
        logger.info("AnalyzerThread 시작 (input_size=%s)", self.input_size)
        while not self._stop_evt.is_set():
            item = self.queue.get(timeout=0.5)
            if item is None:
                continue
            try:
                self._process(item)
            except Exception:
                logger.exception("[%s] 프레임 분석 실패 (seq=%d)", item.cam_id, item.seq)
        logger.info("AnalyzerThread 종료")

    def _process(self, item: FrameItem) -> None:
        t0 = time.perf_counter()
        lag = max(time.time() - item.ts, 0.0)

        # 검출기 공통 인터페이스 — detect_frame(bgr) -> (dets, scale_ref).
        # dets 좌표계는 검출기마다 다르고(YOLOX=letterbox, YOLO26/RF-DETR=원본),
        # scale_ref의 shape가 그 차이를 표현한다(BoostTrack.update와 동일 규약).
        pred, ref = self.detector.detect_frame(item.frame)
        h, w = item.frame.shape[:2]
        scale_r = min(ref.shape[2] / h, ref.shape[3] / w)

        tracker = self._tracker_for(item.cam_id)
        targets = tracker.update(pred, ref, item.frame,
                                 f"{item.cam_id}:{item.seq}")

        # 트랙별 '실제 검출 점수' — 트래커 출력 conf는 내부 신뢰도(부스팅 포함)라
        # 오탐 연명 트랙도 높게 나온다. 원본 검출과 IoU 매칭해 진짜 점수를 싣는다
        # (TrackedObject.conf 계약 의미). 미매칭(coasting) 프레임은 0.0.
        det_xyxy, det_scores = self._frame_dets(pred, scale_r)

        tracks: list[TrackedObject] = []
        for t in np.asarray(targets).reshape(-1, targets.shape[1] if targets.size else 6):
            x1, y1, x2, y2, tid = t[0], t[1], t[2], t[3], int(t[4])
            conf = self._matched_score(np.array([x1, y1, x2, y2], np.float64),
                                       det_xyxy, det_scores)
            tracks.append(TrackedObject(
                cam_id=item.cam_id,
                local_track_id=tid,
                foot_uv=((float(x1) + float(x2)) / 2.0, float(y2)),
                bbox_xyxy=(float(x1), float(y1), float(x2), float(y2)),
                conf=conf,
                ts=item.ts,
            ))

        infer_ms = (time.perf_counter() - t0) * 1000.0
        with self._stats_lock:
            self._frames += 1
            self._frames_by_cam[item.cam_id] = self._frames_by_cam.get(item.cam_id, 0) + 1
            self._infer_ms_sum += infer_ms
            self._infer_ms_last = infer_ms
            self._lag_sum += lag

        self.on_tracks(item.cam_id, item.ts, tracks)

    # ------------------------------------------------------------ 내부
    @staticmethod
    def _frame_dets(pred, scale_r: float):
        """검출 결과 → (원본 px xyxy[N,4], 점수[N]). pred 없으면 빈 배열."""
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
        """트랙 박스와 최대 IoU 검출의 점수 (IoU<임계 → 0.0 = 이번 프레임 미검출)."""
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

    def _tracker_for(self, cam_id: str) -> BoostTrack:
        with self._lock:
            tracker = self._trackers.get(cam_id)
            if tracker is not None:
                return tracker
            fps = self.camera_fps.get(cam_id, self.default_fps)
            max_age = max(int(round(fps * self.track_buffer_sec)), 3)
            tracker = BoostTrack(per_instance_ids=True, max_age=max_age)
            # ReID를 공유 TRT 임베더로 패치 — 카메라 수와 무관하게 모델 1개
            if self._gpu_embedder is not None and tracker.embedder is not None:
                tracker.embedder.compute_embedding = self._gpu_embedder.compute_embedding
                tracker.embedder.model = self._trt_reid   # lazy torch 로드 차단
            assert tracker.ecc is None, "고정 CCTV 전제 — ECC는 비활성이어야 함"
            self._trackers[cam_id] = tracker
            logger.info("[%s] 트래커 생성 (fps=%.3g → max_age=%d)", cam_id, fps, max_age)
            return tracker
