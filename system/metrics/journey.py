"""여정 재구성 — 세션이 끝난 뒤 트랙 조각을 사람 단위로 다시 묶는다 (오프라인 ReID).

왜 필요한가
-----------
실시간 추적은 카메라 안에서만 ID 를 잇고(BoostTrack), 그나마 max_age(기본 fps×2s)
를 넘겨 안 보이면 트랙을 버린다. 그래서 한 사람이 여러 조각으로 갈린다 —
AI hub 3F scenario_01 실측: **실제 10명이 트랙렛 75개**(1인당 7.5조각).
카메라가 7대이니 7조각은 당연하고, 나머지가 가림·검출누락으로 생긴 분절이다.

온라인 글로벌 ID(system/identity)는 "지금까지 본 것"만으로 즉시 결정해야 하고
한 번 바인딩되면 되돌릴 수 없다. 세션이 끝난 뒤에는 전 구간이 기록에 남아 있으므로
**양방향으로 보고 전역 최적화**할 수 있다 — 그게 이 모듈이다.

입력: 세션 녹화 db (tracks + track_embs, schema ≥4)
출력: 클러스터(=사람) 목록. 각 클러스터가 어떤 트랙렛으로 이뤄졌는지 + 동선·출구.

알고리즘
--------
1. 트랙렛 집계 — (cam_id, local_id) 별 시작/종료 시각·맵 위치(m)·대표 임베딩
2. 유사도 S[i,j] = max 코사인 (프로토타입 집합 간)
3. k-reciprocal 재랭킹 (Zhong et al., CVPR 2017) — "서로를 이웃으로 꼽는가"를 반영해
   비슷한 옷차림이 많은 군중에서 오매칭을 줄인다
4. **물리 제약**으로 cannot-link:
   - 같은 카메라에서 시간이 겹침 → 한 카메라에 같은 사람이 둘일 수 없다
   - 시간차 대비 이동거리가 불가능 → 그 시간에 거기까지 못 간다
5. 제약 병합 클러스터링 — 유사도 높은 쌍부터 합치되 cannot-link 를 어기면 건너뛴다.
   문턱(cos_th)으로 정지 — 인원수 입력을 받지 않는다(현장마다 달라 오히려 노이즈).

실측 근거(AI hub 3F): 같은 트랙렛 내 코사인 중앙 0.995 / 다른 트랙렛 간 중앙 0.299,
트랙렛 71개 기준 0.90 문턱에서 서로 다른 사람이 붙는 경우 0%.
"""
from __future__ import annotations

import logging
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

from system.metrics import recorder

logger = logging.getLogger("system.metrics.journey")

# 병합 문턱 — 이보다 유사하면 같은 사람 후보. 실측 분리도(0.995 vs 0.299)에서
# 넉넉히 잡아도 오매칭이 거의 없다. 재랭킹 거리는 [0,1] 이라 1-dist 로 환산해 쓴다.
DEFAULT_COS_TH = 0.62
MAX_SPEED_MPS = 3.0        # 사람 보행 상한 — 이보다 빠른 재등장은 타인
SLACK_M = 3.0              # 카메라 간 매핑 오프셋 여유 — 이 거리 안은 속도 판정 생략
MIN_OBS = 2                # 이만큼 미만 관측 트랙렛은 노이즈로 제외
# 클러스터 관측 수가 이보다 적으면 '파편'으로 분리 — 사람 수 집계에서 뺀다.
# 실측(AI hub 3F scenario_01): 22 클러스터 중 관측 50+ 가 14개로 전체 관측의 96%,
# 나머지 8개는 4~30 관측(0.6~9초)짜리 오탐·스침이었다. 분포가 뚜렷하게 갈린다.
FRAGMENT_OBS = 50


def _rerank(S: np.ndarray, k1: int = 20, k2: int = 6, lam: float = 0.3) -> np.ndarray:
    """k-reciprocal 재랭킹 — 유사도 행렬 → 재계산 **거리** 행렬 [0,1].

    원 구현(CVPR 2017)은 query/gallery 를 나누지만 여기선 트랙렛끼리의 대칭 문제라
    전체를 한 집합으로 넣는다(all-to-all). 이웃 수 k1 은 트랙렛 수에 맞춰 줄인다.
    """
    n = S.shape[0]
    if n < 3:
        return 1.0 - S
    k1 = max(2, min(k1, n - 1))
    k2 = max(1, min(k2, k1))
    orig = 1.0 - S                                  # 코사인 → 거리
    orig = orig / (orig.max() + 1e-12)
    init_rank = np.argsort(orig, axis=1)
    V = np.zeros_like(orig, dtype=np.float32)
    for i in range(n):
        fwd = init_rank[i, : k1 + 1]
        bwd = init_rank[fwd, : k1 + 1]
        # 상호 이웃 — i 가 꼽은 이웃 중, 그 이웃도 i 를 꼽은 것만
        recip = fwd[np.any(bwd == i, axis=1)]
        exp = recip
        for c in recip:                             # 이웃의 이웃으로 한 번 확장
            cand = init_rank[c, : int(round(k1 / 2)) + 1]
            cand_bwd = init_rank[cand, : int(round(k1 / 2)) + 1]
            cr = cand[np.any(cand_bwd == c, axis=1)]
            if len(np.intersect1d(cr, recip)) > 2 / 3 * len(cr):
                exp = np.append(exp, cr)
        exp = np.unique(exp)
        w = np.exp(-orig[i, exp])
        V[i, exp] = w / (w.sum() + 1e-12)
    if k2 > 1:                                      # 이웃 평균으로 평활
        Vq = np.zeros_like(V)
        for i in range(n):
            Vq[i] = V[init_rank[i, :k2]].mean(axis=0)
        V = Vq
    # Jaccard 거리 — 이웃 집합이 얼마나 겹치나
    jac = np.zeros_like(orig, dtype=np.float32)
    for i in range(n):
        mn = np.minimum(V[i], V).sum(axis=1)
        mx = np.maximum(V[i], V).sum(axis=1)
        jac[i] = 1.0 - mn / (mx + 1e-12)
    return jac * (1 - lam) + orig * lam


