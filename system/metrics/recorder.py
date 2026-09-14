"""세션 녹화 — 경보 세션 동안 엔진 입력(on_tracks의 raw 트랙)을 세션별
SQLite에 append 기록하고, 리플레이/재계산 시 그대로 되읽는다.

설계 근거: docs/architecture/05-세션-녹화-리플레이-지표재계산-설계.md

무엇을 녹화하나:
- `tracks` 테이블에 프레임별 원본 트랙 (call_seq, ts, cam_id, local_id, u, v, conf,
  **bbox x1,y1,x2,y2**). **min_conf 필터 이전의 raw 관측**을 저장한다 → 재생 시
  min_conf도 바꿔 재계산 가능.
  bbox를 기록하는 이유(v1.12): 화면 **영역** 출입구(ZoneGate)는 발끝점이 문틀에
  잘리는 것을 bbox 겹침으로 보정한다. bbox를 더미로 재생하면 그 보정이 죽어
  통과 인원이 실제의 1/3까지 빠지고 SEI가 틀어진다(16F 실측: 라이브 19명 →
  리플레이 6명). bbox 없는 옛 녹화(schema 1)는 더미로 재생된다(하위호환).
- `track_embs` 테이블에 **트랙렛별 ReID 임베딩 대표 샘플**(기본 8개, v1.14).
  세션이 끝난 뒤 트랙 조각을 사람 단위로 다시 묶는 오프라인 재구성(여정)에 쓴다.
  실시간 추적은 max_age(기본 fps×2s) 를 넘겨 안 보이면 트랙을 버리므로, 가렸다
  나온 사람은 새 번호를 받는다(실측: 실제 10명이 트랙 125개). 끝난 뒤에는 전부
  기록에 남아 있어 임베딩만 있으면 다시 이을 수 있다 — 그 근거 데이터다.
  임베딩은 트래커가 이미 계산해 쓰는 값이라 **추가 GPU 연산이 없다**. 전량이
  아니라 트랙당 시간 균등 N개만 남긴다(full 502s 세션 기준 44MB → 15MB).
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

import numpy as np

from system.contracts import TrackedObject

logger = logging.getLogger("system.metrics.recorder")

SCHEMA_VERSION = "6"         # 2: bbox 4열 (v1.12) · 3: gid 열 (v1.13)
                             # 4: track_embs 테이블 — 트랙렛 ReID 임베딩 (v1.14)
                             # 5: track_embs.thumb — 대표 프레임 JPEG (v1.15)
                             # 6: 헐 밖도 외형 저장 + 트랙당 32장 (v1.16)
EMB_PER_TRACK = 32           # 트랙당 남길 대표 임베딩 수 (시간 균등 — 시점 변화 포착).
                             # 32 로 올린 이유: 트랙 **안쪽**의 뒤바뀜을 찾으려면
                             # 조밀해야 한다. 8장이면 5fps 기준 40초 트랙에서
                             # 5초에 1장이라 바뀐 지점을 못 짚는다. 용량은
                             # (벡터 1.5KB + 썸네일 2.3KB) x 32 ≈ 122KB/트랙.
_COMMIT_EVERY = 200          # 이만큼 on_tracks 호출마다 commit (I/O 완충)
_BUFFER_FLUSH = 500          # 버퍼 행이 이만큼 쌓이면 executemany



def _jpeg(crop, q: int = 78) -> bytes | None:
    """썸네일 BGR → JPEG 바이트. crop 이 None 이거나 인코딩 실패면 None.

    64x128 기준 개당 약 2KB — 트랙 1,200개 × 8장이면 20MB 남짓이라 임베딩
    (768d fp16 = 1.5KB) 과 비슷한 무게다.
    """
    if crop is None:
        return None
    try:
        import cv2
        ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), q])
        return buf.tobytes() if ok else None
    except Exception:
        return None

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
              conf     REAL,
              x1       REAL,
              y1       REAL,
              x2       REAL,
              y2       REAL,
              gid      TEXT
            );
            CREATE TABLE track_embs (
              cam_id   TEXT    NOT NULL,
              local_id INTEGER NOT NULL,
              ts       REAL    NOT NULL,
              dim      INTEGER NOT NULL,
              emb      BLOB    NOT NULL,     -- float16 정규화 벡터
              thumb    BLOB                  -- 64x128 JPEG (없으면 NULL)
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
        # 트랙렛별 대표 임베딩 (cam, local) -> [(ts, float16 vec, jpeg), ...].
        # 트랙 1,200개 × 32개 × (1.5KB + 2.3KB) ≈ 146MB — 큰 훈련에서는 무겁다.
        # 리허설·단일 층 규모(트랙 100여 개)에서는 4MB 수준이라 문제없다.
        self._embs: dict[tuple, list] = {}
        self._closed = False

    def record(self, cam_id: str, ts: float, tracks,
               gids: list[str | None] | None = None,
               in_hull: list[bool] | None = None) -> None:
        """on_tracks 1회분 raw 트랙 버퍼링 (엔진 락 안에서 호출).

        gids: tracks 와 정렬된 확정 global_id (글로벌 ID 모드, v1.13) — 리플레이가
        갤러리 상태를 재현하지 않고도 같은 id 를 쓰게 한다(결정성). None = 미확정.

        in_hull: tracks 와 정렬된 "임베딩·썸네일을 남길 관측인가" (v1.15).
        tracks 행은 raw 계약대로 전부 남기지만 외형은 헐(valid_roi) 안에서만
        남긴다 — 헐 밖은 호모그래피 외삽이라 맵 좌표가 부정확하고, 그 좌표로
        여정 재구성의 운동학 제약을 판정하면 근거 없는 판정이 된다.
        None = 전부 남김(구버전 호출 호환)."""
        if self._closed:
            return
        seq = self._call_seq
        self._call_seq += 1
        for i, tr in enumerate(tracks):
            u, v = tr.foot_uv
            x1, y1, x2, y2 = tr.bbox_xyxy
            self._buf.append((seq, float(ts), cam_id, int(tr.local_track_id),
                              float(u), float(v), float(tr.conf),
                              float(x1), float(y1), float(x2), float(y2),
                              (gids[i] if gids else None)))
            if in_hull is None or (i < len(in_hull) and in_hull[i]):
                self._note_emb(cam_id, int(tr.local_track_id), float(ts), tr.emb,
                               getattr(tr, "crop_bgr", None))
        if len(self._buf) >= _BUFFER_FLUSH:
            self._flush()
        if seq % _COMMIT_EVERY == 0:
            self._con.commit()

    def _note_emb(self, cam_id: str, local_id: int, ts: float, emb,
                  crop=None) -> None:
        """트랙렛별 임베딩을 **시간 균등**으로 EMB_PER_TRACK 개만 유지.

        앞부분만 담으면 그 사람의 초기 시점(각도·조명)만 남아 재구성 때
        뒤쪽 구간과 안 붙는다. 가득 차면 간격이 가장 촘촘한 한 개를 버려
        전 구간에 고르게 퍼지게 한다(의존성 없이 O(N)).
        """
        if emb is None:
            return
        try:
            v = np.asarray(emb, dtype=np.float32).reshape(-1)
        except Exception:
            return
        if v.size < 16:                      # DS 경로 등 더미(ones((1,)))는 버린다
            return
        n = float(np.linalg.norm(v))
        if n < 1e-6:
            return
        v = (v / n).astype(np.float16)
        key = (cam_id, local_id)
        slot = self._embs.setdefault(key, [])
        slot.append((ts, v, _jpeg(crop)))
        if len(slot) <= EMB_PER_TRACK:
            return
        # 이웃 간 시간 간격이 가장 작은 지점을 하나 제거 → 균등 유지
        gaps = [(slot[i + 1][0] - slot[i - 1][0], i) for i in range(1, len(slot) - 1)]
        _, drop = min(gaps)                  # 양끝(첫·마지막)은 항상 보존
        slot.pop(drop)

    def _flush(self) -> None:
        if self._embs:
            rows = [(cam, lid, ts, int(v.size), v.tobytes(), th)
                    for (cam, lid), slot in self._embs.items() for ts, v, th in slot]
            # 트랙별 최신 상태로 통째 교체 — 중복 없이 마지막 선택본만 남는다
            self._con.executemany(
                "DELETE FROM track_embs WHERE cam_id=? AND local_id=?",
                list(self._embs.keys()))
            self._con.executemany(
                "INSERT INTO track_embs(cam_id, local_id, ts, dim, emb, thumb)"
                " VALUES(?,?,?,?,?,?)", rows)
        if not self._buf:
            return
        self._con.executemany(
            "INSERT INTO tracks(call_seq, ts, cam_id, local_id, u, v, conf,"
            " x1, y1, x2, y2, gid) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", self._buf)
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
            self._con.execute(
                "CREATE INDEX idx_embs_track ON track_embs(cam_id, local_id)")
            n_embs = self._con.execute("SELECT COUNT(*) FROM track_embs").fetchone()[0]
            self._con.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                ("emb_row_count", json.dumps(n_embs)))
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


def load_track_embs(db_path: str | Path) -> dict[tuple[str, int], list[tuple[float, "np.ndarray"]]]:
    """트랙렛별 대표 임베딩 — {(cam_id, local_id): [(ts, 정규화 float32 벡터), ...]}.

    여정 재구성(오프라인 클러스터링)의 입력. 임베딩이 없는 옛 녹화(schema ≤3)는
    빈 dict 를 돌려준다 — 호출부가 "재구성 불가"로 안내하면 된다.
    """
    con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "track_embs" not in names:
            return {}
        out: dict[tuple[str, int], list] = {}
        for cam, lid, ts, dim, blob in con.execute(
                "SELECT cam_id, local_id, ts, dim, emb FROM track_embs ORDER BY cam_id, local_id, ts"):
            v = np.frombuffer(blob, dtype=np.float16).astype(np.float32)
            if v.size != dim:
                continue
            out.setdefault((cam, int(lid)), []).append((float(ts), v))
        return out
    finally:
        con.close()


def load_track_thumbs(db_path: str | Path,
                      keys: "list[tuple[str, int]] | None" = None) -> dict:
    """트랙렛별 대표 썸네일 — {(cam_id, local_id): [(ts, jpeg bytes), ...]}.

    임베딩과 같은 프레임에서 뽑은 것이라 "이 벡터가 어떤 사람이었나" 를 그대로
    보여준다. thumb 열이 없는 옛 녹화(schema ≤4)는 빈 dict.
    """
    con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "track_embs" not in names:
            return {}
        cols = {r[1] for r in con.execute("PRAGMA table_info(track_embs)")}
        if "thumb" not in cols:
            return {}
        want = set(keys) if keys else None
        out: dict[tuple[str, int], list] = {}
        for cam, lid, ts, blob in con.execute(
                "SELECT cam_id, local_id, ts, thumb FROM track_embs"
                " WHERE thumb IS NOT NULL ORDER BY cam_id, local_id, ts"):
            k = (cam, int(lid))
            if want is not None and k not in want:
                continue
            out.setdefault(k, []).append((float(ts), bytes(blob)))
        return out
    finally:
        con.close()


def iter_calls(db_path: str | Path) -> Iterator[tuple[str, float, list[TrackedObject]]]:
    """call_seq 순서대로 (cam_id, ts, [TrackedObject...]) 묶음을 재생.

    원래 on_tracks 호출 단위(같은 call_seq)를 그대로 복원한다 → 엔진에 다시
    흘려보내면 결정적으로 동일 결과. bbox는 화면 영역 출입구(ZoneGate) 판정에
    쓰이므로 있으면 그대로 복원하고, 없는 옛 녹화(schema 1)는 더미로 채운다."""
    con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(tracks)")}
        has_bbox, has_gid = "x1" in cols, "gid" in cols
        cur = con.execute(
            "SELECT call_seq, ts, cam_id, local_id, u, v, conf, "
            + ("x1, y1, x2, y2, " if has_bbox else "0, 0, 0, 0, ")
            + ("gid " if has_gid else "NULL ")
            + "FROM tracks ORDER BY call_seq")
        cur_seq = None
        cam_id = None
        ts = 0.0
        batch: list[TrackedObject] = []
        for seq, row_ts, cid, lid, u, v, conf, x1, y1, x2, y2, gid in cur:
            if cur_seq is None:
                cur_seq = seq
            if seq != cur_seq:
                yield cam_id, ts, batch
                batch = []
                cur_seq = seq
            cam_id, ts = cid, row_ts
            batch.append(TrackedObject(
                cam_id=cid, local_track_id=int(lid), foot_uv=(u, v),
                bbox_xyxy=(x1 or 0.0, y1 or 0.0, x2 or 0.0, y2 or 0.0),
                conf=conf, ts=row_ts, gid_hint=(gid or None)))
        if batch:
            yield cam_id, ts, batch
    finally:
        con.close()
