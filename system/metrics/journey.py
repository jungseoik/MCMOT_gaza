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
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from system.metrics import recorder

logger = logging.getLogger("system.metrics.journey")

# 문턱은 **두 개**다. 재랭킹을 켜면 유사도 분포가 통째로 옮겨가기 때문이다 —
# 실측(AI hub 3F scenario_01): 원본 코사인 중앙 0.443 / 재랭킹 후 중앙 0.185.
# 하나의 값으로 둘 다 걸면 "0.62" 가 무엇을 뜻하는지가 재랭킹 on/off 에 따라
# 달라져 해석이 불가능해진다. 그래서 cos_th 는 **항상 원본 코사인**에 걸고,
# rerank_th 는 재랭킹 유사도에 따로 건다.
DEFAULT_COS_TH = 0.50      # 원본 코사인 하한 (재랭킹과 무관하게 의미 고정)
DEFAULT_RERANK_TH = 0.62   # 재랭킹 유사도 하한 (rerank=True 일 때만 추가로 적용)
MAX_SPEED_MPS = 3.0        # 사람 보행 상한 — 이보다 빠른 재등장은 타인
SLACK_M = 3.0              # 카메라 간 매핑 오프셋 여유 — 이 거리 안은 속도 판정 생략
MIN_OBS = 2                # 이만큼 미만 관측 트랙렛은 노이즈로 제외
# 클러스터 관측 수가 이보다 적으면 '파편'으로 분리 — 사람 수 집계에서 뺀다.
# 처음엔 50 이었다(AI hub 3F s01 단일 세션 기준: 22 클러스터 중 관측 50+ 가 14개로
# 전체 관측의 96%). 다중 세션으로 넓혀 보니 50 은 헐거웠다 — 정답 10명인 3층
# 시나리오 6개에서 50 은 15~27명, 100 은 10~17명으로 100 쪽이 일관되게 정답에 가깝다.
#   s01 18→12 · s02 27→15 · s03 20→15 · s04 18→14 · s05 15→10 · s06 18→17
# 카메라 7대 × 5fps 면 사람 하나가 한 대만 스쳐도 관측 수십 개가 나오므로, 50 은
# "잠깐 스친 오탐"과 "실제 참가자"를 가르기엔 낮다.
FRAGMENT_OBS = 100
LINK_TOL = 0.0             # 군집 간 허용 금지쌍 비율 (0 = 순수 complete-link)
# 같은 카메라 시간겹침을 '다른 사람' 으로 보기까지의 최소 겹침. 트래커가 트랙을
# 갈아끼우는 순간(인계)에는 옛 트랙의 꼬리와 새 트랙의 머리가 1~2프레임 공존한다.
# 그걸 겹침으로 세면 **한 사람이 영원히 둘로 갈린다**. 실측(AI hub 3F s01, 5fps):
# 같은 카메라 겹침 149쌍 중 ≤0.5s 가 11쌍뿐이고 나머지는 대부분 3s 초과 —
# 짧은 겹침과 진짜 동시존재는 분포가 뚜렷하게 갈린다.
OVERLAP_TOL_SEC = 0.5


@dataclass(frozen=True)
class Params:
    """재구성 파라미터 — 전부 기본값이 있고, 리플레이 UI 에서 덮어쓸 수 있다.

    실측상 **결과를 실제로 바꾸는 것**은 link_tol · max_speed_mps · fragment_obs
    쪽이다. 문턱만 0.50~0.70 으로 흔들면 사람 수는 그대로고 파편 분류만 바뀐다
    (구속하는 것이 문턱이 아니라 cannot-link + complete-link 이기 때문).
    """
    cos_th: float = DEFAULT_COS_TH
    rerank: bool = True
    rerank_th: float = DEFAULT_RERANK_TH
    max_speed_mps: float = MAX_SPEED_MPS
    slack_m: float = SLACK_M
    fragment_obs: int = FRAGMENT_OBS
    overlap_tol_sec: float = OVERLAP_TOL_SEC
    min_obs: int = MIN_OBS
    link_tol: float = LINK_TOL
    # "한 사람이 같은 출구를 두 번 나가지 않는다" — 같은 카메라 동시 존재와
    # 같은 성격의 물리 제약. 피난 훈련에서 한 번 나간 사람은 돌아오지 않는다.
    # 시간이 안 겹쳐 기존 제약으로 못 막던 오병합을 이게 잡는다.
    exit_unique: bool = True

    @classmethod
    def from_dict(cls, d: dict | None) -> "Params":
        """UI/API 의 부분 지정을 받아 기본값 위에 얹는다. 범위를 넘으면 자른다."""
        d = d or {}
        def num(k, lo, hi, cast=float):
            v = d.get(k)
            if v is None or v == "":
                return getattr(cls, k)
            try:
                return max(lo, min(hi, cast(v)))
            except Exception:
                return getattr(cls, k)
        return cls(
            cos_th=num("cos_th", 0.0, 0.99),
            rerank=bool(d.get("rerank", True)),
            rerank_th=num("rerank_th", 0.0, 0.99),
            max_speed_mps=num("max_speed_mps", 0.5, 20.0),
            slack_m=num("slack_m", 0.0, 30.0),
            fragment_obs=num("fragment_obs", 0, 10000, int),
            overlap_tol_sec=num("overlap_tol_sec", 0.0, 5.0),
            min_obs=num("min_obs", 1, 1000, int),
            link_tol=num("link_tol", 0.0, 1.0),
            exit_unique=bool(d.get("exit_unique", True)),
        )


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




