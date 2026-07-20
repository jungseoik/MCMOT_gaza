"""ingest_ds launcher 슬롯 로직 단위테스트 — docker 불필요(순수 로직만).

WORKERS_PER_GPU=1이 기존 단일 워커 동작(이름·포트·분할)과 완전히 같은지,
≥2에서 슬롯 분할·포트 체계가 겹치지 않는지를 검증한다.
"""
import pytest

from system.ingest_ds.launcher import (
    CONTAINER_PREFIX,
    DsIngestManager,
    WorkerContainer,
    parse_workers_per_gpu,
    partition_cams,
    worker_port,
)


def _cams(n: int, fps: float = 5.0) -> list[dict]:
    return [{"cam_id": f"cam{i + 1:02d}", "rtsp": f"rtsp://x/{i}",
             "analyze_fps": fps} for i in range(n)]


# ------------------------------------------------------------ partition_cams


def test_partition_int_keys_기존호출_하위호환():
    """GPU id(int) 목록 호출 — 기존 시그니처 그대로 동작."""
    assign = partition_cams(_cams(4), [0, 1])
    assert set(assign) == {0, 1}
    assert len(assign[0]) == 2 and len(assign[1]) == 2
    # 결정적: 같은 입력이면 같은 결과
    assert assign == partition_cams(_cams(4), [0, 1])


def test_partition_slot_keys_부하균등():
    """(gpu, worker) 슬롯 튜플 키로도 부하 균등 분할."""
    slots = [(1, 0), (1, 1)]
    assign = partition_cams(_cams(16), slots)
    assert {len(assign[s]) for s in slots} == {8}
    # 전 채널이 정확히 한 슬롯에만 배정
    all_ids = sorted(c["cam_id"] for cams in assign.values() for c in cams)
    assert all_ids == sorted(c["cam_id"] for c in _cams(16))


def test_partition_홀수채널_편차1이내():
    assign = partition_cams(_cams(13), [(1, 0), (1, 1)])
    sizes = sorted(len(v) for v in assign.values())
    assert sizes == [6, 7]


# ------------------------------------------------------------ 포트 체계


def test_worker_port_w0은_기존포트와_동일():
    # 기존 단일 워커 컨벤션: 5701 + K
    assert worker_port(0) == 5701
    assert worker_port(1) == 5702
    assert worker_port(1, 0) == 5702


def test_worker_port_충돌없음():
    ports = {worker_port(g, j) for g in range(4) for j in range(4)}
    assert len(ports) == 16
    assert worker_port(1, 1) == 5802


# ------------------------------------------------------------ WorkerContainer


def test_container_단일워커_이름_기존과동일():
    wc = WorkerContainer(1)
    assert wc.name == f"{CONTAINER_PREFIX}-gpu1"
    assert wc.port == 5702
    assert wc.endpoint == "tcp://127.0.0.1:5702"


def test_container_2분할_이름과포트():
    w0 = WorkerContainer(1, worker=0, n_workers=2)
    w1 = WorkerContainer(1, worker=1, n_workers=2)
    assert w0.name == f"{CONTAINER_PREFIX}-gpu1-w0"
    assert w1.name == f"{CONTAINER_PREFIX}-gpu1-w1"
    assert (w0.port, w1.port) == (5702, 5802)
    assert w0.cams_path != w1.cams_path


def test_container_워커인덱스_범위검증():
    with pytest.raises(ValueError):
        WorkerContainer(1, worker=2, n_workers=2)


# ------------------------------------------------------------ DsIngestManager


def test_manager_기본1워커_슬롯과이름_기존동일():
    mgr = DsIngestManager(lambda *a: None, gpu_devices=[0, 1],
                          workers_per_gpu=1)
    assert mgr.slots == [(0, 0), (1, 0)]
    names = [w.name for w in mgr.workers.values()]
    assert names == [f"{CONTAINER_PREFIX}-gpu0", f"{CONTAINER_PREFIX}-gpu1"]


def test_manager_2분할_슬롯과엔드포인트():
    mgr = DsIngestManager(lambda *a: None, gpu_devices=[1],
                          workers_per_gpu=2)
    assert mgr.slots == [(1, 0), (1, 1)]
    eps = [w.endpoint for w in mgr.workers.values()]
    assert eps == ["tcp://127.0.0.1:5702", "tcp://127.0.0.1:5802"]


# ------------------------------------------------------------ env 파싱


def test_parse_workers_per_gpu(monkeypatch):
    monkeypatch.delenv("WORKERS_PER_GPU", raising=False)
    assert parse_workers_per_gpu() == 1          # 기본값 — 기존 동작
    monkeypatch.setenv("WORKERS_PER_GPU", "2")
    assert parse_workers_per_gpu() == 2
    assert parse_workers_per_gpu(3) == 3         # 인자 우선
    with pytest.raises(ValueError):
        parse_workers_per_gpu(0)
