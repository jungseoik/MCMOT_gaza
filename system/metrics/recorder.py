"""세션 녹화 — 경보 세션 동안 엔진 입력(on_tracks의 raw 트랙)을 세션별
SQLite에 append 기록하고, 리플레이/재계산 시 그대로 되읽는다.

설계 근거: docs/architecture/05-세션-녹화-리플레이-지표재계산-설계.md

무엇을 녹화하나:
- `tracks` 테이블에 프레임별 원본 트랙 (call_seq, ts, cam_id, local_id, u, v, conf).
  **min_conf 필터 이전의 raw 관측**을 저장한다 → 재생 시 min_conf도 바꿔 재계산 가능.
- `meta` 테이블에 세션 시작 시점 스냅샷 (그 층의 공간요소 SiteConfig 뷰·카메라·
  경보원·alarm_ts·축척). 재생은 이 스냅샷으로 엔진을 그대로 복원한다.

왜 결정적인가: MetricsEngine은 "관측 ts 기준 결정적"이라, 같은 입력 스트림을
같은 순서(call_seq)로 다시 흘려보내면 같은 결과가 나온다. 따라서 저장된 트랙 +
임의 임계값으로 4대 지표를 재산출할 수 있다(도면·호모그래피 동일 전제).

스레드: record()/close()는 MetricsEngine._lock 안에서만 호출된다(직렬화 보장).
sqlite 커넥션은 check_same_thread=False로 열어 분석 스레드↔API 스레드 공용.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Iterator

from system.contracts import TrackedObject

logger = logging.getLogger("system.metrics.recorder")

SCHEMA_VERSION = "1"
_COMMIT_EVERY = 200          # 이만큼 on_tracks 호출마다 commit (I/O 완충)
_BUFFER_FLUSH = 500          # 버퍼 행이 이만큼 쌓이면 executemany


class SessionRecorder:
    """세션 1회의 입력 트랙을 <session_id>.db로 녹화."""

    def __init__(self, db_path: str | Path, meta: dict):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 진행 중 임시 파일에 쓰고 close 시 rename — 반쪽 db 노출 방지
        self._tmp = self.path.with_suffix(".db.part")
        if self._tmp.exists():
            self._tmp.unlink()
        self._con = sqlite3.connect(str(self._tmp), check_same_thread=False)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")
        self._con.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE tracks (
              call_seq INTEGER NOT NULL,
              ts       REAL    NOT NULL,
              cam_id   TEXT    NOT NULL,
              local_id INTEGER NOT NULL,
              u        REAL    NOT NULL,
              v        REAL    NOT NULL,
              conf     REAL
            );
            """
        )
        meta = {**meta, "schema_version": SCHEMA_VERSION}
        self._con.executemany(
            "INSERT INTO meta(key, value) VALUES(?, ?)",
            [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()],
        )
        self._con.commit()
        self._call_seq = 0
        self._buf: list[tuple] = []
        self._closed = False

    def record(self, cam_id: str, ts: float, tracks) -> None:
        """on_tracks 1회분 raw 트랙 버퍼링 (엔진 락 안에서 호출)."""
        if self._closed:
            return
        seq = self._call_seq
        self._call_seq += 1
        for tr in tracks:
            u, v = tr.foot_uv
            self._buf.append((seq, float(ts), cam_id, int(tr.local_track_id),
                              float(u), float(v), float(tr.conf)))
        if len(self._buf) >= _BUFFER_FLUSH:
            self._flush()
        if seq % _COMMIT_EVERY == 0:
            self._con.commit()

    def _flush(self) -> None:
        if not self._buf:
            return
        self._con.executemany(
            "INSERT INTO tracks(call_seq, ts, cam_id, local_id, u, v, conf) "
            "VALUES(?,?,?,?,?,?,?)", self._buf)
        self._buf.clear()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._flush()
            # call 수·트랙 수를 meta에 마감 기록
            n_tracks = self._con.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            self._con.executemany(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                [("call_count", json.dumps(self._call_seq)),
                 ("track_row_count", json.dumps(n_tracks))])
            self._con.execute("CREATE INDEX idx_tracks_seq ON tracks(call_seq)")
            self._con.commit()
            self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._con.close()
            self._tmp.replace(self.path)   # 원자적 완결
        except Exception:
            logger.exception("SessionRecorder close 실패: %s", self.path)
            try:
                self._con.close()
            except Exception:
                pass


# ------------------------------------------------------------ 재생(리더)

def load_meta(db_path: str | Path) -> dict:
    """녹화 db의 meta 전체를 dict로 (value는 JSON 디코드)."""
    con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT key, value FROM meta").fetchall()
    finally:
        con.close()
    return {k: json.loads(v) for k, v in rows}


def iter_calls(db_path: str | Path) -> Iterator[tuple[str, float, list[TrackedObject]]]:
    """call_seq 순서대로 (cam_id, ts, [TrackedObject...]) 묶음을 재생.

    원래 on_tracks 호출 단위(같은 call_seq)를 그대로 복원한다 → 엔진에 다시
    흘려보내면 결정적으로 동일 결과. bbox는 지표 계산에 미사용이라 더미."""
    con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        cur = con.execute(
            "SELECT call_seq, ts, cam_id, local_id, u, v, conf "
            "FROM tracks ORDER BY call_seq")
        cur_seq = None
        cam_id = None
        ts = 0.0
        batch: list[TrackedObject] = []
        for seq, row_ts, cid, lid, u, v, conf in cur:
            if cur_seq is None:
                cur_seq = seq
            if seq != cur_seq:
                yield cam_id, ts, batch
                batch = []
                cur_seq = seq
            cam_id, ts = cid, row_ts
            batch.append(TrackedObject(
                cam_id=cid, local_track_id=int(lid), foot_uv=(u, v),
                bbox_xyxy=(0.0, 0.0, 0.0, 0.0), conf=conf, ts=row_ts))
        if batch:
            yield cam_id, ts, batch
    finally:
        con.close()