def _merge_path(path: list, bin_sec: float = 0.2) -> list:
    """여러 조각의 관측을 하나의 궤적으로 합친다 — **같은 시각은 평균**.

    시야가 겹치는 카메라 두 대가 같은 순간을 동시에 보면, 단순히 이어 붙일 경우
    두 카메라의 투영 좌표를 왕복하며 지그재그가 생겨 이동거리가 몇 배로 부풀고
    속도가 사람 한계를 넘는다(실측: 조각 10개짜리에서 235m·평균 6.6m/s).
    한 사람은 한 순간에 한 곳에 있으므로 같은 시간 구간의 관측은 평균이 맞다.
    """
    if not path:
        return []
    acc = defaultdict(list)
    for ts, p in path:
        acc[round(float(ts) / bin_sec)].append(p)
    out = []
    for k in sorted(acc):
        ps = acc[k]
        out.append((k * bin_sec,
                    (sum(q[0] for q in ps) / len(ps), sum(q[1] for q in ps) / len(ps))))
    # 카메라가 붙고 떨어지는 순간 평균 좌표가 계단처럼 튄다(매핑 오차가 카메라마다
    # 달라서). 3점 이동평균으로 그 계단만 눕힌다 — 보행 궤적은 거의 그대로다.
    if len(out) >= 3:
        sm = [out[0]]
        for i in range(1, len(out) - 1):
            w = [out[i - 1][1], out[i][1], out[i + 1][1]]
            sm.append((out[i][0], (sum(q[0] for q in w) / 3.0, sum(q[1] for q in w) / 3.0)))
        sm.append(out[-1])
        out = sm
    return out



SPLIT_TH = 0.30            # 트랙렛 내부 교차 유사도가 이보다 낮으면 뒤바뀜으로 보고 쪼갠다.
                           # 0.75 로 잡으면 시점 변화(앉음→뒷모습)까지 잘라 과분할된다.


