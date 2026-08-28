# system/vsource — 훈련영상 동기 송출

영상 파일 묶음을 **전 채널 같은 시각으로** RTSP 송출한다. 훈련 세션 전체를
실제 RTSP를 물린 것처럼 **리허설**하고, 현장 시연에도 쓴다.

설계·근거: [ADR 08](../../docs/architecture/08-훈련영상-동기송출-설계.md)

## 본체와의 관계

**추론·추적·4대 지표 파이프라인은 건드리지 않는다.** 카메라 입장에선 여전히 RTSP다.
켜지 않으면 시스템은 지금과 동일하게 동작하고, 켜는 동안만 해당 RTSP 경로의
**송출 주체**가 pm2 → vsource로 바뀐다(정지하면 pm2 복구).

## 왜 필요한가

`pm2 + ffmpeg -stream_loop -1` 로 채널마다 독립 송출하면 시간축이 안 맞는다.
영상 길이가 제각각(181.0 / 179.9 / 83.4s)이라 **루프마다 벌어지고**, 159시간
가동분 누적 드리프트가 약 11,000분이었다. 16F 카메라의 40초와 1F 카메라의 40초가
같은 순간이 아니면 IDR·건물 롤업이 무의미해진다.

## 구조

| 파일 | 역할 |
|---|---|
| `scenario.py` | `data/scenarios/<id>.json` 로드 + 영상 검증(존재·길이·fps·코덱), 사이클 자동 산출 |
| `publisher.py` | 채널 1개. **모듈이자 실행 스크립트** — 컨트롤러가 `python -m` 으로 띄운다 |
| `controller.py` | N채널 동시 시작·정지·상태 집계, pm2 연동, 상태파일 |

### 동기 방식 (실측 편차 1.7ms)

퍼블리셔를 미리 spawn 해두고 **각자 공통 T0까지 정밀 대기 후 ffmpeg를 exec** 한다.
순차 spawn 해도 실제 시작은 T0에 모인다. 구독(카메라) 타이밍은 맞출 필요가 없다 —
퍼블리셔가 맞으면 벽시계 `t` 에서 모든 스트림이 내용상 `(t − T0)` 위치라, 늦게 붙은
카메라도 붙는 순간부터 올바른 위치를 받는다.

### 사이클 루프

영상 길이가 제각각이라 채널별 루프는 금지다. `n`번째 재생을 `T0 + n*cycle` 에
시작해 **전 채널이 함께 되감긴다**. `cycle_sec` 미지정 시 `가장 긴 영상 + 2s`.

## 사용

```bash
# 시나리오 확인
curl -s localhost:8900/api/vsource/scenarios | python -m json.tool

# 시작 / 상태 / 정지
curl -s -XPOST localhost:8900/api/vsource/start \
  -H 'Content-Type: application/json' -d '{"scenario_id":"drill-16f","loop":true}'
curl -s localhost:8900/api/vsource/status | python -m json.tool
curl -s -XPOST localhost:8900/api/vsource/stop -H 'Content-Type: application/json' -d '{}'
```

UI는 **② 카메라 등록·매핑** 좌측 [훈련영상 송출] 패널. 송출 중이면 **③ 운영 뷰**
상단에 다음 사이클까지 남은 시간이 칩으로 뜬다 — 그 시점에 경보를 누르면 t=0부터
전 채널이 정렬된다.

## 시나리오 정의

```json
{
  "id": "drill-16f",
  "name": "화재대피훈련 — 16F 6채널",
  "cycle_sec": 0,
  "streams": [
    {"path": "field_16f_s", "file": "media/vsource/drill-16f/16f_s.mp4"}
  ]
}
```

- `path` = **카메라가 이미 보고 있는 RTSP 경로 그대로** (경로 인수 — 매핑을 안 고쳐도 된다)
- `file` = 레포 루트 기준 상대경로. 새 영상은 `media/vsource/<id>/` (gitignore + HF 보관)
- `cycle_sec: 0` = 자동 산출

