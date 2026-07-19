"""DeepStream 워커 → 호스트 브리지: ZMQ PULL 수신 → on_tracks 콜백 어댑터.

컨테이너 워커(system/ingest_ds/worker.py)가 PUSH하는 트랙 메시지를 받아
system.contracts.TrackedObject 목록으로 복원해 on_tracks(cam_id, ts, tracks)
콜백으로 넘긴다 — system/tracking/analyzer.py 의 OnTracks 와 동일 시그니처라
INGEST_BACKEND 스위치 시 spatial/metrics 쪽을 그대로 물릴 수 있다.

메시지 형식(json 또는 msgpack — 첫 바이트로 자동 판별):
  {"cam_id": str, "ts": float,
   "tracks": [{"local_track_id", "foot_uv", "bbox_xyxy", "conf"}, ...]}

단독 실행(수신 통계 출력):
  conda run -n boosttrack python -m system.ingest_ds.bridge \
      --connect tcp://127.0.0.1:5701
"""
from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from collections.abc import Callable

import zmq

from system.contracts import TrackedObject

logger = logging.getLogger("ingest_ds.bridge")

OnTracks = Callable[[str, float, list[TrackedObject]], None]

try:
    import msgpack
except ImportError:                    # msgpack은 선택 — 기본 codec은 json
    msgpack = None


def _decode(raw: bytes) -> dict:
    """워커 메시지 디코드 — json(기본)/msgpack 자동 판별."""
    if raw[:1] == b"{":
        return json.loads(raw)
    if msgpack is None:
        raise RuntimeError("msgpack 메시지 수신 — `pip install msgpack` 필요")
    return msgpack.unpackb(raw, raw=False)


class TrackBridge(threading.Thread):
    """ZMQ PULL → TrackedObject 복원 → on_tracks 콜백 (수신 전용 스레드)."""

    def __init__(self, on_tracks: OnTracks,
                 connect: str = "tcp://127.0.0.1:5701") -> None:
        super().__init__(daemon=True, name="ds-bridge")
        self.on_tracks = on_tracks
        self.connect_addr = connect
        self._stop_evt = threading.Event()

        # 통계
        self._lock = threading.Lock()
        self._msgs = 0
        self._tracks = 0
        self._by_cam: dict[str, int] = {}
        self._last_ts: dict[str, float] = {}

    def stop(self) -> None:
        self._stop_evt.set()

    def stats(self) -> dict:
        with self._lock:
            return {
                "msgs": self._msgs,
                "tracks": self._tracks,
                "msgs_by_cam": dict(self._by_cam),
                "last_ts_by_cam": dict(self._last_ts),
            }

    def run(self) -> None:
        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.PULL)
        sock.setsockopt(zmq.RCVHWM, 1000)
        sock.connect(self.connect_addr)
        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)
        logger.info("브리지 수신 시작: %s", self.connect_addr)
        try:
            while not self._stop_evt.is_set():
                if not poller.poll(500):
                    continue
                raw = sock.recv(zmq.DONTWAIT)
                try:
                    msg = _decode(raw)
                    cam_id = msg["cam_id"]
                    ts = float(msg["ts"])
                    tracks = [
                        TrackedObject(
                            cam_id=cam_id,
                            local_track_id=int(t["local_track_id"]),
                            foot_uv=tuple(t["foot_uv"]),
                            bbox_xyxy=tuple(t["bbox_xyxy"]),
                            conf=float(t["conf"]),
                            ts=ts,
                        )
                        for t in msg["tracks"]
                    ]
                except Exception:
                    logger.exception("메시지 디코드 실패 (%d바이트)", len(raw))
                    continue
                with self._lock:
                    self._msgs += 1
                    self._tracks += len(tracks)
                    self._by_cam[cam_id] = self._by_cam.get(cam_id, 0) + 1
                    self._last_ts[cam_id] = ts
                try:
                    self.on_tracks(cam_id, ts, tracks)
                except Exception:
                    logger.exception("[%s] on_tracks 콜백 실패", cam_id)
        finally:
            sock.close()
            logger.info("브리지 수신 종료")


def _main() -> None:
    """단독 실행 — 수신 통계를 5초마다 출력 (워커 검증용)."""
    ap = argparse.ArgumentParser(description="DS 워커 트랙 수신 브리지 (통계 모드)")
    ap.add_argument("--connect", default="tcp://127.0.0.1:5701")
    ap.add_argument("--duration", type=float, default=0.0, help="N초 후 종료 (0=무한)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    latest: dict[str, int] = {}   # 카메라별 마지막 메시지의 트랙 수

    def on_tracks(cam_id: str, ts: float, tracks: list[TrackedObject]) -> None:
        latest[cam_id] = len(tracks)

    bridge = TrackBridge(on_tracks, connect=args.connect)
    bridge.start()

    t_end = time.time() + args.duration if args.duration > 0 else None
    prev = bridge.stats()
    try:
        while t_end is None or time.time() < t_end:
            time.sleep(5)
            cur = bridge.stats()
            rate = {c: (cur["msgs_by_cam"].get(c, 0) - prev["msgs_by_cam"].get(c, 0)) / 5.0
                    for c in cur["msgs_by_cam"]}
            logger.info("RECV msgs=%d tracks=%d | msg/s=%s | 트랙수(최근)=%s",
                        cur["msgs"], cur["tracks"],
                        {k: round(v, 2) for k, v in sorted(rate.items())},
                        dict(sorted(latest.items())))
            prev = cur
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
        bridge.join(timeout=2)
        final = bridge.stats()
        logger.info("최종: %s", final)


if __name__ == "__main__":
    _main()