def refine_features(db_path: Path, split_th: float = SPLIT_TH) -> tuple[dict, dict]:
    """저장된 crop 을 ReID 로 다시 임베딩 → (임베딩 override, 분할점).

    녹화 임베딩은 트래커의 EMA(α=0.9)라 트랙 **안쪽**에서 박스가 다른 사람에게
    옮겨가도 서서히 섞여 안 보인다(실측 내부 교차 유사도 EMA 중앙 0.977 vs
    crop 재임베딩 0.632). crop 은 프레임별 원본이라 그대로 드러난다.

    분할과 특징 교체는 **같이** 가야 한다 — 쪼개기만 하고 EMA 를 그대로 쓰면
    나뉜 조각이 다시 같은 사람으로 묶인다(실측 cam09:15 두 조각이 눈으로
    명백히 다른 사람인데 둘 다 p5).

    GPU 를 쓴다. 엔진이 없거나 썸네일이 없는 녹화(schema ≤4)면 ({}, {}).
    """
    import cv2
    import torch
    import model_zoo

    thumbs = recorder.load_track_thumbs(db_path)
    if not thumbs:
        return {}, {}
    keys, crops, spans = [], [], []
    for (cam, lid), lst in thumbs.items():
        ims = [(ts, cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR))
               for ts, b in lst]
        ims = [(ts, im) for ts, im in ims if im is not None]
        if len(ims) < 2:
            continue
        keys.append((cam, lid)); spans.append([ts for ts, _ in ims])
        crops.append([im for _, im in ims])
    if not keys:
        return {}, {}

    prof = model_zoo.resolve(None)
    reid, crop = model_zoo.build_reid(prof)
    W, H = crop
    batch = [cv2.cvtColor(cv2.resize(im, (W, H), interpolation=cv2.INTER_LINEAR),
                          cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
             for c in crops for im in c]
    x = torch.from_numpy(np.stack(batch)).float().cuda()
    outs = []
    with torch.no_grad():
        for i in range(0, len(x), 128):
            outs.append(reid(x[i:i + 128]).float().cpu().numpy())
    V = np.concatenate(outs)
    V = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)

    ovr, splits, off = {}, {}, 0
    for (cam, lid), c, sp in zip(keys, crops, spans):
        P = V[off:off + len(c)]; off += len(c)
        ovr[(cam, lid)] = [(ts, P[i]) for i, ts in enumerate(sp)]
        if len(P) < 4:
            continue
        S = P @ P.T
        best = (1.0, -1)
        for k in range(1, len(P)):                 # 앞/뒤가 가장 안 닮는 지점
            cross = float(S[:k, k:].mean())
            if cross < best[0]:
                best = (cross, k)
        if best[0] < split_th and 0 < best[1] < len(sp):
            splits[f"{cam}:{lid}"] = [(sp[best[1] - 1] + sp[best[1]]) / 2.0]
    logger.info("refine: 트랙렛 %d · crop %d · 분할 %d곳", len(ovr), len(V), len(splits))
    return ovr, splits


def _run_gates(db_path: Path, meta: dict) -> dict:
    """엔진과 같은 판정기로 출구 게이트를 재생 → {(트랙렛키, exit_id): 최초 out ts}.

    분기도 engine._rebuild_exits 와 동일: count_cam+cam_zone → ZoneGate(화면
    영역+체류), count_cam+cam_line → DirectionalLine(화면), 그 외 → 맵 통과선.
    게이트는 **트랙렛 단위**로 센다(엔진과 같은 키).
    """
    from system.spatial.geometry import DirectionalLine, ZoneGate

    exits = (meta.get("site_view") or {}).get("exits") or []
    gates = []
    for e in exits:
        cc, z = e.get("count_cam"), e.get("cam_zone")
        if cc and z and len(z) >= 3:
            gates.append({"id": e.get("id"), "cam": cc, "map": False,
                          "g": ZoneGate(z, dwell=int(e.get("cam_zone_dwell") or 2))})
        elif cc and e.get("cam_line") and e.get("cam_inside"):
            gates.append({"id": e.get("id"), "cam": cc, "map": False,
                          "g": DirectionalLine(e["cam_line"], e["cam_inside"], margin_px=3.0)})
        elif e.get("line") and e.get("inside"):
            gates.append({"id": e.get("id"), "cam": None, "map": True,
                          "g": DirectionalLine(e["line"], e["inside"], margin_px=6.0)})
    if not gates:
        return {}
    H = {}
    for c in meta.get("cameras", []):
        m = c.get("mapping")
        if m and m.get("H"):
            H[c["cam_id"]] = np.asarray(m["H"], dtype=np.float64).reshape(3, 3)
    con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT ts, cam_id, local_id, u, v, x1, y1, x2, y2 FROM tracks"
            " ORDER BY call_seq, rowid").fetchall()
    finally:
        con.close()
    out: dict = {}
    for ts, cam, lid, u, v, x1, y1, x2, y2 in rows:
        okey = f"{cam}:{int(lid)}"
        bbox = (x1, y1, x2, y2) if x1 is not None else None
        for gt in gates:
            if gt["map"]:
                h = H.get(cam)
                if h is None:
                    continue
                q = h @ np.array([float(u), float(v), 1.0])
                if abs(q[2]) < 1e-9:
                    continue
                pt, bb = (float(q[0] / q[2]), float(q[1] / q[2])), None
            else:
                if cam != gt["cam"]:
                    continue
                pt, bb = (float(u), float(v)), bbox
            ev = (gt["g"].observe(okey, pt, bb) if isinstance(gt["g"], ZoneGate)
                  else gt["g"].observe(okey, pt))
            if ev == "out":
                out.setdefault((okey, gt["id"]), float(ts))
    return out


