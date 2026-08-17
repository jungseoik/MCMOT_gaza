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


# ------------------------------------------------- 벌크 등록 (add_cameras)


def _mgr_with_restart_spy(monkeypatch, **kw):
    """_restart_slot 호출을 기록하는 매니저 — docker를 띄우지 않는다."""
    mgr = DsIngestManager(lambda *a: None, **kw)
    calls: list = []
    monkeypatch.setattr(DsIngestManager, "_restart_slot",
                        lambda self, slot: calls.append(slot))
    return mgr, calls


def test_add_cameras_슬롯당_재시작_1회(monkeypatch):
    """벌크 등록의 핵심 — N대를 넣어도 슬롯당 워커 재시작은 1회."""
    mgr, calls = _mgr_with_restart_spy(monkeypatch, gpu_devices=[1],
                                       workers_per_gpu=1)
    mgr.add_cameras(_cams(9))
    assert calls == [(1, 0)]                       # 9대인데 재시작 1회
    assert len(mgr._cam_slot) == 9


def test_add_camera_하나씩이면_매번_재시작(monkeypatch):
    """대조군 — 기존 경로는 대수만큼 재시작(그래서 느리고 매번 끊긴다)."""
    mgr, calls = _mgr_with_restart_spy(monkeypatch, gpu_devices=[1],
                                       workers_per_gpu=1)
    for cam in _cams(9):
        mgr.add_camera(cam)
    assert calls == [(1, 0)] * 9


def test_add_cameras_GPU_2장_부하분산_후_각1회(monkeypatch):
    """GPU가 여러 장이면 누적 fps가 작은 슬롯으로 자동 배정되고,
    재시작은 '영향받은 슬롯'마다 1회씩."""
    mgr, calls = _mgr_with_restart_spy(monkeypatch, gpu_devices=[0, 1],
                                       workers_per_gpu=1)
    mgr.add_cameras(_cams(8))
    assert calls == [(0, 0), (1, 0)]               # 슬롯 순서대로 각 1회
    per_slot = {s: sum(1 for v in mgr._cam_slot.values() if v == s)
                for s in mgr.slots}
    assert per_slot == {(0, 0): 4, (1, 0): 4}      # 균등 분배


def test_add_cameras_비활성은_슬롯배정_없음(monkeypatch):
    """enabled=False는 설정만 보관하고 워커에 올리지 않는다."""
    mgr, calls = _mgr_with_restart_spy(monkeypatch, gpu_devices=[1],
                                       workers_per_gpu=1)
    cams = _cams(3)
    cams[1]["enabled"] = False
    mgr.add_cameras(cams)
    assert calls == [(1, 0)]
    assert set(mgr._cam_slot) == {"cam01", "cam03"}
    assert "cam02" in mgr._cfgs                    # 설정은 남아 있다


def test_add_cameras_중복으로_실패해도_그전까지는_반영(monkeypatch):
    """도중 실패해도 이미 배정된 슬롯은 재시작 — 메모리/워커 불일치를 안 남긴다."""
    mgr, calls = _mgr_with_restart_spy(monkeypatch, gpu_devices=[1],
                                       workers_per_gpu=1)
    mgr.add_cameras(_cams(2))                      # cam01·cam02 등록됨
    calls.clear()

    new, dup = _cams(3)[2], _cams(1)[0]            # cam03(신규), cam01(중복)
    with pytest.raises(ValueError):
        mgr.add_cameras([new, dup])

    assert calls == [(1, 0)]                       # cam03 반영분 1회 재시작
    assert set(mgr._cam_slot) == {"cam01", "cam02", "cam03"}


def test_add_camera_defer_restart는_재시작_생략(monkeypatch):
    mgr, calls = _mgr_with_restart_spy(monkeypatch, gpu_devices=[1],
                                       workers_per_gpu=1)
    slot = mgr.add_camera(_cams(1)[0], defer_restart=True)
    assert slot == (1, 0) and calls == []


# ------------------------------------------- 벌크 변경 (update_cameras)


def test_update_cameras_일괄활성화_재시작_1회(monkeypatch):
    """매핑 후 일괄 활성화 — 9대를 켜도 워커 재시작은 1회."""
    mgr, calls = _mgr_with_restart_spy(monkeypatch, gpu_devices=[1],
                                       workers_per_gpu=1)
    cams = _cams(9)
    for c in cams:
        c["enabled"] = False
    mgr.add_cameras(cams)                          # 비활성 등록 → 슬롯 배정 없음
    assert calls == [] and mgr._cam_slot == {}

    for c in cams:
        c["enabled"] = True
    mgr.update_cameras(cams)
    assert calls == [(1, 0)]                       # 9대를 켰는데 재시작 1회
    assert len(mgr._cam_slot) == 9


def test_update_camera_하나씩_켜면_매번_재시작(monkeypatch):
    """대조군 — 비활성→활성은 신규 추가와 같은 경로라 켤 때마다 재시작된다."""
    mgr, calls = _mgr_with_restart_spy(monkeypatch, gpu_devices=[1],
                                       workers_per_gpu=1)
    cams = _cams(9)
    for c in cams:
        c["enabled"] = False
    mgr.add_cameras(cams)
    calls.clear()
    for c in cams:
        mgr.update_camera({**c, "enabled": True})
    assert calls == [(1, 0)] * 9


def test_update_cameras_비활성화도_슬롯에서_내린다(monkeypatch):
    mgr, calls = _mgr_with_restart_spy(monkeypatch, gpu_devices=[1],
                                       workers_per_gpu=1)
    cams = _cams(3)
    mgr.add_cameras(cams)
    calls.clear()
    mgr.update_cameras([{**cams[0], "enabled": False}])
    assert calls == [(1, 0)]
    assert set(mgr._cam_slot) == {"cam02", "cam03"}


def test_update_cameras_변화없는_비활성은_재시작_안함(monkeypatch):
    """이미 비활성인 카메라의 이름만 바꾸는 등 — 워커를 건드릴 이유가 없다."""
    mgr, calls = _mgr_with_restart_spy(monkeypatch, gpu_devices=[1],
                                       workers_per_gpu=1)
    cam = {**_cams(1)[0], "enabled": False}
    mgr.add_cameras([cam])
    calls.clear()
    mgr.update_cameras([{**cam, "analyze_fps": 3.0}])
    assert calls == []


# ------------------------------------------------------------ env 파싱


def test_parse_workers_per_gpu(monkeypatch):
    monkeypatch.delenv("WORKERS_PER_GPU", raising=False)
    assert parse_workers_per_gpu() == 1          # 기본값 — 기존 동작
    monkeypatch.setenv("WORKERS_PER_GPU", "2")
    assert parse_workers_per_gpu() == 2
    assert parse_workers_per_gpu(3) == 3         # 인자 우선
    with pytest.raises(ValueError):
        parse_workers_per_gpu(0)
