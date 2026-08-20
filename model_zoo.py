"""추론 프로파일 레지스트리 — 검출기·ReID·트래커 조합을 한 곳에서 갈아끼운다.

위치: 레포 루트 (default_settings.py·dataset.py와 같은 층). DS 컨테이너는
`src/__init__.py`가 끌어오는 무거운 의존(pycocotools 등)을 설치하지 않으므로
`src.` 아래 두면 워커가 import할 수 없다 — 실측으로 확인.

문제: 추론 스택을 호출하는 곳이 3군데(단일영상 `src/inference_gpu.py`,
멀티카메라 ffmpeg `system/tracking/analyzer.py`, DeepStream 컨테이너
`system/ingest_ds/worker.py`)인데 엔진 경로·crop 크기·임계값이 각자 하드코딩돼
있어, 모델을 바꾸려면 세 파일을 다 고쳐야 했다. 조합을 **프로파일**로 이름
붙여 여기에 모으고, 세 경로 모두 프로파일 id 하나만 받는다.

    profile = resolve("yolo26_clipreid")
    detector = build_detector(profile)          # 호스트(단일 프레임)
    reid, crop = build_reid(profile)            # (모듈, (W,H))
    apply_tracker_settings(profile)             # GeneralSettings 전역 반영

선택 값의 우선순위 (높은 쪽이 이김):
    1) 호출자가 명시한 profile 인자
    2) data/infer_profile.json  ← :8900 UI가 쓰는 영속 설정
    3) 환경변수 INFER_PROFILE
    4) DEFAULT_PROFILE ("yolox_fastreid" — 기존 동작)

엔진은 GPU 아키텍처·TRT 버전마다 따로 구워야 한다. 호스트(conda TRT)는
`external/weights/trt/`, DS 컨테이너(TRT 버전 다름)는 `external/weights/trt_ds/`
에 같은 파일명으로 둔다 — 그래서 스펙은 파일명만 갖고 디렉토리는 ds 플래그로
고른다. 빌드는 `tools/build_trt_engine.py`.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

TRT_DIR = "external/weights/trt"
TRT_DS_DIR = "external/weights/trt_ds"
STATE_FILE = Path("data/infer_profile.json")
DEFAULT_PROFILE = "yolox_fastreid"


@dataclass(frozen=True)
class DetectorSpec:
    kind: str                       # "yolox" | "yolo26" | "rfdetr"
    engine: str                     # 파일명 (디렉토리는 호스트/DS로 결정)
    ds_engine: str = ""             # 배치(DS) 엔진 파일명 — 비면 engine 사용
    input_size: tuple[int, int] = (896, 1600)   # 전처리 letterbox 크기 (H, W)
    conf_thresh: float = 0.1        # 검출기 자체 신뢰도 하한
    min_box_size: int = 0           # 이보다 짧은 변의 박스는 버림 (프레임 px 기준)
    onnx: str = ""                  # 재빌드용 원본 ONNX 파일명
    center_pad: bool = False        # letterbox 패딩 중앙 배치(YOLO26) / 좌상단(YOLOX)


@dataclass(frozen=True)
class ReIDSpec:
    kind: str                       # "fastreid" | "clipreid"
    engine: str
    crop: tuple[int, int]           # (W, H)
    dim: int
    onnx: str = ""
    ds_engine: str = ""             # 배치(DS) 엔진 파일명 — 비면 engine 사용


@dataclass(frozen=True)
class TrackerSpec:
    kind: str = "boosttrack"        # 현재 구현은 BoostTrack++ 하나
    det_thresh: float = 0.4         # 트래커 승격 임계값(신뢰도 부스트 이후)
    track_buffer_sec: float = 2.0   # max_age 시간 환산 (fps × 이 값)


@dataclass(frozen=True)
class Profile:
    id: str
    label: str                      # UI 표기
    detector: DetectorSpec
    reid: ReIDSpec
    tracker: TrackerSpec = field(default_factory=TrackerSpec)
    note: str = ""


PROFILES: dict[str, Profile] = {
    "yolox_fastreid": Profile(
        id="yolox_fastreid",
        label="기존 (YOLOX + FastReID)",
        detector=DetectorSpec(
            kind="yolox",
            engine="yolox_mot20_fp16.engine",
            ds_engine="yolox_mot20_fp16_dyn_b16.engine",
            input_size=(896, 1600),
            conf_thresh=0.1,
            onnx="yolox_mot20_dynamic.onnx",
        ),
        reid=ReIDSpec(kind="fastreid", engine="fastreid_sbs_s50_fp16.engine",
                      ds_engine="fastreid_sbs_s50_fp16_dyn_b256.engine",
                      crop=(128, 384), dim=2048, onnx="fastreid_sbs_s50.onnx"),
        tracker=TrackerSpec(det_thresh=0.4),
        note="현장 검증을 마친 기본 스택 (MOT20 학습 YOLOX-X + FastReID SBS-S50).",
    ),
    "yolo26_clipreid": Profile(
        id="yolo26_clipreid",
        label="신규 (YOLO26-L + CLIP-ReID)",
        detector=DetectorSpec(
            kind="yolo26",
            engine="yolo26l_v6.3_fp16_b16.engine",
            ds_engine="yolo26l_v6.3_fp16_b16.engine",
            input_size=(640, 640),
            conf_thresh=0.4,        # 참조 구현(config/tracking.yaml) 값
            min_box_size=16,        # 〃 (프레임 px — letterbox 좌표가 아니다)
            onnx="yolo26l_v6.3.onnx",
            center_pad=True,
        ),
        reid=ReIDSpec(kind="clipreid", engine="clipreid_person_fp16_b256.engine",
                      crop=(128, 256), dim=768, onnx="clipreid_person.onnx"),
        # det_thresh 0.6 — 현장영상 스윕 실측(0.4/0.5/0.6/0.7)에서 트랙 파편화가
        # 가장 적고(단명 트랙 0, 수명 median 최대) 평균 트랙 수 손실은 1% 미만.
        # 참조 구현(config/tracking.yaml)의 0.7은 자체 트래커 기준값이라 그대로
        # 옮기지 않는다 — 우리 BoostTrack++은 게이트 전에 신뢰도 부스트를 건다.
        tracker=TrackerSpec(det_thresh=0.6),
        note="PIASPACE 사람전용 파인튜닝 YOLO26-L v6.3 + CLIP-ReID ViT-B/16. "
             "검출 지연이 YOLOX 대비 크게 낮다(실측 6.2ms vs 28.9ms @720p).",
    ),
}


# ------------------------------------------------------------------ 선택
def _from_file() -> str | None:
    try:
        if STATE_FILE.is_file():
            pid = json.loads(STATE_FILE.read_text()).get("profile")
            return pid if pid in PROFILES else None
    except Exception:
        logger.exception("infer_profile.json 읽기 실패 — 기본값 사용")
    return None


def current_id() -> str:
    """지금 적용될 프로파일 id (파일 → 환경변수 → 기본값)."""
    return _from_file() or os.environ.get("INFER_PROFILE") or DEFAULT_PROFILE


def resolve(profile: str | Profile | None = None) -> Profile:
    if isinstance(profile, Profile):
        return profile
    pid = profile or current_id()
    if pid not in PROFILES:
        raise ValueError(f"알 수 없는 추론 프로파일: {pid!r} (가능: {list(PROFILES)})")
    return PROFILES[pid]


def select(profile_id: str) -> Profile:
    """프로파일을 영속 선택 — 실제 반영은 추론 계층 재기동 시점."""
    p = resolve(profile_id)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"profile": p.id}, ensure_ascii=False) + "\n")
    return p


# ------------------------------------------------------------------ 경로
def engine_path(name: str, ds: bool = False) -> str:
    return str(Path(TRT_DS_DIR if ds else TRT_DIR) / name)


def detector_engine(p: Profile, ds: bool = False) -> str:
    return engine_path((p.detector.ds_engine or p.detector.engine) if ds
                       else p.detector.engine, ds=ds)


def reid_engine(p: Profile, ds: bool = False) -> str:
    return engine_path((p.reid.ds_engine or p.reid.engine) if ds else p.reid.engine, ds=ds)


def missing_engines(p: Profile, ds: bool = False) -> list[str]:
    """없는 엔진 파일 목록 — UI가 '선택 가능' 여부를 표시하는 데 쓴다."""
    return [q for q in (detector_engine(p, ds), reid_engine(p, ds))
            if not Path(q).is_file()]


# ------------------------------------------------------------------ 팩토리
def build_detector(p: Profile, ds: bool = False):
    """단일 프레임 검출기 — 공통 인터페이스 detect_frame(bgr)->(dets, ref)."""
    path = detector_engine(p, ds)
    if p.detector.kind == "yolox":
        from src.inference_trt import TRTDetector
        return TRTDetector(path, input_size=p.detector.input_size,
                           conf_thresh=p.detector.conf_thresh)
    if p.detector.kind == "yolo26":
        from src.yolo26_trt import YOLO26TRTDetector
        return YOLO26TRTDetector(path, imgsz=p.detector.input_size[0],
                                 conf_thresh=p.detector.conf_thresh)
    if p.detector.kind == "rfdetr":
        from src.rfdetr_trt import RFDETRTRTDetector
        return RFDETRTRTDetector(path, conf_thresh=p.detector.conf_thresh)
    raise ValueError(f"미지원 검출기 종류: {p.detector.kind}")


def build_reid(p: Profile, ds: bool = False):
    """(ReID 모듈, crop (W,H)) — 모듈은 0~255 RGB 배치를 받는 계약."""
    path = reid_engine(p, ds)
    if p.reid.kind == "fastreid":
        from src.inference_trt import TRTReID
        return TRTReID(path), p.reid.crop
    if p.reid.kind == "clipreid":
        from src.clipreid_trt import CLIPReIDTRT
        m = CLIPReIDTRT(path)
        return m, m.crop_size
    raise ValueError(f"미지원 ReID 종류: {p.reid.kind}")


def apply_tracker_settings(p: Profile, *, use_reid: bool = True,
                           use_ecc: bool = False, dataset: str = "mot20") -> None:
    """프로세스 전역 트래커 설정 반영 (BoostTrack은 전역 설정을 읽는다)."""
    from default_settings import GeneralSettings
    GeneralSettings.values["dataset"] = dataset
    GeneralSettings.values["test_dataset"] = True
    GeneralSettings.values["use_embedding"] = use_reid
    GeneralSettings.values["use_ecc"] = use_ecc
    GeneralSettings.values["det_thresh"] = p.tracker.det_thresh


def describe(ds: bool = False) -> list[dict]:
    """UI용 프로파일 목록."""
    cur = current_id()
    out = []
    for p in PROFILES.values():
        miss = missing_engines(p, ds)
        # 표기 엔진은 실제로 로드될 파일 — 호스트/DS가 다른 파일을 쓴다.
        det_f = Path(detector_engine(p, ds)).name
        reid_f = Path(reid_engine(p, ds)).name
        out.append({
            "id": p.id, "label": p.label, "note": p.note,
            "detector": f"{p.detector.kind} ({det_f})",
            "reid": f"{p.reid.kind} · {p.reid.dim}d ({reid_f})",
            "tracker": f"{p.tracker.kind} · det_thresh {p.tracker.det_thresh}",
            "selected": p.id == cur,
            "ready": not miss,
            "missing": miss,
        })
    return out