def _exit_events(db_path: Path, meta: dict) -> dict:
    """트랙렛 키 → [(exit_id, ts), ...]. 클러스터링 **전에** 필요하다.

    "한 사람이 같은 출구를 두 번 나가지는 않는다" 를 병합 제약으로 쓰기 위한
    것. 같은 카메라 동시 존재와 같은 성격의 물리 제약이다 — 피난 훈련에서
    한 번 나간 사람은 돌아오지 않는다.
    """
    ev = _run_gates(db_path, meta)
    out: dict = {}
    for (okey, eid), ts in ev.items():
        out.setdefault(okey, []).append((eid, ts))
    return out


def _exit_times(db_path: Path, persons: list, meta: dict) -> None:
    """사람별 출구 통과 시각(경보 기준 초)을 persons 에 채운다.

    **엔진과 같은 판정기를 그대로 쓴다** — 자체 기준을 만들면 리포트의 통과
    시각과 세션의 통과 인원이 다른 규칙으로 나와 서로 대조가 안 된다.
    분기도 engine._rebuild_exits 와 동일하게 맞춘다:
      ① count_cam + cam_zone  → ZoneGate (화면 영역 + 체류). 문이 프레임
         가장자리면 사람이 선 반대편에 안 나타나 선으로는 못 센다.
      ② count_cam + cam_line  → DirectionalLine (화면 px, 데드밴드 절반)
      ③ 그 외                  → DirectionalLine (맵 px)
    ①·②는 그 카메라의 관측만, ③은 맵 투영 좌표를 먹인다.
    """
    ev = _run_gates(db_path, meta)
    alarm = meta.get("alarm_ts")
    rows_t0 = None
    out = ev
    owner = {}
    for pp in persons:
        for s in pp["segments"]:
            cam, lid = s["key"].split("#", 1)[0].rsplit(":", 1)
            owner.setdefault((cam, int(lid)), pp["person_id"])

    base = alarm if alarm is not None else (min(out.values()) if out else 0.0)
    # 트랙렛 사건 → 사람 귀속. 한 사람이 여러 번 세지면 **과병합 신호**라
    # 횟수를 그대로 남긴다(가장 이른 시각 + 횟수).
    first: dict = {}
    cnt: dict = {}
    unowned = 0
    for (okey, eid), ts in sorted(out.items(), key=lambda kv: kv[1]):
        cam, lid = okey.rsplit(":", 1)
        pid = owner.get((cam, int(lid)))
        if pid is None:
            unowned += 1
            continue
        k = (pid, eid)
        first.setdefault(k, ts)
        cnt[k] = cnt.get(k, 0) + 1
    for pp in persons:
        pid = pp["person_id"]
        pp["exit_at"] = {eid: round(ts - base, 1) for (q, eid), ts in first.items() if q == pid}
        pp["exit_count"] = sum(v for (q, _), v in cnt.items() if q == pid)
    return {"events": len(out), "unowned": unowned,
            "persons_with_exit": len({q for q, _ in first})}

def _kinematics(path: list, mps_cap: float = 12.0) -> dict:
    """궤적(초, (x,y) m) → 이동거리·속도·가속도.

    순간값은 검출 흔들림에 매우 민감해 그대로 쓰면 최고속도가 사람 한계를
    넘는다. 1초 창으로 묶어 계산하고, 물리적으로 불가능한 구간(mps_cap)은
    센서 오차로 보고 버린다.
    """
    if len(path) < 2:
        return {"dist_m": 0.0, "speed_avg": None, "speed_p95": None, "accel_p95": None}
    dist = 0.0
    for i in range(1, len(path)):
        dist += math.dist(path[i - 1][1], path[i][1])
    # 1초 창 속도열
    vs = []
    j = 0
    for i in range(len(path)):
        while path[i][0] - path[j][0] > 1.0:
            j += 1
        dt = path[i][0] - path[j][0]
        if dt >= 0.5:
            v = math.dist(path[j][1], path[i][1]) / dt
            if v <= mps_cap:
                vs.append((path[i][0], v))
    if not vs:
        span = path[-1][0] - path[0][0]
        return {"dist_m": round(dist, 2),
                "speed_avg": round(dist / span, 2) if span > 0 else None,
                "speed_p95": None, "accel_p95": None}
    acc = []
    for i in range(1, len(vs)):
        dt = vs[i][0] - vs[i - 1][0]
        if dt > 0.2:
            acc.append(abs(vs[i][1] - vs[i - 1][1]) / dt)
    span = path[-1][0] - path[0][0]
    # 최댓값은 5fps 검출의 단발 이상치를 그대로 집는다(실측 10.5m/s). 분포의
    # 상위 5% 분위를 쓰면 "빠르게 움직인 구간" 이라는 의미는 지키면서 한 프레임
    # 튐에는 흔들리지 않는다.
    def p95(a):
        if not a:
            return None
        b = sorted(a)
        return round(b[min(len(b) - 1, int(round(0.95 * (len(b) - 1))))], 2)
    return {
        "dist_m": round(dist, 2),
        "speed_avg": round(dist / span, 2) if span > 0 else None,
        "speed_p95": p95([v for _, v in vs]),
        "accel_p95": p95(acc),
    }


