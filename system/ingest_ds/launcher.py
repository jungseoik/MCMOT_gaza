"""멀티 GPU DeepStream 워커 런처 — GPU당 컨테이너 1개 + 호스트 중앙 수신.

GPU 1장/2장/N장 환경에서 같은 코드·설정으로 동작하는 것이 목표다:
  GPU_DEVICES="0,1"  (환경변수 또는 인자)
  → 카메라를 부하 균등(채널의 analyze_fps 합 기준 greedy)으로 GPU별 분할
  → GPU별 docker 워커 컨테이너 기동 (--gpus device=K, --network host,
     ZMQ 포트 = 5701 + K)
  → TrackBridge 1개가 모든 워커 엔드포인트를 PULL로 통합 수신.

`DsIngestManager`는 기존 IngestManager(system/ingest/manager.py) +
AnalyzerThread(system/tracking/analyzer.py) 조합과 동일한 외부 인터페이스
(start(cams)/stop()/states()/add·remove·update_camera/on_tracks 콜백)를
제공한다 — 이후 server.py에서 INGEST_BACKEND=deepstream 스위치로 갈아끼우는
것을 전제로 한다 (server.py 수정은 이 모듈 범위 아님).

카메라 hot add/remove(최소 구현): 해당 카메라가 배정된 GPU 워커만
cams JSON 갱신 후 컨테이너 재시작 — 다른 GPU 워커는 무영향.
(DS 파이프라인 동적 소스 add/remove는 다음 단계.)

단독 실행(검증용 — Ctrl-C 종료):
  GPU_DEVICES=0,1 conda run -n boosttrack python -m system.ingest_ds.launcher \
      --cams system/ingest_ds/configs/cams_12ch.json [--duration 60]
컨테이너 정리만:
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
from pathlib import Path

from system.contracts import CameraState
from system.ingest_ds.bridge import OnTracks, TrackBridge

logger = logging.getLogger("ingest_ds.launcher")

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = Path(__file__).resolve().parent / "configs" / "runtime"

ZMQ_PORT_BASE = 5701          # 워커 포트 = ZMQ_PORT_BASE + gpu_id
DEFAULT_IMAGE = "macs-deepstream:9.0"
CONTAINER_PREFIX = "macs-ds-worker"
ENGINE_MAX_BATCH = 16         # YOLOX dynamic 엔진 max (README '엔진 빌드' 참조)
STALL_SEC = 10.0              # 이 시간 이상 트랙 미수신이면 reconnecting 판정
FPS_WINDOW_SEC = 10.0         # fps_in 계산 슬라이딩 윈도


# ------------------------------------------------------------ 부하 균등 분할


def partition_cams(cams: list[dict], gpus: list[int]) -> dict[int, list[dict]]:
    """카메라를 GPU별로 부하 균등 분할 (부하 = 채널의 analyze_fps 합, greedy).

    analyze_fps 내림차순으로 정렬 후 매번 누적 부하가 가장 작은 GPU에 배정.
    입력 순서가 같으면 결과도 같다(결정적) — 재시작 시 배정이 흔들리지 않는다.
    """
    assign: dict[int, list[dict]] = {g: [] for g in gpus}
    load: dict[int, float] = {g: 0.0 for g in gpus}
    order = sorted(cams, key=lambda c: (-float(c.get("analyze_fps", 5.0)),
                                        str(c.get("cam_id", ""))))
    for cam in order:
        g = min(gpus, key=lambda k: (load[k], gpus.index(k)))
        assign[g].append(cam)
        load[g] += float(cam.get("analyze_fps", 5.0))
    # 워커 파이프라인의 pad 순서 안정화를 위해 cam_id 순 정렬
    for g in gpus:
        assign[g].sort(key=lambda c: str(c.get("cam_id", "")))
    return assign


def parse_gpu_devices(spec: str | None = None) -> list[int]:
    """GPU_DEVICES 환경변수(예 "0,1") 파싱 — 미지정 시 기본 [1](GPU0은 타 프로젝트)."""
    raw = spec if spec is not None else os.environ.get("GPU_DEVICES", "1")
    gpus = [int(g) for g in raw.split(",") if g.strip() != ""]
    if not gpus:
        raise ValueError(f"GPU_DEVICES가 비어 있음: {raw!r}")
    return gpus


# ------------------------------------------------------------ 컨테이너 1개


class WorkerContainer:
    """GPU 1장에 대응하는 DS 워커 컨테이너의 수명주기 관리 (docker CLI 래퍼)."""

    def __init__(self, gpu: int, *, image: str = DEFAULT_IMAGE,
                 prefix: str = CONTAINER_PREFIX,
                 repo_root: Path = REPO_ROOT,
                 runtime_dir: Path = RUNTIME_DIR) -> None:
        self.gpu = gpu
        self.image = image
        self.name = f"{prefix}-gpu{gpu}"
        self.port = ZMQ_PORT_BASE + gpu
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
        """cams JSON을 쓰고 컨테이너를 (재)기동. 다른 GPU 워커는 건드리지 않는다."""
        self.stop()
        self.cams = list(cams)
        if not cams:
            logger.info("[%s] 배정 카메라 없음 — 컨테이너 미기동", self.name)
            return
        self.cams_path.parent.mkdir(parents=True, exist_ok=True)
        self.cams_path.write_text(
            json.dumps(cams, indent=2, ensure_ascii=False) + "\n")
        if batch_size is None:
            batch_size = min(ENGINE_MAX_BATCH, max(1, len(cams)))
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
        logger.info("[%s] 기동 — GPU%d, %d채널, batch<=%d, %s",
                    self.name, self.gpu, len(cams), batch_size, self.endpoint)

    def stop(self) -> None:
        if self.running():
            self._docker("stop", "-t", "5", self.name, check=False)
            logger.info("[%s] 중지", self.name)
        else:  # --rm 이전에 죽은 잔여물 정리
            self._docker("rm", "-f", self.name, check=False)


# ------------------------------------------------------------ 통합 매니저


class DsIngestManager:
    """IngestManager+AnalyzerThread 대체 — DS 워커 무리 + 브리지 통합 수신.

    외부 인터페이스(호출 시그니처)는 기존 조합과 동일:
      start(cameras) / stop() / states() / add·remove·update_camera /
      set_enabled / get_snapshot — on_tracks(cam_id, ts, tracks) 콜백 수신.
    cameras는 CameraConfig(pydantic) 또는 {cam_id, rtsp, analyze_fps, enabled}
    dict 어느 쪽이든 받는다.
    """

    def __init__(self, on_tracks: OnTracks, *,
                 gpu_devices: list[int] | None = None,
                 image: str = DEFAULT_IMAGE,
                 batch_size: int | None = None,
                 worker_args: list[str] | None = None) -> None:
        self.gpus = gpu_devices or parse_gpu_devices()
        self._on_tracks = on_tracks
        self._batch_size = batch_size
        self._worker_args = list(worker_args or [])
        self.workers: dict[int, WorkerContainer] = {
            g: WorkerContainer(g, image=image) for g in self.gpus}
        self.bridge: TrackBridge | None = None

        self._lock = threading.Lock()
        self._cfgs: dict[str, dict] = {}          # cam_id → 정규화된 설정 dict
        self._cam_gpu: dict[str, int] = {}        # cam_id → 배정 GPU
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
            assign = partition_cams(enabled, self.gpus)
            self._cam_gpu = {c["cam_id"]: g
                             for g, cams in assign.items() for c in cams}
            self._started = True
        for g, cams in assign.items():
            self.workers[g].start(cams, batch_size=self._batch_size,
                                  extra_args=self._worker_args)
        self.bridge = TrackBridge(
            self._on_tracks_wrapped,
            connect=[w.endpoint for w in self.workers.values()])
        self.bridge.start()
        logger.info("DsIngestManager 기동 — GPU=%s, 분할=%s", self.gpus,
                    {g: [c["cam_id"] for c in cams] for g, cams in assign.items()})

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

    # -- 카메라 CRUD (hot add/remove — 해당 GPU 워커만 재시작) --------------
    def _restart_gpu(self, gpu: int) -> None:
        cams = [self._worker_cam(self._cfgs[cid])
                for cid, g in sorted(self._cam_gpu.items()) if g == gpu]
        self.workers[gpu].start(cams, batch_size=self._batch_size,
                                extra_args=self._worker_args)

    def add_camera(self, cfg) -> None:
        d = self._norm(cfg)
        with self._lock:
            if d["cam_id"] in self._cam_gpu:
                raise ValueError(f"이미 실행 중인 카메라: {d['cam_id']}")
            self._cfgs[d["cam_id"]] = d
            if not d["enabled"]:
                return
            # 현재 배정 기준 누적 부하가 가장 작은 GPU에 추가
            load = {g: 0.0 for g in self.gpus}
            for cid, g in self._cam_gpu.items():
                load[g] += self._cfgs[cid]["analyze_fps"]
            gpu = min(self.gpus, key=lambda k: (load[k], self.gpus.index(k)))
            self._cam_gpu[d["cam_id"]] = gpu
        self._restart_gpu(gpu)

    def remove_camera(self, cam_id: str) -> None:
        with self._lock:
            self._cfgs.pop(cam_id, None)
            gpu = self._cam_gpu.pop(cam_id, None)
            self._recv_ts.pop(cam_id, None)
            self._last_frame_ts.pop(cam_id, None)
        if gpu is not None:
            self._restart_gpu(gpu)

    def update_camera(self, cfg) -> None:
        """rtsp/analyze_fps/enabled 변경 반영 — 배정 GPU 유지, 그 워커만 재시작."""
        d = self._norm(cfg)
        with self._lock:
            self._cfgs[d["cam_id"]] = d
            gpu = self._cam_gpu.get(d["cam_id"])
            if gpu is None and d["enabled"]:
                pass                       # 아래 add 경로로
            elif gpu is not None and not d["enabled"]:
                self._cam_gpu.pop(d["cam_id"])
        if gpu is None and d["enabled"]:
            with self._lock:
                self._cfgs.pop(d["cam_id"])   # add_camera가 다시 넣는다
            self.add_camera(d)
            return
        if gpu is not None:
            self._restart_gpu(gpu)

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
            cam_gpu = dict(self._cam_gpu)
            recv = {c: list(dq) for c, dq in self._recv_ts.items()}
            last_ts = dict(self._last_frame_ts)
        running_gpu = {g: w.running() for g, w in self.workers.items()}
        for cam_id in sorted(cfgs):
            d = cfgs[cam_id]
            gpu = cam_gpu.get(cam_id)
            if not d["enabled"] or gpu is None:
                out.append(CameraState(cam_id=cam_id, status="disabled"))
                continue
            ticks = [t for t in recv.get(cam_id, []) if now - t <= FPS_WINDOW_SEC]
            fps_in = len(ticks) / FPS_WINDOW_SEC
            fresh = bool(ticks) and (now - ticks[-1]) < STALL_SEC
            if fresh:
                status = "running"
            elif running_gpu.get(gpu):
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
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--duration", type=float, default=0.0, help="N초 후 종료 (0=무한)")
    ap.add_argument("--stop", action="store_true", help="컨테이너 정리만 하고 종료")
    ap.add_argument("--worker-args", default="",
                    help="워커에 그대로 전달할 추가 인자 (공백 구분)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    gpus = parse_gpu_devices(args.gpus)

    if args.stop:
        for g in gpus:
            WorkerContainer(g).stop()
        return
    if not args.cams:
        ap.error("--cams 필요 (--stop 제외)")

    with open(args.cams) as f:
        cams = json.load(f)

    latest: dict[str, int] = {}

    def on_tracks(cam_id: str, ts: float, tracks) -> None:
        latest[cam_id] = len(tracks)

    mgr = DsIngestManager(on_tracks, gpu_devices=gpus,
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
