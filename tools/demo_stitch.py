# -*- coding: utf-8 -*-
"""**표출용 고정 ID** — 끊긴 트랙 조각을 사람 단위로 묶고, 그 시계열에서 보간한다.

왜 필요한가. 17F RTSP 는 15초 클립을 무한 루프해서 **등장 인물이 고정**이다
(cam01 3명 · cam02 6명 · cam03 5명). 그런데 트래커는 한 사람이 가려지거나 검출이
한 박자 빠지면 새 gid 를 발급한다 — 화면에서는 점이 깜빡이고, 자취가 끊기고,
잠깐 제자리에 멈춘 점이 남는다. 보는 사람에게는 "추적이 끊긴다" 로 읽힌다.

구조. gid 를 **사람 자리(pid)** 에 꽂아 pid 기준 시계열을 쌓고, 그릴 때는 그
시계열에서 Catmull-Rom 으로 뽑는다. gid 가 바뀌어도 시계열은 이어지므로
이어붙이기·부드러움·자취 유지가 한 번에 풀린다.

  · 새 gid → 방금 잃은 자리의 **예측 위치** 안이면 그 자리에 잇는다
  · 짧은 공백은 시계열이 비어 있을 뿐이라 보간이 알아서 메운다
  · 공백이 `max_gap_s` 를 넘으면 **잇지 않는다** — 루프 되감기(15초마다 전원
    순간이동)를 한 사람으로 이으면 맵을 가로지르는 가짜 선이 생긴다
  · 오래 제자리인 조각은 사람으로 치지 않는다(정지 오검출)

**지표는 건드리지 않는다.** 4대 지표는 서버가 원 관측으로 낸 값 그대로다 —
그림과 수치가 어긋나면 그게 더 나쁜 거짓말이라, 이어붙인 건 그림에만 쓴다.
demo 영상의 `tools/demo_tracks.py`(이상적 궤적)와 같은 성격이고, 이쪽은 라이브라
미래를 못 보므로 **지나간 것만 보고** 잇는다.
"""
from __future__ import annotations

import math
from collections import deque