def _roi_of(cam: dict):
    """카메라의 유효영역 polygon (맵 투영과 같은 규칙, spatial/projector 와 동일).

    valid_roi 가 있으면 그것, 없으면 대응점(cctv_pts)의 컨벡스 헐 — 즉 보간
    범위. 둘 다 없으면 None(거르지 않음).
    """
    if cam.get("valid_roi"):
        return np.asarray(cam["valid_roi"], dtype=np.float32).reshape(-1, 2)
    pts = ((cam.get("mapping") or {}).get("cctv_pts")) or None
    if not pts or len(pts) < 3:
        return None
    import cv2
    return cv2.convexHull(
        np.asarray(pts, dtype=np.float32).reshape(-1, 1, 2)).reshape(-1, 2)


def _in_roi(roi, u: float, v: float) -> bool:
    import cv2
    return cv2.pointPolygonTest(roi.reshape(-1, 1, 2).astype(np.float32),
                                (float(u), float(v)), False) >= 0


def _tracklets(db_path: Path, min_obs: int = MIN_OBS,
               splits: "dict[str, list[float]] | None" = None,
               embs_override: "dict | None" = None) -> tuple[list[dict], dict]:
    """트랙렛 집계 — (cam_id, local_id) 별 시각·맵위치(m)·임베딩.

    splits: {"cam:local": [끊을 시각, ...]} — 트랙렛 **안쪽**에서 박스가 다른
    사람에게 옮겨간 지점. 그 시각을 경계로 하위 트랙렛(key 에 #1, #2 …)으로
    나눈다. 녹화 임베딩은 트래커의 EMA(α=0.9)라 뒤바뀜이 뭉개져 여기서는
    찾을 수 없다 — 분할점은 저장된 crop 을 ReID 로 다시 임베딩해서 구한다
    (tools/tracklet_purity.py).

    embs_override: {(cam, local): [(ts, vec), ...]} — 녹화 EMA 대신 쓸 임베딩.
    EMA 는 트랙 내부의 뒤바뀜을 뭉개므로, 쪼개기만 하고 특징을 그대로 두면
    나뉜 조각이 **다시 같은 사람으로 묶인다**(실측: cam09:15 두 조각이 눈으로
    명백히 다른 사람인데 둘 다 p5 로). 분할과 특징 교체는 같이 가야 한다.
    """
    meta = recorder.load_meta(db_path)
    mpp = float(((meta.get("site_view") or {}).get("map") or {}).get("m_per_px") or 0) or None
    H, ROI = {}, {}
    for c in meta.get("cameras", []):
        m = c.get("mapping")
        if m and m.get("H"):
            H[c["cam_id"]] = np.asarray(m["H"], dtype=np.float64).reshape(3, 3)
        r = _roi_of(c)
        if r is not None:
            ROI[c["cam_id"]] = r
    embs = embs_override if embs_override is not None else recorder.load_track_embs(db_path)

    con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT cam_id, local_id, ts, u, v FROM tracks ORDER BY cam_id, local_id, ts"
        ).fetchall()
    finally:
        con.close()

    # 헐(valid_roi)은 **위치**에만 적용한다 (v1.16).
    #   · 시간 범위·관측 수 — 헐 밖 포함. 존재 자체는 헐과 무관하다.
    #   · 맵 좌표(궤적·p0/p1) — 헐 안만. 헐 밖은 호모그래피 외삽이라 부정확하고,
    #     그 좌표로 운동학 cannot-link 를 판정하면 근거 없는 판정이 된다.
    # 이전엔 관측 자체를 헐로 걸렀는데, 출구가 헐 밖이라 문 앞 증거가 통째로
    # 사라졌다(실측 cam09:1 관측 10개 중 헐 안 1개 → 출구 통과 미귀속).
    agg = defaultdict(list)
    for cam, lid, ts, u, v in rows:
        r = ROI.get(cam)
        ok = (r is None) or _in_roi(r, u, v)
        agg[(cam, int(lid))].append((float(ts), float(u), float(v), ok))

    out = []
    for (cam, lid), obs in agg.items():
        if len(obs) < min_obs:
            continue
        E = embs.get((cam, lid)) or []
        if not E:
            continue                                 # 임베딩 없으면 묶을 근거가 없다
        base = f"{cam}:{lid}"
        cuts = sorted((splits or {}).get(base) or [])
        for part, (lo, hi) in enumerate(_ranges(cuts), 1):
            po = [o for o in obs if lo <= o[0] < hi]
            pe = [e for e in E if lo <= e[0] < hi]
            if len(po) < min_obs or not pe:
                continue
            _emit(out, base if not cuts else f"{base}#{part}", cam, lid, po,
                  np.stack([v for _, v in pe]), h=H.get(cam), mpp=mpp)
    out.sort(key=lambda d: (d["t0"], d["cam"]))
    return out, meta


