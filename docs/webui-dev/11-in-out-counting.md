# 11 · 인·아웃 라인 카운팅 (재실 추정)

출입구에 **가상 라인(2점)** 을 그어 통과하는 사람을 in/out으로 세고, **재실(occupancy)
= in − out** 을 실시간 추정한다. 화장실·폐쇄공간 출입구 CCTV로 "안에 몇 명 남았나"를
보는 표준 기법(tripwire counting). 코드: `webui/counter.py`.

## 원리

- 선 = 두 점 A,B. 외적 부호 `sign((B−A)×(P−A))` 로 점 P가 선의 **어느 쪽**인지 판별.
- 각 사람의 **발끝(접지점)** 이 어느 쪽인지 추적 → **안정 쪽(side)이 뒤집히면 = 통과**.
- 사용자가 클릭한 **"안쪽" 점**의 부호 = inside_sign. 통과 후 쪽이 inside면 **in++**, 아니면 **out++**.
- 재실 = 시작값(기본 0) + in − out.

카운팅은 **이미지 픽셀 공간**에서 한다 → 속도·맵과 달리 보정(호모그래피) 불필요.

## 오카운트 방지 / 옵션

- **deadband(`margin_px`)**: 선 위에 서서 떨리면 부호가 진동 → 거짓 카운트. 선에서
  일정 거리(기본 6px) 안에선 "안정 쪽"을 갱신하지 않아 진동을 무시.
- **선분 vs 무한선(`segment_only`)**: 기본은 **두 점 사이 선분 근처**(투영 t∈[−pad,1+pad])
  통과만 카운트(출입구 한정). 해제 시 선을 **무한 연장**해 화면 전체를 분단.
- **기준점 = 발끝**(접지). 안/밖은 클릭으로 지정.

## 핵심 코드 (`LineCounter`)

```python
def update(self, targets):
    for tg in targets:
        P = foot(tg); d = signed_dist(P)          # 선까지 부호 거리(px)
        if abs(d) < margin: continue              # deadband(진동 무시)
        cur = +1 if d > 0 else -1
        prev = side.get(id)
        if prev is None: side[id] = cur; continue # 첫 안정 쪽
        if cur != prev:                           # 통과!
            if (not segment_only) or near_segment(P):
                in_count  += (cur == inside_sign)
                out_count += (cur != inside_sign)
            side[id] = cur
```

`metrics()` → `{kind:"count", in, out, occupancy, alert(occ<0), present}`.

## "0" 체크 = 안전장치

비었다고 아는 시점에 **재실 ≠ 0**, 특히 **음수(out>in)** 면 → 가림으로 미카운트했거나
관측 밖에서 유입/유출됐다는 신호. UI가 **음수 재실에 경보(빨강)** 를 띄운다.
바로 "in−out 합이 0이어야 한다"는 검증 의도를 그대로 구현한 것.

## 흐름 / UI

1. 세팅에서 **분석 = 인·아웃 카운팅** 선택.
2. 첫 프레임에 **선 2점** 클릭 → **안쪽(공간 내부) 1점** 클릭. 선분/무한선 토글.
3. `POST /start {count:{line, inside, segment}}` → 워커가 `LineCounter`로 처리
   (속도 분석 대신). 파일·RTSP 둘 다 동작.
4. 분석 화면: 영상에 라인 + IN 방향 화살표 + 발끝점 + 하단 IN/OUT/재실, 우측 대시보드
   (IN / OUT / 현재 재실 / 음수 경보). 파일은 완료 후 카운트도 동기 재생.

## 정확도·한계 (정직하게)

- **문 앞 혼잡/가림·ID 스위치**가 최대 오차원. 사람이 겹쳐 미검출되면 통과를 놓침 →
  재실이 어긋남(음수 경보로 감지). 카메라는 출입구를 **위/정면에서 겹침 적게** 잡을수록 정확.
- **무한선**은 연장선상 다른 위치 통과자도 카운트 → 출입구만 비추거나 선분 모드 권장.
- 빠른 통과 + 과한 프레임 스킵(RTSP)이면 한쪽에서 못 잡아 놓칠 수 있음.
- 시작 시 이미 안에 있던 사람은 미반영(시작 재실 0 가정) — 필요 시 시작값 입력 확장.
- 테스트 영상 `assets/in_out_counting.mp4` 는 폐쇄공간이 아니라 광장 부감뷰라 재실
  숫자 자체는 의미 없음(메커니즘 검증용). 실제 출입구 선이면 의미 있는 재실.