def _tracklets(db_path: Path) -> tuple[list[dict], dict]:
    """트랙렛 집계 — (cam_id, local_id) 별 시각·맵위치(m)·임베딩."""
    meta = recorder.load_meta(db_path)
    mpp = float(((meta.get("site_view") or {}).get("map") or {}).get("m_per_px") or 0) or None
    H = {}
    for c in meta.get("cameras", []):
        m = c.get("mapping")
        if m and m.get("H"):
            H[c["cam_id"]] = np.asarray(m["H"], dtype=np.float64).reshape(3, 3)
    embs = recorder.load_track_embs(db_path)

    con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT cam_id, local_id, ts, u, v FROM tracks ORDER BY cam_id, local_id, ts"
        ).fetchall()
    finally:
        con.close()

    agg = defaultdict(list)
    for cam, lid, ts, u, v in rows:
        agg[(cam, int(lid))].append((float(ts), float(u), float(v)))

    out = []
    for (cam, lid), obs in agg.items():
        if len(obs) < MIN_OBS:
            continue
        E = embs.get((cam, lid)) or []
        if not E:
            continue                                 # 임베딩 없으면 묶을 근거가 없다
        P = np.stack([v for _, v in E])              # (k, dim) 정규화 완료
        h = H.get(cam)
        def xy(u, v):
            if h is None or mpp is None:
                return None
            p = h @ np.array([u, v, 1.0])
            if abs(p[2]) < 1e-9:
                return None
            return (float(p[0] / p[2]) * mpp, float(p[1] / p[2]) * mpp)
        out.append({
            "key": f"{cam}:{lid}", "cam": cam, "local_id": lid,
            "t0": obs[0][0], "t1": obs[-1][0], "n": len(obs),
            "p0": xy(obs[0][1], obs[0][2]), "p1": xy(obs[-1][1], obs[-1][2]),
            "protos": P,
        })
    out.sort(key=lambda d: (d["t0"], d["cam"]))
    return out, meta


def _cannot_link(a: dict, b: dict) -> bool:
    """물리적으로 같은 사람일 수 없는 쌍인가."""
    # ① 같은 카메라에서 시간이 겹친다 → 한 카메라에 같은 사람이 둘일 수 없다
    overlap = min(a["t1"], b["t1"]) - max(a["t0"], b["t0"])
    if a["cam"] == b["cam"] and overlap > 0:
        return True
    # ② 시간차 대비 이동거리가 불가능하다 (겹치면 판정 생략 — 시야 겹침 핸드오버)
    if overlap <= 0 and a["p1"] and b["p0"]:
        first, second = (a, b) if a["t1"] <= b["t0"] else (b, a)
        dt = second["t0"] - first["t1"]
        if dt > 0:
            d = math.dist(first["p1"], second["p0"])
            if d > SLACK_M and d / dt > MAX_SPEED_MPS:
                return True
    return False


def persons_index(persons: list, pid) -> int:
    """person_id 의 표시 순서 — 정렬 키(파편은 뒤로)."""
    for i, p in enumerate(persons):
        if p["person_id"] == pid:
            return i
    return len(persons)


def _project2d(P: np.ndarray) -> np.ndarray:
    """트랙렛 대표벡터 → 2D 좌표 (산점도용).

    768d 를 바로 t-SNE 에 넣으면 느리고 불안정하므로 PCA 로 30d 까지 줄인 뒤 t-SNE.
    표본이 너무 적으면(perplexity 확보 불가) PCA 2d 로 떨어진다.
    """
    n = P.shape[0]
    if n < 3:
        return np.zeros((n, 2), np.float32)
    X = P - P.mean(axis=0, keepdims=True)
    try:
        from sklearn.decomposition import PCA
        from sklearn.manifold import TSNE
        d = min(30, n - 1, X.shape[1])
        Xp = PCA(n_components=d, random_state=0).fit_transform(X) if d >= 2 else X
        perp = max(5.0, min(30.0, (n - 1) / 3.0))
        if n >= 10:
            return TSNE(n_components=2, perplexity=perp, init="pca",
                        random_state=0, max_iter=500).fit_transform(Xp).astype(np.float32)
        return Xp[:, :2].astype(np.float32)
    except Exception:                       # sklearn 없거나 실패 — numpy SVD 로 PCA 2d
        logger.exception("[journey] t-SNE 실패 — PCA 2d 로 대체")
        U, S_, _ = np.linalg.svd(X, full_matrices=False)
        return (U[:, :2] * S_[:2]).astype(np.float32)


