#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""엔진 판정 격자(t_alarm + k·1s) 위의 원자료를 정확히 뽑아 idr_sweep.json 갱신.

리플레이 프레임에서 되짚으면 샘플 격자가 최대 1초 어긋난다. 여기서는
EvaluationSession._sample 을 감싸 **엔진이 실제로 판정한 그 순간**의
구역 내 (speed, align) 을 그대로 받아 적는다 — 그림의 시계열과 판정 수치가
같은 표본 위에 놓인다.
"""
import json, os, sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from system.metrics import session as S                        # noqa: E402
from system.metrics.replay import run_replay                   # noqa: E402
from system.spatial import point_in_polygon                    # noqa: E402
import collect as C                                            # noqa: E402

_orig = S.EvaluationSession._sample
CAP: list = []


def _patched(self, t):
    for zacc in self.zones:
        mem = [(s, a) for x, y, s, a in self._obj_rows()
               if point_in_polygon((x, y), zacc.zone.polygon)]
        CAP.append({"t": round(t - self.alarm_ts, 2), "z": zacc.zone.id, "m": mem})
        break                                   # 첫 구역만 (보고서 대상)
    return _orig(self, t)


def main():
    S.EvaluationSession._sample = _patched
    p = Path(__file__).parent / "idr_sweep.json"
    D = json.load(open(p))
    for num, (db, fl, lab) in sorted(C.sessions().items()):
        CAP.clear()
        run_replay(db, {"thresholds": D["base"]}, fps=1.0)
        D["rows"][num]["series"] = [dict(x) for x in CAP]
        print(f"S{num} {len(CAP)}샘플", flush=True)
    json.dump(D, open(p, "w"), ensure_ascii=False)
    print(f"→ {p.name} 갱신 ({p.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