def _ranges(cuts: list) -> list:
    """분할 시각 목록 → [(시작, 끝), ...] 반열린 구간. 빈 목록이면 전체 1개."""
    edges = [-math.inf, *cuts, math.inf]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def _emit(out: list, key: str, cam: str, lid: int, obs: list, P, h, mpp) -> None:
    """트랙렛 1개(또는 그 하위 조각)를 결과 목록에 담는다."""
    def xy(u, v):
        if h is None or mpp is None:
            return None
        p = h @ np.array([u, v, 1.0])
        if abs(p[2]) < 1e-9:
            return None
        return (float(p[0] / p[2]) * mpp, float(p[1] / p[2]) * mpp)
    # 궤적(맵 m) — 사람 단위 이동거리·속도·가속도 산출용. 투영 실패는 버린다.
    inr = [o for o in obs if o[3]]            # 헐 안 관측만 위치로 쓴다
    path = [(ts, q) for ts, u, v, _ in inr if (q := xy(u, v)) is not None]
    out.append({
        "key": key, "cam": cam, "local_id": lid,
        "t0": obs[0][0], "t1": obs[-1][0], "n": len(obs),
        "n_in_roi": len(inr),
        "p0": xy(inr[0][1], inr[0][2]) if inr else None,
        "p1": xy(inr[-1][1], inr[-1][2]) if inr else None,
        "path": path,
        "protos": P,
    })


def _cannot_link(a: dict, b: dict, pr: "Params | None" = None) -> bool:
    """물리적으로 같은 사람일 수 없는 쌍인가."""
    pr = pr or Params()
    # ① 같은 카메라에서 시간이 겹친다 → 한 카메라에 같은 사람이 둘일 수 없다.
    #    단 트랙 인계(옛 트랙 꼬리 + 새 트랙 머리)의 1~2프레임 공존은 제외한다.
    overlap = min(a["t1"], b["t1"]) - max(a["t0"], b["t0"])
    if a["cam"] == b["cam"] and overlap > pr.overlap_tol_sec:
        return True
    # ② 시간차 대비 이동거리가 불가능하다 (겹치면 판정 생략 — 시야 겹침 핸드오버)
    if overlap <= 0 and a["p1"] and b["p0"]:
        first, second = (a, b) if a["t1"] <= b["t0"] else (b, a)
        dt = second["t0"] - first["t1"]
        if dt > 0:
            d = math.dist(first["p1"], second["p0"])
            if d > pr.slack_m and d / dt > pr.max_speed_mps:
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