def reconstruct(db_path: str | Path, cos_th: float = DEFAULT_COS_TH,
                rerank: bool = True, viz: bool = False) -> dict:
    """트랙렛 → 사람 클러스터. UI/API 가 그대로 쓰는 dict 를 돌려준다."""
    db_path = Path(db_path)
    tls, meta = _tracklets(db_path)
    if not tls:
        return {"ok": False, "reason": "임베딩이 없는 녹화(schema ≤3)이거나 트랙렛 없음",
                "tracklets": 0, "persons": []}

    n = len(tls)
    # 유사도 — 프로토타입 집합 간 최대 코사인
    S = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        Pi = tls[i]["protos"]
        for j in range(i + 1, n):
            c = float((Pi @ tls[j]["protos"].T).max())
            S[i, j] = S[j, i] = c
    np.fill_diagonal(S, 1.0)

    D = _rerank(S) if rerank and n >= 3 else (1.0 - S)
    sim = 1.0 - D                                    # 재랭킹 후 유사도

    # cannot-link 사전 계산
    forbid = np.zeros((n, n), dtype=bool)
    for i in range(n):
        for j in range(i + 1, n):
            if _cannot_link(tls[i], tls[j]):
                forbid[i, j] = forbid[j, i] = True

    # 제약 병합 클러스터링 — 유사도 높은 쌍부터, cannot-link 를 어기면 건너뜀
    parent = list(range(n))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    members = {i: {i} for i in range(n)}
    pairs = [(sim[i, j], i, j) for i in range(n) for j in range(i + 1, n)
             if not forbid[i, j] and sim[i, j] >= cos_th]
    pairs.sort(reverse=True)
    for s, i, j in pairs:
        ri, rj = find(i), find(j)
        if ri == rj:
            continue
        # 두 군집 사이에 금지 쌍이 하나라도 있으면 합치지 않는다 (complete-link 제약)
        if any(forbid[a, b] for a in members[ri] for b in members[rj]):
            continue
        parent[rj] = ri
        members[ri] |= members[rj]
        members.pop(rj, None)

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    persons = []
    for gi, (_, idxs) in enumerate(sorted(groups.items(),
                                          key=lambda kv: min(tls[i]["t0"] for i in kv[1])), 1):
        idxs.sort(key=lambda i: tls[i]["t0"])
        segs = [{"key": tls[i]["key"], "cam": tls[i]["cam"],
                 "t0": tls[i]["t0"], "t1": tls[i]["t1"], "n": tls[i]["n"]} for i in idxs]
        obs_n = sum(s["n"] for s in segs)
        persons.append({
            "person_id": f"p{gi}",
            "fragment": obs_n < FRAGMENT_OBS,
            "n_tracklets": len(idxs),
            "t0": min(s["t0"] for s in segs), "t1": max(s["t1"] for s in segs),
            "cams": sorted({s["cam"] for s in segs}),
            "obs": sum(s["n"] for s in segs),
            "segments": segs,
        })
    persons.sort(key=lambda x: (x["fragment"], -x["obs"]))
    main = [x for x in persons if not x["fragment"]]

    vizdata = None
    if viz:
        # 트랙렛 → 소속 사람 매핑 + 클러스터 순 정렬 (유사도 행렬의 블록 대각용)
        of = {}
        for pp in persons:
            for s in pp["segments"]:
                of[s["key"]] = pp["person_id"]
        order = sorted(range(n), key=lambda i: (persons_index(persons, of.get(tls[i]["key"])),
                                                tls[i]["t0"]))
        reps = np.stack([tls[i]["protos"].mean(axis=0) for i in range(n)])
        reps /= (np.linalg.norm(reps, axis=1, keepdims=True) + 1e-12)
        xy = _project2d(reps)
        vizdata = {
            "keys": [tls[i]["key"] for i in order],
            "person_of": [of.get(tls[i]["key"]) for i in order],
            "cam_of": [tls[i]["cam"] for i in order],
            "xy": [[round(float(xy[i][0]), 2), round(float(xy[i][1]), 2)] for i in order],
            # 재랭킹 후 유사도를 클러스터 순으로 재배열 — 대각 블록이 뚜렷하면 잘 묶인 것
            "sim": [[round(float(sim[i][j]), 3) for j in order] for i in order],
        }
    return {
        "ok": True, "tracklets": n, "persons": persons,
        "n_persons": len(main),                     # 파편 제외한 '사람' 수
        "n_fragments": len(persons) - len(main),
        "fragment_obs_th": FRAGMENT_OBS,
        "cos_th": cos_th, "rerank": rerank, "viz": vizdata,
        "alarm_ts": meta.get("alarm_ts"), "floor_id": meta.get("floor_id"),
    }
