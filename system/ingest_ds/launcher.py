"""멀티 GPU DeepStream 워커 런처 — GPU당 워커 컨테이너 N개 + 호스트 중앙 수신.

GPU 1장/2장/N장 환경에서 같은 코드·설정으로 동작하는 것이 목표다:
  GPU_DEVICES="0,1"  (환경변수 또는 인자)
  WORKERS_PER_GPU=2  (기본 1 권장 — 기존 단일 워커 동작 그대로)
  → 카메라를 부하 균등(채널의 analyze_fps 합 기준 greedy)으로
    (GPU, 워커) 슬롯 전체에 분할
  → 슬롯별 docker 워커 컨테이너 기동 (--gpus device=K, --network host)
  → TrackBridge 1개가 모든 워커 엔드포인트를 PULL로 통합 수신.

워커 분할의 효과(실측 — docs/reports/DeepStream-한계처리량-실측.md §7):
5fps 유지 최대 채널 수는 분할과 무관하게 16ch로 동일하다(상한은 GPU 커널
시간). 분할의 총량 이득은 5fps가 깨진 과부하 영역에서만 +25~36%이고 워커당
엔진 메모리 ~5-7GB가 추가된다 — 기본 1을 권장하며, "저하를 허용하고 많은
채널의 총 처리량"이 목표일 때만 2~3분할을 쓴다.

컨테이너명·ZMQ 포트 체계 (하위호환):
  WORKERS_PER_GPU=1 → 이름 macs-ds-worker-gpuK, 포트 5701+K   (기존과 동일)
  WORKERS_PER_GPU≥2 → 이름 macs-ds-worker-gpuK-wj, 포트 5701 + K + 100*j
  j=0 워커의 포트는 항상 기존 단일 워커 포트(5701+K)와 같다 — 기존 문서의
  `bridge --connect tcp://127.0.0.1:570(1+K)` 예시가 그대로 유효하다.

`DsIngestManager`는 기존 IngestManager(system/ingest/manager.py) +
AnalyzerThread(system/tracking/analyzer.py) 조합과 동일한 외부 인터페이스
(start(cams)/stop()/states()/add·remove·update_camera/on_tracks 콜백)를
제공한다 — server.py의 INGEST_BACKEND=deepstream 스위치가 이 클래스를 쓴다.

카메라 hot add/remove(최소 구현): 해당 카메라가 배정된 슬롯의 워커만
cams JSON 갱신 후 컨테이너 재시작 — 다른 슬롯 워커는 무영향.
(DS 파이프라인 동적 소스 add/remove는 다음 단계.)

단독 실행(검증용 — Ctrl-C 종료):
  GPU_DEVICES=1 WORKERS_PER_GPU=2 conda run -n boosttrack python -m \
      system.ingest_ds.launcher --cams system/ingest_ds/configs/cams_12ch.json
컨테이너 정리만 (워커 수 무관 이름 프리픽스로 전부 정리):
  conda run -n boosttrack python -m system.ingest_ds.launcher --stop
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Hashable, Sequence
from pathlib import Path

from system.contracts import CameraState
from system.ingest_ds.bridge import OnTracks, TrackBridge

logger = logging.getLogger("ingest_ds.launcher")

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = Path(__file__).resolve().parent / "configs" / "runtime"

ZMQ_PORT_BASE = 5701          # 워커 포트 = ZMQ_PORT_BASE + gpu + 100*worker
WORKER_PORT_STRIDE = 100      # 워커 인덱스당 포트 간격 (gpu id < 100 전제)
DEFAULT_IMAGE = "macs-deepstream:9.0"
CONTAINER_PREFIX = "macs-ds-worker"
ENGINE_MAX_BATCH = 16         # 기본 b16 엔진의 max — DS_ENGINE_MAX_BATCH로 재정의
                              # (워커도 엔진 프로파일에서 읽어 자체 클램프하므로
                              #  이 값은 "기본 배치 산정"에만 쓰인다. README '엔진 빌드' 참조)
STALL_SEC = 10.0              # 이 시간 이상 트랙 미수신이면 reconnecting 판정
FPS_WINDOW_SEC = 10.0         # fps_in 계산 슬라이딩 윈도

# 슬롯 = (gpu_id, worker_idx). WORKERS_PER_GPU=1이면 (K, 0) 하나뿐이라
# 기존 "GPU당 1워커" 동작과 완전히 같다.
Slot = tuple[int, int]


# ------------------------------------------------------------ 부하 균등 분할


def partition_cams(cams: list[dict], gpus: Sequence[Hashable]) -> dict:
    """카메라를 슬롯별로 부하 균등 분할 (부하 = 채널의 analyze_fps 합, greedy).

    `gpus`는 GPU id 목록(기존 호출)이든 (gpu, worker) 슬롯 목록이든 아무
    해시 가능 키 시퀀스나 받는다 — 분할 로직은 키에 무관하다.
    analyze_fps 내림차순으로 정렬 후 매번 누적 부하가 가장 작은 키에 배정.
    입력 순서가 같으면 결과도 같다(결정적) — 재시작 시 배정이 흔들리지 않는다.
    """
    keys = list(gpus)
    assign: dict = {k: [] for k in keys}
    load: dict = {k: 0.0 for k in keys}
    order = sorted(cams, key=lambda c: (-float(c.get("analyze_fps", 5.0)),
                                        str(c.get("cam_id", ""))))
    for cam in order:
        k = min(keys, key=lambda x: (load[x], keys.index(x)))
        assign[k].append(cam)
        load[k] += float(cam.get("analyze_fps", 5.0))
    # 워커 파이프라인의 pad 순서 안정화를 위해 cam_id 순 정렬
    for k in keys:
        assign[k].sort(key=lambda c: str(c.get("cam_id", "")))
    return assign


def parse_gpu_devices(spec: str | None = None) -> list[int]:
    """GPU_DEVICES 환경변수(예 "0,1") 파싱 — 미지정 시 기본 [1](GPU0은 타 프로젝트)."""
    raw = spec if spec is not None else os.environ.get("GPU_DEVICES", "1")
    gpus = [int(g) for g in raw.split(",") if g.strip() != ""]
    if not gpus:
        raise ValueError(f"GPU_DEVICES가 비어 있음: {raw!r}")
    return gpus


def parse_workers_per_gpu(spec: str | int | None = None) -> int:
    """WORKERS_PER_GPU 환경변수 파싱 — 기본 1(권장). ≥2는 총량 우선 모드."""
    raw = spec if spec is not None else os.environ.get("WORKERS_PER_GPU", "1")
    n = int(raw)
    if n < 1:
        raise ValueError(f"WORKERS_PER_GPU는 1 이상이어야 함: {raw!r}")
    return n


def parse_engine_max_batch(spec: str | int | None = None) -> int:
    """DS_ENGINE_MAX_BATCH 환경변수 파싱 — 기본 16(b16 엔진).

    b32 엔진(`yolox_mot20_fp16_dyn_b32.engine`)을 쓸 때 32로 올리면
    슬롯 기본 배치 산정(min(max_batch, 채널 수))의 상한이 따라 커진다.
    실제 안전장치는 워커가 엔진 프로파일에서 읽는 자체 클램프 —
    이 값이 엔진 max보다 커도 워커가 줄여서 동작한다.
    """
    raw = spec if spec is not None else os.environ.get(
        "DS_ENGINE_MAX_BATCH", str(ENGINE_MAX_BATCH))
    n = int(raw)
    if n < 1:
        raise ValueError(f"DS_ENGINE_MAX_BATCH는 1 이상이어야 함: {raw!r}")
    return n


def worker_port(gpu: int, worker: int = 0) -> int:
    """슬롯의 ZMQ 포트 — worker=0이면 기존 단일 워커 포트(5701+K)와 동일."""
    return ZMQ_PORT_BASE + gpu + WORKER_PORT_STRIDE * worker


# ------------------------------------------------------------ 컨테이너 1개


class WorkerContainer:
    """(GPU, 워커) 슬롯 1개에 대응하는 DS 워커 컨테이너 수명주기 (docker CLI 래퍼).

    n_workers=1(기본)이면 이름·포트가 기존 단일 워커와 완전히 같다:
      이름 {prefix}-gpu{K}, 포트 5701+K.
    n_workers≥2면 이름에 -w{j} 접미사, 포트는 5701 + K + 100*j.
    """

    def __init__(self, gpu: int, *, worker: int = 0, n_workers: int = 1,
                 image: str = DEFAULT_IMAGE,
                 prefix: str = CONTAINER_PREFIX,
                 repo_root: Path = REPO_ROOT,
                 runtime_dir: Path = RUNTIME_DIR,
                 max_batch: int | None = None) -> None:
        if not (0 <= worker < n_workers):
            raise ValueError(f"worker 인덱스 범위 밖: {worker} / n_workers={n_workers}")
        self.gpu = gpu
        self.worker = worker
        self.image = image
        self.max_batch = (max_batch if max_batch is not None
                          else parse_engine_max_batch())
        self.name = (f"{prefix}-gpu{gpu}" if n_workers == 1
                     else f"{prefix}-gpu{gpu}-w{worker}")
        self.port = worker_port(gpu, worker)
        self.endpoint = f"tcp://127.0.0.1:{self.port}"
        self.repo_root = repo_root
        self.cams_path = runtime_dir / f"cams_{self.name}.json"
        self.cams: list[dict] = []

    # -- docker 헬퍼 -------------------------------------------------------
    @staticmethod
    def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", *args], capture_output=True,
                              text=True, check=check)

    def running(self) -> bool:
        r = self._docker("inspect", "-f", "{{.State.Running}}", self.name,
                         check=False)
        return r.returncode == 0 and r.stdout.strip() == "true"

    def logs(self, tail: int = 50) -> str:
        r = self._docker("logs", "--tail", str(tail), self.name, check=False)
        return (r.stdout or "") + (r.stderr or "")

    # -- 수명주기 ----------------------------------------------------------
    def start(self, cams: list[dict], *, batch_size: int | None = None,
              extra_args: list[str] | None = None) -> None:
        """cams JSON을 쓰고 컨테이너를 (재)기동. 다른 슬롯 워커는 건드리지 않는다."""
        self.stop()
        self.cams = list(cams)
        if not cams:
            logger.info("[%s] 배정 카메라 없음 — 컨테이너 미기동", self.name)
            return
        self.cams_path.parent.mkdir(parents=True, exist_ok=True)
        self.cams_path.write_text(
            json.dumps(cams, indent=2, ensure_ascii=False) + "\n")
        if batch_size is None:
            # 슬롯 담당 채널 수 기준 (분할 시 워커별 배치가 자연히 작아진다).
            # 상한은 하드코딩 대신 max_batch(기본 16, DS_ENGINE_MAX_BATCH로 재정의) —
            # 워커가 엔진 프로파일로 재클램프하므로 과대 지정해도 안전하다.
            batch_size = min(self.max_batch, max(1, len(cams)))
        rel_cams = self.cams_path.relative_to(self.repo_root)
        cmd = [
            "docker", "run", "-d", "--rm", "--name", self.name,
            "--network", "host", "--gpus", f"device={self.gpu}",
            "-v", f"{self.repo_root}:/workspace", "-w", "/workspace",
            "-e", "PYTHONUNBUFFERED=1",
            self.image, "python3", "-m", "system.ingest_ds.worker",
            "--cams", str(rel_cams),
            "--zmq-bind", f"tcp://*:{self.port}",
            "--batch-size", str(batch_size),
            *(extra_args or []),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"[{self.name}] docker run 실패: {r.stderr.strip()}")
        logger.info("[%s] 기동 — GPU%d/w%d, %d채널, batch<=%d, %s",
                    self.name, self.gpu, self.worker, len(cams), batch_size,
                    self.endpoint)

    def stop(self) -> None:
        if self.running():
            self._docker("stop", "-t", "5", self.name, check=False)
            logger.info("[%s] 중지", self.name)
        else:  # --rm 이전에 죽은 잔여물 정리
            self._docker("rm", "-f", self.name, check=False)


def stop_all_workers(prefix: str = CONTAINER_PREFIX) -> list[str]:
    """이름이 prefix로 시작하는 워커 컨테이너 전부 정리 — 워커 수 설정 무관."""
    r = subprocess.run(["docker", "ps", "-a", "--filter", f"name=^{prefix}",
                        "--format", "{{.Names}}"],
                       capture_output=True, text=True, check=False)
    names = [n for n in (r.stdout or "").split() if n]
    for n in names:
        subprocess.run(["docker", "rm", "-f", n], capture_output=True,
                       text=True, check=False)
        logger.info("[%s] 강제 정리", n)
    return names


# ------------------------------------------------------------ 통합 매니저


class DsIngestManager:
    """IngestManager+AnalyzerThread 대체 — DS 워커 무리 + 브리지 통합 수신.

    외부 인터페이스(호출 시그니처)는 기존 조합과 동일:
      start(cameras) / stop() / states() / add·remove·update_camera /
      set_enabled / get_snapshot — on_tracks(cam_id, ts, tracks) 콜백 수신.
    cameras는 CameraConfig(pydantic) 또는 {cam_id, rtsp, analyze_fps, enabled}
    dict 어느 쪽이든 받는다.

    workers_per_gpu(기본 1 — 환경변수 WORKERS_PER_GPU)로 GPU당 워커 수를
    늘릴 수 있다. 카메라 분할·hot add/remove·상태 판정은 전부 (GPU, 워커)
    슬롯 단위로 동작하며, 1이면 기존 "GPU당 1워커"와 완전히 같다.
    """

    def __init__(self, on_tracks: OnTracks, *,
                 gpu_devices: list[int] | None = None,
                 workers_per_gpu: int | None = None,
                 image: str = DEFAULT_IMAGE,
                 batch_size: int | None = None,
                 max_batch: int | None = None,
                 worker_args: list[str] | None = None) -> None:
        self.gpus = gpu_devices or parse_gpu_devices()
        self.workers_per_gpu = (workers_per_gpu if workers_per_gpu is not None
                                else parse_workers_per_gpu())
        self.slots: list[Slot] = [(g, j) for g in self.gpus
                                  for j in range(self.workers_per_gpu)]
        self._on_tracks = on_tracks
        self._batch_size = batch_size
        self._max_batch = (max_batch if max_batch is not None
                           else parse_engine_max_batch())
        self._worker_args = list(worker_args or [])
        # DS_DET_ENGINE: server.py 수정 없이 검출 엔진 교체 (예: b32 엔진).
        # 명시 worker_args에 --det-engine이 이미 있으면 그것이 우선한다.
        det_engine = os.environ.get("DS_DET_ENGINE")
        if det_engine and "--det-engine" not in self._worker_args:
            self._worker_args += ["--det-engine", det_engine]
        self.workers: dict[Slot, WorkerContainer] = {
            (g, j): WorkerContainer(g, worker=j,
                                    n_workers=self.workers_per_gpu, image=image,
                                    max_batch=self._max_batch)
            for (g, j) in self.slots}
        self.bridge: TrackBridge | None = None

        self._lock = threading.Lock()
        self._cfgs: dict[str, dict] = {}          # cam_id → 정규화된 설정 dict
        self._cam_slot: dict[str, Slot] = {}      # cam_id → 배정 (GPU, 워커)
        self._recv_ts: dict[str, deque[float]] = {}   # fps_in 슬라이딩 윈도
        self._last_frame_ts: dict[str, float] = {}
        self._started = False

    # -- 설정 정규화 -------------------------------------------------------
    @staticmethod
    def _norm(cfg) -> dict:
        """CameraConfig(pydantic) 또는 dict → 워커 cams JSON 항목 + enabled."""
        if hasattr(cfg, "model_dump"):
            d = cfg.model_dump()
        else:
            d = dict(cfg)
        return {
            "cam_id": d["cam_id"],
            "rtsp": d["rtsp"],
            "analyze_fps": float(d.get("analyze_fps", 5.0)),
            "enabled": bool(d.get("enabled", True)),
        }

    @staticmethod
    def _worker_cam(d: dict) -> dict:
        return {"cam_id": d["cam_id"], "rtsp": d["rtsp"],
                "analyze_fps": d["analyze_fps"]}

    @staticmethod
    def _slot_name(slot: Slot) -> str:
        return f"gpu{slot[0]}-w{slot[1]}"

    # -- 수신 계측 래퍼 ----------------------------------------------------
    def _on_tracks_wrapped(self, cam_id: str, ts: float, tracks) -> None:
        now = time.monotonic()
        with self._lock:
            dq = self._recv_ts.setdefault(cam_id, deque(maxlen=512))
            dq.append(now)
            self._last_frame_ts[cam_id] = ts
        self._on_tracks(cam_id, ts, tracks)

    # -- 기동/중지 ---------------------------------------------------------
    def start(self, cameras: list) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("이미 start됨 — stop() 후 재호출")
            self._cfgs = {}
            for cfg in cameras:
                d = self._norm(cfg)
                self._cfgs[d["cam_id"]] = d
            enabled = [self._worker_cam(d) for d in self._cfgs.values()
                       if d["enabled"]]
            assign = partition_cams(enabled, self.slots)
            self._cam_slot = {c["cam_id"]: s
                              for s, cams in assign.items() for c in cams}
            self._started = True
        for s, cams in assign.items():
            self.workers[s].start(cams, batch_size=self._batch_size,
                                  extra_args=self._worker_args)
        self.bridge = TrackBridge(
            self._on_tracks_wrapped,
            connect=[w.endpoint for w in self.workers.values()])
        self.bridge.start()
        logger.info("DsIngestManager 기동 — GPU=%s ×%d워커, 분할=%s",
                    self.gpus, self.workers_per_gpu,
                    {self._slot_name(s): [c["cam_id"] for c in cams]
                     for s, cams in assign.items()})

    def stop(self) -> None:
        for w in self.workers.values():
            w.stop()
        if self.bridge is not None:
            self.bridge.stop()
            self.bridge.join(timeout=2.0)
            self.bridge = None
        with self._lock:
            self._started = False
        logger.info("DsIngestManager 중지 (%d workers)", len(self.workers))

    # -- 카메라 CRUD (hot add/remove — 해당 슬롯 워커만 재시작) --------------
    def _restart_slot(self, slot: Slot) -> None:
        cams = [self._worker_cam(self._cfgs[cid])
                for cid, s in sorted(self._cam_slot.items()) if s == slot]
        self.workers[slot].start(cams, batch_size=self._batch_size,
                                 extra_args=self._worker_args)

    def add_camera(self, cfg) -> None:
        d = self._norm(cfg)
        with self._lock:
            if d["cam_id"] in self._cam_slot:
                raise ValueError(f"이미 실행 중인 카메라: {d['cam_id']}")
            self._cfgs[d["cam_id"]] = d
            if not d["enabled"]:
                return
            # 현재 배정 기준 누적 부하가 가장 작은 슬롯에 추가
            load: dict[Slot, float] = {s: 0.0 for s in self.slots}
            for cid, s in self._cam_slot.items():
                load[s] += self._cfgs[cid]["analyze_fps"]
            slot = min(self.slots,
                       key=lambda s: (load[s], self.slots.index(s)))
            self._cam_slot[d["cam_id"]] = slot
        self._restart_slot(slot)

    def remove_camera(self, cam_id: str) -> None:
        with self._lock:
            self._cfgs.pop(cam_id, None)
            slot = self._cam_slot.pop(cam_id, None)
            self._recv_ts.pop(cam_id, None)
            self._last_frame_ts.pop(cam_id, None)
        if slot is not None:
            self._restart_slot(slot)

    def update_camera(self, cfg) -> None:
        """rtsp/analyze_fps/enabled 변경 반영 — 배정 슬롯 유지, 그 워커만 재시작."""
        d = self._norm(cfg)
        with self._lock:
            self._cfgs[d["cam_id"]] = d
            slot = self._cam_slot.get(d["cam_id"])
            if slot is None and d["enabled"]:
                pass                       # 아래 add 경로로
            elif slot is not None and not d["enabled"]:
                self._cam_slot.pop(d["cam_id"])
        if slot is None and d["enabled"]:
            with self._lock:
                self._cfgs.pop(d["cam_id"])   # add_camera가 다시 넣는다
            self.add_camera(d)
            return
        if slot is not None:
            self._restart_slot(slot)

    def set_enabled(self, cam_id: str, enabled: bool) -> None:
        with self._lock:
            d = self._cfgs.get(cam_id)
        if d is None:
            raise KeyError(f"미등록 카메라: {cam_id}")
        if d["enabled"] == enabled:
            return
        self.update_camera({**d, "enabled": enabled})

    # -- 상태 ---------------------------------------------------------------
    def states(self) -> list[CameraState]:
        now = time.monotonic()
        out: list[CameraState] = []
        with self._lock:
            cfgs = dict(self._cfgs)
            cam_slot = dict(self._cam_slot)
            recv = {c: list(dq) for c, dq in self._recv_ts.items()}
            last_ts = dict(self._last_frame_ts)
        running_slot = {s: w.running() for s, w in self.workers.items()}
        for cam_id in sorted(cfgs):
            d = cfgs[cam_id]
            slot = cam_slot.get(cam_id)
            if not d["enabled"] or slot is None:
                out.append(CameraState(cam_id=cam_id, status="disabled"))
                continue
            ticks = [t for t in recv.get(cam_id, []) if now - t <= FPS_WINDOW_SEC]
            fps_in = len(ticks) / FPS_WINDOW_SEC
            fresh = bool(ticks) and (now - ticks[-1]) < STALL_SEC
            if fresh:
                status = "running"
            elif running_slot.get(slot):
                status = "reconnecting"    # 컨테이너는 살아있으나 트랙 미수신
            else:
                status = "disconnected"
            out.append(CameraState(cam_id=cam_id, status=status, fps_in=fps_in,
                                   last_frame_ts=last_ts.get(cam_id)))
        return out

    def state(self, cam_id: str) -> CameraState | None:
        for st in self.states():
            if st.cam_id == cam_id:
                return st
        return None

    def get_snapshot(self, cam_id: str):
        """프레임 픽셀은 컨테이너 밖으로 나오지 않는다 — 스냅샷 미지원(None).

        셋업 UI의 스냅샷이 필요하면 기존 ffmpeg 단발 캡처를 별도로 쓰는 것을
        제안한다 (계약 변경 아님 — IngestManager도 None 반환이 허용 시그니처).
        """
        return None


# ------------------------------------------------------------------ 단독 실행


def _main() -> None:
    ap = argparse.ArgumentParser(description="멀티 GPU DS 워커 런처 (검증용)")
    ap.add_argument("--cams", help="카메라 JSON — [{cam_id, rtsp, analyze_fps}, ...]")
    ap.add_argument("--gpus", default=None,
                    help="GPU 목록 (예 '0,1') — 미지정 시 GPU_DEVICES 환경변수")
    ap.add_argument("--workers-per-gpu", type=int, default=None,
                    help="GPU당 워커 수 — 미지정 시 WORKERS_PER_GPU 환경변수(기본 1)")
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--duration", type=float, default=0.0, help="N초 후 종료 (0=무한)")
    ap.add_argument("--stop", action="store_true",
                    help="이름 프리픽스 기준 워커 컨테이너 전부 정리 후 종료")
    ap.add_argument("--worker-args", default="",
                    help="워커에 그대로 전달할 추가 인자 (공백 구분)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.stop:
        stop_all_workers()
        return
    if not args.cams:
        ap.error("--cams 필요 (--stop 제외)")

    gpus = parse_gpu_devices(args.gpus)
    with open(args.cams) as f:
        cams = json.load(f)

    latest: dict[str, int] = {}

    def on_tracks(cam_id: str, ts: float, tracks) -> None:
        latest[cam_id] = len(tracks)

    mgr = DsIngestManager(on_tracks, gpu_devices=gpus,
                          workers_per_gpu=parse_workers_per_gpu(
                              args.workers_per_gpu),
                          batch_size=args.batch_size,
                          worker_args=args.worker_args.split() or None)
    mgr.start(cams)
    t_end = time.time() + args.duration if args.duration > 0 else None
    try:
        while t_end is None or time.time() < t_end:
            time.sleep(5)
            sts = mgr.states()
            logger.info("STATES %s | 트랙수(최근)=%s",
                        {s.cam_id: f"{s.status}@{s.fps_in:.1f}fps" for s in sts},
                        dict(sorted(latest.items())))
    except KeyboardInterrupt:
        pass
    finally:
        mgr.stop()


if __name__ == "__main__":
    _main()
