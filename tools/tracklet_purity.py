#!/usr/bin/env python3
"""트랙렛 순도 검사 — 한 트랙렛 안에서 사람이 바뀌었는지 저장된 crop 으로 확인한다.

    python tools/tracklet_purity.py --db data/sites/default/sessions/floor4/<sess>.db
    python tools/tracklet_purity.py --db <db> --viz            # 의심 트랙렛 몽타주 저장

왜 필요한가
-----------
녹화에 남는 임베딩은 트래커의 **EMA(α=0.9) 외형 특징**이다. 트랙 도중 박스가
다른 사람에게 옮겨가도 EMA 는 서서히 섞이므로, 그 벡터만 보면 내부 유사도가
0.98 로 높게 나와 뒤바뀜이 안 보인다(실측: 68개 트랙렛 중 <0.75 가 1개뿐).

반면 썸네일은 **프레임별 원본 crop** 이다. 그걸 ReID 모델로 다시 임베딩하면
트랙렛 안쪽의 뒤바뀜이 그대로 드러난다. 이 도구는 그 차이를 재고, 어디서
끊어야 하는지(분할점)를 찾는다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import torch                                        # noqa: E402
import model_zoo                                    # noqa: E402
from system.metrics import journey as J             # noqa: E402
from system.metrics import recorder as R            # noqa: E402


def embed(crops: list, prof) -> np.ndarray:
    """crop(BGR) 목록 → 정규화 임베딩 (N, dim). ReID 엔진을 직접 돌린다."""
    reid, crop = model_zoo.build_reid(prof)      # (모듈, (W,H)) — 0~255 RGB 배치 계약
    W, H = crop
    batch = []
    for im in crops:
        r = cv2.resize(im, (W, H), interpolation=cv2.INTER_LINEAR)
        batch.append(cv2.cvtColor(r, cv2.COLOR_BGR2RGB).transpose(2, 0, 1))
    x = torch.from_numpy(np.stack(batch)).float().cuda()
    out = []
    with torch.no_grad():
        for i in range(0, len(x), 128):
            out.append(reid(x[i:i + 128]).float().cpu().numpy())
    v = np.concatenate(out)
    return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-12)


def best_split(S: np.ndarray) -> tuple[float, int]:
    """시간순 임베딩 유사도 S 에서 '앞/뒤' 가 가장 안 닮는 분할점.

    반환 (교차 유사도, 분할 인덱스). 값이 낮을수록 그 지점에서 사람이 바뀐 것.
    """
    k = len(S)
    best = (1.0, -1)
    for c in range(1, k):
        cross = float(S[:c, c:].mean())
        if cross < best[0]:
            best = (cross, c)
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--th", type=float, default=0.75,
                    help="교차 유사도가 이보다 낮으면 '뒤바뀜 의심' (기본 0.75)")
    ap.add_argument("--viz", action="store_true", help="의심 트랙렛 몽타주 저장")
    ap.add_argument("--out", default="results/tracklet_purity")
    ap.add_argument("--apply", action="store_true",
                    help="의심 지점에서 트랙렛을 쪼개 재구성을 A/B 로 비교")
    a = ap.parse_args()

    db = Path(a.db)
    meta = R.load_meta(db)
    thumbs = R.load_track_thumbs(db)
    if not thumbs:
        print("썸네일 없음 — schema ≤4 녹화다. v1.15 이후로 다시 녹화해야 한다.")
        return 2
    tls, _ = J._tracklets(db)
    ema = {t["key"]: t["protos"] for t in tls}

    prof = model_zoo.resolve(None)
    print(f"[purity] {db.name} · 트랙렛 {len(tls)}개 · ReID {prof.reid.kind} ({prof.reid.engine})")

    # 재임베딩은 **모든** 트랙렛에. 분할 판정만 4장 이상으로 제한한다 —
    # 여기서 거르면 그 트랙렛은 override 에 없어 임베딩이 아예 사라지고,
    # 클러스터링에서 통째로 빠진다(실측: 출구 귀속 6명 → 3명).
    keys, crops, spans = [], [], []
    for (cam, lid), lst in thumbs.items():
        k = f"{cam}:{lid}"
        if k not in ema or len(lst) < 2:
            continue
        ims = [cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR) for _, b in lst]
        ims = [(ts, im) for (ts, _), im in zip(lst, ims) if im is not None]
        if len(ims) < 2:
            continue
        keys.append(k); spans.append([ts for ts, _ in ims])
        crops.append([im for _, im in ims])

    flat = [im for c in crops for im in c]
    print(f"[purity] crop {len(flat)}장 재임베딩 …")
    V = embed(flat, prof)
    off, rows = 0, []
    for k, c, sp in zip(keys, crops, spans):
        P = V[off:off + len(c)]; off += len(c)
        if len(P) < 4:                     # 분할 판정은 4장 이상만 (재임베딩은 이미 됨)
            continue
        S = P @ P.T
        cross, cut = best_split(S)
        e = ema[k]
        ema_cross = best_split(e @ e.T)[0] if len(e) >= 4 else None
        rows.append({"key": k, "n": len(c), "cross": cross, "cut": cut,
                     "ema_cross": ema_cross, "ts": sp})
    rows.sort(key=lambda r: r["cross"])

    print(f"\n{'트랙렛':>12} {'장수':>4} {'재임베딩 교차':>13} {'EMA 교차':>10} {'분할점':>6}")
    for r in rows[:16]:
        em = "—" if r["ema_cross"] is None else f"{r['ema_cross']:.3f}"
        flag = "  ← 뒤바뀜 의심" if r["cross"] < a.th else ""
        print(f"{r['key'].replace('rh_',''):>12} {r['n']:4d} {r['cross']:13.3f} {em:>10} {r['cut']:6d}{flag}")

    sus = [r for r in rows if r["cross"] < a.th]
    cs = np.array([r["cross"] for r in rows])
    es = np.array([r["ema_cross"] for r in rows if r["ema_cross"] is not None])
    print(f"\n재임베딩 교차: 중앙 {np.median(cs):.3f} · 최소 {cs.min():.3f} · <{a.th} 가 {len(sus)}개")
    if len(es):
        print(f"EMA      교차: 중앙 {np.median(es):.3f} · 최소 {es.min():.3f}"
              f"  ← EMA 는 뒤바뀜을 뭉갠다")

    if a.apply:
        # 분할 시각 = 경계 두 프레임의 중간. 그 시각부터 새 조각.
        splits = {}
        for r in sus:
            c = r["cut"]
            if 0 < c < len(r["ts"]):
                splits[r["key"]] = [(r["ts"][c - 1] + r["ts"][c]) / 2.0]
        # 재임베딩 특징을 클러스터링에도 먹인다. **분할만 해서는 소용없다** —
        # 녹화 EMA 는 뒤바뀜을 뭉개서 나뉜 조각이 다시 같은 사람으로 묶인다
        # (실측: cam09:15 두 조각이 눈으로 다른 사람인데 둘 다 p5).
        ovr = {}
        off2 = 0
        for k, c, sp in zip(keys, crops, spans):
            cam, lid = k.rsplit(":", 1)
            ovr[(cam, int(lid))] = [(ts, V[off2 + i]) for i, ts in enumerate(sp)]
            off2 += len(c)
        runs = [("① 현재 (EMA · 분할 없음)", {}),
                ("② 재임베딩만", {"embs_override": ovr}),
                (f"③ 재임베딩 + 분할 {len(splits)}곳",
                 {"embs_override": ovr, "splits": splits})]
        print(f"\n[A/B] {'구성':<26}{'트랙렛':>6}{'사람':>5}{'파편':>5}{'출구귀속':>8}{'최다통과':>8}")
        for lab, kw in runs:
            x = J.reconstruct(db, viz=False, **kw)
            ex = x.get("exit_summary") or {}
            mx = max((p.get("exit_count", 0) for p in x["persons"]), default=0)
            print(f"      {lab:<26}{x['tracklets']:6d}{x['n_persons']:5d}{x['n_fragments']:5d}"
                  f"{str(ex.get('persons_with_exit', '—')):>8}{mx:8d}")
        # 쪼갠 조각이 실제로 서로 다른 사람에게 갔는지 — 이게 분할의 성패다
        fin = J.reconstruct(db, viz=False, embs_override=ovr, splits=splits)
        who = {s["key"]: p["person_id"] for p in fin["persons"] for s in p["segments"]}
        print("\n      분할된 조각의 소속:")
        for k in sorted(splits):
            parts = {kk.replace("rh_", ""): v for kk, v in who.items() if kk.startswith(k + "#")}
            tag = "→ 서로 다른 사람" if len(set(parts.values())) > 1 else "→ 같은 사람 (분할 효과 없음)"
            print(f"        {k.replace('rh_',''):>12} {parts} {tag}")

    if a.viz and sus:
        outd = ROOT / a.out; outd.mkdir(parents=True, exist_ok=True)
        idx = {k: i for i, k in enumerate(keys)}
        for r in sus[:12]:
            c = crops[idx[r["key"]]]
            im = np.hstack([cv2.resize(x, (64, 128)) for x in c])
            cv2.line(im, (r["cut"] * 64, 0), (r["cut"] * 64, 128), (0, 0, 255), 2)
            cv2.putText(im, f"{r['key']} cross={r['cross']:.2f}", (3, 14),
                        cv2.FONT_HERSHEY_SIMPLEX, .4, (0, 255, 255), 1)
            cv2.imwrite(str(outd / f"{r['key'].replace(':', '_')}.png"), im)
        print(f"[purity] 몽타주 {min(len(sus),12)}장 → {outd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
