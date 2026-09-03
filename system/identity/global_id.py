"""카메라 간 동일인 연결(글로벌 ID) — 갤러리·코사인 매칭·TTL.

요구사항 '카테고리 1: Multi-Camera ID 병합'(최종 단계)의 1차 구현.
켜짐/파라미터는 `data/global_id.json` — ① 설정 [추론 모델] 패널에서 조작하며,
**꺼져 있으면(기본) 이 모듈은 전혀 실행되지 않고** 기존 gid(`f"{cam}:{local}"`)
동작 그대로다(온오프 롤백 보장).

설계 전제(사용자 확정): **카메라 매핑 헐은 서로 겹치지 않는다** → 같은 사람이
같은 순간 두 카메라에 보일 수 없다. 그래서
  - 특징 수집은 헐 안 관측만(엔진이 투영 성공한 프레임에서만 resolve 호출),
  - 병합은 '핸드오버'(A헐 이탈 → 나중에 B헐 등장)에만 일어나고,
  - 매칭 후보가 **지금 다른 카메라에서 관측 중이면 무조건 기각**한다(동시 활성
    기각 — 전제상 같은 사람일 수 없으므로 닮은 타인이다). 오병합(두 사람=한 id)은
    출구 debounce 를 먹어 통과 인원이 증발하는, 유실보다 나쁜 실패라 보수적으로 간다.

특징 벡터는 BoostTrack 이 트랙마다 이미 유지하는 EMA 임베딩(CLIP-ReID 768d /
FastReID 2048d)을 재사용한다 — 추가 GPU 비용 0, 여기선 코사인 행렬곱만 한다.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

STATE_FILE = Path("data/global_id.json")     # model_zoo.STATE_FILE 과 같은 컨벤션
DEFAULTS = {
    "enabled": False,        # 기본 off = 현행 동작 그대로 (로컬 디폴트)
    "ttl_sec": 600.0,        # 갤러리 기억 시간 — 마지막 관측 후 이 안에 재등장하면 같은 id
    "cos_th": 0.45,          # 코사인 유사도 매칭 임계 (리허설 실측으로 캘리브레이션)
    "update_every": 40,      # 바인딩된 트랙의 프로토타입(EMA) 갱신 주기 (관측 횟수)
}
# '지금 활성' 판정 창 — 동시 활성 기각용. "같은 순간 두 카메라"만 기각해야 하므로
# 분석 프레임 간격(5fps=0.2s)보다 약간 큰 값. 크게 잡으면(예: 1s) 인접 헐 사이의
# 정상 핸드오버(경계에서 경계로 1초 미만 이동)까지 기각해 조각화가 생긴다.
_ACTIVE_SEC = 0.3

_lock = threading.Lock()
_cache: dict | None = None
_cache_at = 0.0


def get_settings() -> dict:
    """설정 로드 (2초 캐시) — 엔진이 프레임마다 불러도 부담 없게."""
    global _cache, _cache_at
    with _lock:
        now = time.monotonic()
        if _cache is not None and now - _cache_at < 2.0:
            return _cache
        d = dict(DEFAULTS)
        try:
            if STATE_FILE.is_file():
                raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                for k in DEFAULTS:
                    if k in raw:
                        d[k] = type(DEFAULTS[k])(raw[k])
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass                                  # 깨진 파일 → 기본값 (off)
        _cache, _cache_at = d, now
        return d


def save_settings(patch: dict) -> dict:
    """부분 갱신 저장 — 반환은 저장된 전체 설정."""
    global _cache, _cache_at
    with _lock:
        d = dict(DEFAULTS)
        try:
            if STATE_FILE.is_file():
                d.update({k: v for k, v in json.loads(
                    STATE_FILE.read_text(encoding="utf-8")).items() if k in DEFAULTS})
        except (OSError, json.JSONDecodeError):
            pass
        for k, v in patch.items():
            if k in DEFAULTS and v is not None:
                d[k] = type(DEFAULTS[k])(v)
        d["ttl_sec"] = max(1.0, float(d["ttl_sec"]))
        d["cos_th"] = min(0.99, max(0.05, float(d["cos_th"])))
        d["update_every"] = max(1, int(d["update_every"]))
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(d, ensure_ascii=False) + "\n", encoding="utf-8")
        _cache, _cache_at = d, time.monotonic()
        return d


def _norm(v) -> np.ndarray | None:
    """임베딩 검증·정규화 — 더미(use_embedding off 의 ones((1,)))는 None."""
    a = np.asarray(v, dtype=np.float32).reshape(-1)
    if a.size < 16:
        return None
    n = float(np.linalg.norm(a))
    if n < 1e-6:
        return None
    return a / n


@dataclass
class _Ident:
    """정체성 1개 — 프로토타입 벡터 + 최근 관측 상태."""
    proto: np.ndarray
    last_ts: float
    cam: str                      # 마지막(=현재) 관측 카메라
    n_obs: int = 1                # resolve 호출 누계 (EMA 주기용)


@dataclass
class GlobalIdService:
    """세션/층 단위 글로벌 ID 갤러리. MetricsEngine 락 안에서만 호출된다(스레드 안전 불요)."""
    ttl_sec: float = 600.0
    cos_th: float = 0.45
    update_every: int = 40
    _gallery: dict[str, _Ident] = field(default_factory=dict)
    _bind: dict[tuple[str, int], str] = field(default_factory=dict)
    _next: int = 1

    def reset(self) -> None:
        """세션 시작 시 호출 — id 공간을 g1부터 새로 (리포트 가독성·세션 독립성)."""
        self._gallery.clear()
        self._bind.clear()
        self._next = 1

    def lookup(self, cam: str, local: int) -> str | None:
        return self._bind.get((cam, local))

    def resolve(self, cam: str, local: int, emb, ts: float) -> str | None:
        """헐 안 관측 1회 — 바인딩돼 있으면 갱신, 아니면 매칭/신규. None = emb 무효."""
        key = (cam, int(local))
        gid = self._bind.get(key)
        if gid is not None:
            ident = self._gallery.get(gid)
            if ident is not None:
                ident.last_ts = ts
                ident.cam = cam
                ident.n_obs += 1
                if ident.n_obs % self.update_every == 0:
                    v = _norm(emb)
                    if v is not None:            # 프로토타입 EMA 갱신 (드리프트 추종)
                        ident.proto = _norm(0.9 * ident.proto + 0.1 * v)
            return gid
        v = _norm(emb)
        if v is None:
            return None                          # 특징 없음 — 다음 프레임에 재시도
        best_gid, best_cos = None, -1.0
        for g, ident in self._gallery.items():
            if ts - ident.last_ts > self.ttl_sec:
                continue                         # TTL 만료 — 사실상 삭제
            if ts - ident.last_ts < _ACTIVE_SEC and ident.cam != cam:
                continue                         # 동시 활성 기각 (배타 헐 전제)
            c = float(ident.proto @ v)
            if c > best_cos:
                best_gid, best_cos = g, c
        if best_gid is not None and best_cos >= self.cos_th:
            gid = best_gid                       # 재등장 — 같은 사람
            ident = self._gallery[gid]
            ident.proto = _norm(0.7 * ident.proto + 0.3 * v)
            ident.last_ts, ident.cam = ts, cam
            ident.n_obs += 1
        else:
            gid = f"g{self._next}"               # 새 사람
            self._next += 1
            self._gallery[gid] = _Ident(proto=v, last_ts=ts, cam=cam)
        self._bind[key] = gid
        self._purge(ts)
        return gid

    def touch(self, cam: str, local: int, ts: float) -> None:
        """emb 없는 프레임(헐 밖 등)에서도 '지금 활성' 상태 유지."""
        gid = self._bind.get((cam, int(local)))
        ident = self._gallery.get(gid) if gid else None
        if ident is not None:
            ident.last_ts = ts
            ident.cam = cam

    def _purge(self, now: float) -> None:
        if len(self._gallery) < 512:
            return                               # 훈련 규모에선 거의 안 탐
        dead = [g for g, i in self._gallery.items() if now - i.last_ts > self.ttl_sec]
        for g in dead:
            self._gallery.pop(g, None)
        if dead:
            gone = set(dead)
            for k in [k for k, g in self._bind.items() if g in gone]:
                self._bind.pop(k, None)