def reconstruct(db_path: str | Path, params: "Params | dict | None" = None,
                viz: bool = False, splits: "dict | None" = None,
                embs_override: "dict | None" = None, refine: bool = False,
                **kw) -> dict:
    """트랙렛 → 사람 클러스터. UI/API 가 그대로 쓰는 dict 를 돌려준다.

    params 로 한 번에 주거나 키워드로 낱개 지정(cos_th=…, rerank=…)해도 된다.
    """
    pr = params if isinstance(params, Params) else Params.from_dict(
        {**(params or {}), **{k: v for k, v in kw.items() if v is not None}})
    db_path = Path(db_path)
    refined = False
    if refine and embs_override is None:
        try:
            embs_override, auto = refine_features(db_path)
            if embs_override:
                refined = True
                if splits is None:
                    splits = auto
        except Exception:
            logger.exception("정밀 재구성(crop 재임베딩) 실패 — 녹화 EMA 로 진행")
            embs_override = None
    tls, meta = _tracklets(db_path, min_obs=pr.min_obs, splits=splits,
                           embs_override=embs_override)
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

    use_rr = pr.rerank and n >= 3
    sim = (1.0 - _rerank(S)) if use_rr else S        # 병합 순서용 유사도
    # 문턱은 두 스케일에 각각 — cos_th 는 언제나 원본 코사인의 의미를 유지한다
    ok_pair = S >= pr.cos_th
    if use_rr:
        ok_pair &= sim >= pr.rerank_th

    # 트랙렛별 출구 통과 — 같은 출구를 이미 나간 둘은 같은 사람일 수 없다
    ex_of: list = [set() for _ in range(n)]
    if pr.exit_unique:
        try:
            evs = _exit_events(db_path, meta)
            base_of = {}
            for i, tl in enumerate(tls):
                base_of.setdefault(tl["key"].split("#", 1)[0], []).append(i)
            for okey, lst in evs.items():
                for i in base_of.get(okey, []):
                    ex_of[i] |= {eid for eid, _ in lst}
        except Exception:
            logger.exception("출구 제약 준비 실패 — 제약 없이 진행")

    # cannot-link 사전 계산
    forbid = np.zeros((n, n), dtype=bool)
    for i in range(n):
        for j in range(i + 1, n):
            if _cannot_link(tls[i], tls[j], pr) or (ex_of[i] & ex_of[j]):
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
             if not forbid[i, j] and ok_pair[i, j]]
    pairs.sort(reverse=True)
    for s, i, j in pairs:
        ri, rj = find(i), find(j)
        if ri == rj:
            continue
        # 두 군집 사이 금지 쌍 비율이 link_tol 을 넘으면 합치지 않는다.
        # tol=0 이 순수 complete-link — 한 쌍만 금지여도 거부한다. 군집이 커질수록
        # 이 규칙이 실질 구속이 된다(제약을 다 풀면 14명 → 7명으로 과병합).
        bad = sum(1 for a in members[ri] for b in members[rj] if forbid[a, b])
        if bad and bad > pr.link_tol * len(members[ri]) * len(members[rj]):
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
        # 사람 단위 궤적 — 조각을 시간순으로 이어 붙인다. 조각 사이 공백은 잇지
        # 않는다(안 보이던 구간을 직선으로 메우면 이동거리가 부풀려진다).
        path = []
        for i in idxs:
            path.extend(tls[i].get("path") or [])
        path = _merge_path(path)
        kin = _kinematics(path)
        persons.append({
            "person_id": f"p{gi}",
            "fragment": obs_n < pr.fragment_obs,
            "n_tracklets": len(idxs),
            "t0": min(s["t0"] for s in segs), "t1": max(s["t1"] for s in segs),
            "cams": sorted({s["cam"] for s in segs}),
            "obs": sum(s["n"] for s in segs),
            "segments": segs,
            **kin,
            "exit_at": {},          # 아래에서 엔진과 같은 판정기로 채운다
        })
    # 출구 통과 — 엔진과 같은 판정기로 시간순 1패스 (사람 단위 키)
    exinfo = None
    try:
        exinfo = _exit_times(db_path, persons, meta)
    except Exception:
        logger.exception("출구 통과 시각 산출 실패 — 나머지 지표는 그대로")
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
        "fragment_obs_th": pr.fragment_obs,
        # 출구 게이트 집계 — 게이트는 **트랙렛 단위**로 센다. 사람 수와 다를 수
        # 있고, 그 차이 자체가 품질 신호다(한 사람이 여러 번 = 과병합 의심,
        # 미귀속 = 재구성에서 빠진 조각이 문을 통과).
        "exit_summary": exinfo,
        "refined": refined,      # crop 재임베딩을 썼는가
        "params": asdict(pr),                       # 무엇으로 계산했는지 그대로 반환
        "defaults": asdict(Params()),               # UI 가 "기본값으로" 를 그릴 수 있게
        "cos_th": pr.cos_th, "rerank": pr.rerank, "viz": vizdata,
        "alarm_ts": meta.get("alarm_ts"), "floor_id": meta.get("floor_id"),
    }