## 지켜야 할 것

| | 이유 |
|---|---|
| `-re` 필수 | 없으면 181초를 몇 초에 쏟아붓는다. 시간축·지표가 전부 무의미해진다 |
| `-c:v copy` | 재인코딩하면 채널당 CPU 인코더가 붙어 9채널이면 CPU가 먼저 막힌다 |
| H.264 baseline | 카메라가 못 받는다. `tools/rtsp/encode_video.sh` 로 변환 |

## 환경변수

| 변수 | 기본 | 내용 |
|---|---|---|
| `VSOURCE_RTSP_HOST` | `127.0.0.1:8554` | mediamtx 주소 |
| `VSOURCE_LEAD_SEC` | `2.0` | spawn 여유(T0까지) |
| `VSOURCE_PM2_RESTORE` | `1` | 정지 시 pm2 상시송출 복구 |

## 진단

- 채널별 퍼블리셔 로그: `data/vsource_logs/<path>.log`
- 상태파일: `data/vsource_state.json` (detach 프로세스 재부착용)
- 스트림 확인: `ffprobe -rtsp_transport tcp -i rtsp://127.0.0.1:8554/<path>`

## 리허설 패키지 (ADR 09)

`media/vsource/<site>/<set>/rehearsal.json` 폴더가 영상·시나리오·카메라·매핑·도면의
**진실의 원천**이다 (`package.py`). 시나리오 id 는 `pkg:<패키지>:<시나리오>`.

- 시작하면 가상 카메라(`rh_*`)가 **자동으로 얹히고**, 종료하면 떼어진다 — 사이트
  (`data/sites/`)는 한 글자도 안 바뀐다.
- 층은 **사이트 층을 빌려 쓴다**(manifest `floor` = 사이트 층 id, ⑤ [붙일 층]).
  도면·구역·출구·경로는 ① 맵설정 소관 → 거기서 고치면 리허설 지표에 그대로 반영.
  (패키지 `floors[]` 에 `image` 가 있으면 예외적으로 가상 층 `rh_*` 자립 모드)
- 매핑을 UI에서 찍으면 **패키지 rehearsal.json 에 저장**된다 (폴더만 옮기면 재현).
- 활성 시나리오가 쓰는 카메라만 ingest 에 들어간다 (DS 16ch 한계 보호).
- 사전 검증: `python tools/rehearsal_prep.py <폴더>` (`--scaffold`, `--encode`,
  `--floorplan-from-site <층id>` — 구조 동일한 사이트 층 도면을 패키지로 복사).
- `data/scenarios/*.json` 은 legacy mock (drill-16f 등) — 그대로 동작.
- **오프라인 진단**: `python tools/rehearsal_viz.py --package cj-rehearsal --scenario scenario_02`
  → 라이브와 같은 5fps 분석으로 카메라 그리드(ID·헐·통과선) + 공유 2D 맵(패키지 매핑·사이트 층)
  영상을 만든다. 헐 밖(라이브 폐기) 관측을 빨간 ✕로 보여 "추론 문제 vs 매핑 범위 문제"를 가른다.

## 파일 소스 모드 (ADR 09 §11, 기본)

패키지 리허설은 RTSP 송출 없이 **영상 파일을 직접 읽어** 프레임 잠금 동기로 분석한다
(`filesource.py` → AnalyzerThread 큐 → 엔진). 준비 즉시, 경보 = t0, 앞머리·재부착 없음.
`rehearsal.json` 의 `"source": "rtsp"` 로 바꾸면 예전 송출 경로(현장 RTSP 검증용).
- **전체 연속 시나리오**: `python tools/rehearsal_concat_full.py --package cj-rehearsal` — 01~14 를 이어 붙이고
  없는 카메라 구간은 검정(간격 4s). 결과 `scenario_15_full/` 14대 동일 길이, 매니페스트 `segments` 기록.