class Tracks:
    """gid 조각 → 사람 자리(pid) 시계열.

    gate_m     — 예측 위치에서 이만큼 안이면 같은 사람으로 본다(m)
    join_gap_s — 이 시간 안에 다시 나타나야 잇는다(초)
    max_gap_s  — 시계열에 이보다 큰 구멍이 있으면 그 사이는 안 그린다(초)
    still_m/still_s — 이 시간 동안 이만큼도 못 움직이면 정지 오검출로 본다
    """

    def __init__(self, m_per_px: float, gate_m=2.2, join_gap_s=2.5,
                 max_gap_s=2.0, still_m=0.35, still_s=3.5, keep_s=20.0,
                 cast: dict | None = None):
        mpp = m_per_px or 1.0
        self.gate = gate_m / mpp
        self.still_px = still_m / mpp
        self.join_gap_s, self.max_gap_s = join_gap_s, max_gap_s
        self.still_s, self.keep_s = still_s, keep_s
        self.ser: dict[str, deque] = {}        # pid -> [(t,x,y)]
        self.meta: dict[str, dict] = {}        # pid -> {cam, gid, t, vx, vy}
        self.cast = cast or {}                 # 카메라별 등장 인원 (알면 상한이 된다)
        self.n_join = self.n_merge = self.n_new = 0
        self._n = 0

    def _pred(self, m, t):
        dt = t - m["t"]
        return m["x"] + m["vx"] * dt, m["y"] + m["vy"] * dt

    def push(self, t: float, objs: list[dict]) -> None:
        """스냅샷 하나 — `/api/map/state` 의 objects."""
        live = {o["gid"] for o in objs}
        bound = {}
        for pid, m in self.meta.items():           # 이미 물려 있는 자리
            if m["gid"] in live:
                bound[m["gid"]] = pid
        for o in objs:
            pid = bound.get(o["gid"])
            if pid is None:                        # 짝 없는 gid — 빈 자리를 찾는다
                best, bd = None, None
                for q, m in self.meta.items():
                    if m["cam"] != o["cam_id"] or m["gid"] in live:
                        continue
                    if t - m["t"] > self.join_gap_s:
                        continue
                    px, py = self._pred(m, t)
                    d = math.hypot(o["x"] - px, o["y"] - py)
                    if d <= self.gate and (bd is None or d < bd):
                        best, bd = q, d
                if best is None and self.cast.get(o["cam_id"]):
                    # 등장 인원이 다 찼으면 새로 만들지 말고 **가장 가까운 빈 자리**
                    # 를 넓은 게이트로 되쓴다 — 루프라 인원이 고정인 걸 아니까.
                    act = sum(1 for q, m in self.meta.items()
                              if m["cam"] == o["cam_id"] and m["gid"] in live)
                    if act >= self.cast[o["cam_id"]]:
                        cand = [(math.hypot(o["x"] - self._pred(m, t)[0],
                                            o["y"] - self._pred(m, t)[1]), q)
                                for q, m in self.meta.items()
                                if m["cam"] == o["cam_id"] and m["gid"] not in live
                                and t - m["t"] <= self.join_gap_s * 2]
                        if cand:
                            d, q = min(cand)
                            if d <= self.gate * 2.5:
                                best = q
                if best is None:
                    self._n += 1
                    pid = f"P{self._n:02d}"
                    self.ser[pid] = deque()
                    self.meta[pid] = {"cam": o["cam_id"], "gid": o["gid"],
                                      "x": o["x"], "y": o["y"], "vx": 0.0, "vy": 0.0,
                                      "t": t, "birth": t,
                                      "first": (o["x"], o["y"])}
                    self.n_new += 1
                else:
                    pid = best
                    self.meta[pid]["gid"] = o["gid"]
                    self.n_join += 1
            m = self.meta[pid]
            dt = max(1e-3, t - m["t"])
            if t > m["t"]:
                m["vx"], m["vy"] = (o["x"] - m["x"]) / dt, (o["y"] - m["y"]) / dt
            m["x"], m["y"], m["t"] = o["x"], o["y"], t
            s = self.ser[pid]
            if not s or s[-1][0] < t:
                s.append((t, o["x"], o["y"]))
            while s and t - s[0][0] > self.keep_s:
                s.popleft()
        self._merge(t, live)
        for pid in [p for p, m in self.meta.items() if t - m["t"] > self.keep_s]:
            del self.meta[pid], self.ser[pid]

    def _merge(self, t, live):
        """**겹친 채로 갈린 id** 를 하나로 되돌린다.

        흔한 실패는 끊김이 아니라 겹침이다 — 한 사람에게 옛 id·새 id 가 0.2~0.4초
        같이 붙었다가 옛 것이 죽는다. 그 순간엔 빈 자리가 없어 새 자리를 만들고,
        결과적으로 한 사람이 두 id 로 갈린다(실측: 카메라당 바퀴마다 1~2개 초과).
        방금 죽은 자리 옆에서 **그 직전에 태어난** 자리를 찾아 되붙인다.
        """
        dead = [(p, m) for p, m in self.meta.items()
                if m["gid"] not in live and 0 <= t - m["t"] <= self.join_gap_s]
        for pa, ma in dead:
            if pa not in self.meta:
                continue
            best, bd = None, None
            for pb, mb in self.meta.items():
                if pb == pa or mb["cam"] != ma["cam"] or mb["gid"] not in live:
                    continue
                if not (ma["t"] - 0.9 <= mb["birth"] <= ma["t"] + self.join_gap_s):
                    continue
                px, py = self._pred(ma, mb["birth"])
                d = math.hypot(mb["first"][0] - px, mb["first"][1] - py)
                if d <= self.gate and (bd is None or d < bd):
                    best, bd = pb, d
            if best is None:
                continue
            mb = self.meta[best]
            keep = [q for q in self.ser[pa] if q[0] < mb["birth"]]
            self.ser[pa] = deque(keep + list(self.ser[best]))
            self.meta[pa] = {**mb, "birth": ma["birth"], "first": ma["first"]}
            del self.meta[best], self.ser[best]
            self.n_merge += 1

    def _still(self, pid, t) -> bool:
        h = [q for q in self.ser[pid] if t - q[0] <= self.still_s]
        if len(h) < 6 or (h[-1][0] - h[0][0]) < self.still_s * 0.7:
            return False                           # 아직 판단할 만큼 안 봤다
        xs = [q[1] for q in h]; ys = [q[2] for q in h]
        span = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        return span < self.still_px

    def at(self, t: float, drop_still: bool = True) -> list[dict]:
        """t 시점에 그릴 사람들 — pid 시계열에서 Catmull-Rom 으로 뽑는다."""
        out = []
        for pid, s in self.ser.items():
            p = list(s)
            i = None
            for k in range(len(p) - 1):
                if p[k][0] <= t <= p[k + 1][0]:
                    i = k
                    break
            if i is None or (p[i + 1][0] - p[i][0]) > self.max_gap_s:
                continue                           # 구멍이 크면 안 잇는다(되감기)
            if drop_still and self._still(pid, t):
                continue
            s1, s2 = p[i], p[i + 1]
            s0 = p[i - 1] if i > 0 else None
            s3 = p[i + 2] if i + 2 < len(p) else None
            h = (s2[0] - s1[0]) or 1e-6
            u = max(0.0, min(1.0, (t - s1[0]) / h))
            u2, u3 = u * u, u * u * u
            h00, h10 = 2 * u3 - 3 * u2 + 1, u3 - 2 * u2 + u
            h01, h11 = -2 * u3 + 3 * u2, u3 - u2
            pos = []
            for ax in (1, 2):
                p1, p2 = s1[ax], s2[ax]
                t1 = ((p2 - s0[ax]) / (s2[0] - s0[0]) * h) if s0 else p2 - p1
                t2 = ((s3[ax] - p1) / (s3[0] - s1[0]) * h) if s3 else p2 - p1
                pos.append(h00 * p1 + h10 * t1 + h01 * p2 + h11 * t2)
            out.append({"pid": pid, "cam_id": self.meta[pid]["cam"],
                        "x": pos[0], "y": pos[1]})
        return out
