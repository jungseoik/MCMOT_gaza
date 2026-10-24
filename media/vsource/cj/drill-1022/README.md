# CJ제일제당센터 피난대피훈련 (2026-10-22) — 리허설 패키지

액션캠 **26대**로 찍은 실제 피난대피훈련. 지금까지의 리허설(각본 30~80초)과 달리
**한 번에 9~13분을 통으로** 찍은 실훈련 기록이다.

- 출처: HF `PIA-SPACE/C-lab-AIHub-2026-10-22` → `03_scenarios/` (제공사 분할본 그대로)
  - 레포 이름은 `AIHub` 지만 내용은 **CJ제일제당센터** 다 (1F·16F·17F)
  - 원본(무손실) `01_original/` · 카메라별 전체녹화본 `02_concat_aligned/`
- 영상은 git 에 없다(`*.mp4` 전역 ignore). 받는 법은 아래 "영상 받기".

## 시나리오 6개 — 건물 "최종CJ제일제당PoC"(cjpoc) 층을 빙의

카메라가 많아(26대) 층별로 나눠 돌린다. 영상 폴더는 2개(`scenario_16F/`·`scenario_17F/`)고,
시나리오 6개가 그 클립을 골라 쓴다(같은 파일 공유).

| 시나리오 | 구간 | 길이 | 카메라 | 영상 폴더 |
|---|---|---:|---|---|
| `scenario_all` | 13:00 ~ 25:45 | 765s | 17층 9 + 16층 9 + 1층 8 = **26대** (16층은 550s 에서 끝) | 두 폴더 모두 |
| `scenario_16F_1F` | 13:00 ~ 22:10 | 550s | 16층 9 + 1층 8 = **17대** | `scenario_16F/` |
| `scenario_17F_1F` | 13:00 ~ 25:45 | 765s | 17층 9 + 1층 8 = **17대** | `scenario_17F/` |
| `scenario_16F_only` | 13:00 ~ 22:10 | 550s | 16층 **9대** | `scenario_16F/` |
| `scenario_17F_only` | 13:00 ~ 25:45 | 765s | 17층 **9대** | `scenario_17F/` |
| `scenario_1F_only` | 13:00 ~ 25:45 | 765s | 1층 **8대** | `scenario_17F/` |

1층 단독은 **17F 쪽 클립(765s)** 을 쓴다 — 16층·17층 인원 탈출이 모두 담긴 구간이라서.

```
17층 (floor7)  cam2  cam5  cam7  cam9  cam13 cam14 cam15 cam19 cam23
16층 (floor8)  cam3  cam10 cam11 cam20 cam21 cam22 cam25 cam26 cam27
1층  (floor9)  cam1  cam4  cam6  cam8  cam12 cam16 cam17 cam18
```

floor7/8/9 는 기존 CJ 층(default 17F · floor2 16F · floor3 지상1층)의 도면 세팅을
그대로 복제한 층이다(2026-10-23). 기존 층은 건드리지 않는다.

카메라 번호는 **전 층에 걸쳐 유일**하다(cam24 결번). 그래서 cam 하나 = 층 하나로 고정된다.

## 싱크 — 보정 불필요

**전 클립의 00:00 이 같은 순간**이다. 제공사가 슬레이트 기준으로 맞춰서 잘라줬다.
액션캠 burn-in 시계로 판정하지 말 것(수동 설정값이라 못 믿는다).

| 클립 시각 | 실제 시각 | 뜻 |
|---|---|---|
| `00:00` | 14:59:55 | 훈련 시작 — 16층에서 가장 먼저 출발 |
| `00:05` | 15:00:00 | **피난 사이렌** |
| `09:10` | 15:09:05 | 16층 인원 1층 탈출 (16F 클립 끝) |
| `12:45` | 15:12:40 | 17층 인원 1층 탈출 (17F 클립 끝) |

→ 매니페스트의 `alarm_offset_sec: 5.0` · `wallclock_t0: 2026-10-22T14:59:55+09:00`

## 문서 · 정답 · 카운트 방식

- [docs/](docs/README.md) — 시각 기준·근사 정답·출구 카운트 채점 결과
- [answers.json](answers.json) — 층별 피난층 종료 시각까지 비상구별 근사 정답 (16층 37·61 / 17층 68·19)
- [exit_variants.json](exit_variants.json) — 출구 카운트 두 방식(선 통과 / 문 영역). `tools/exit_variants.py show|apply|compare|score`
- 녹화: `python tools/run_drill_batch.py --set cj-1022 [--tag "· 선 카운트"]`

## 알아둘 점

- **cam16(1층)은 앞 42초가 검정** — 녹화 시작이 늦어 앞을 채운 것. 실측 확인:
  0~42초 검정, **43초부터 영상**. 두 시나리오 모두 해당. 나머지 25대는 0초부터 정상.
- **배터리로 꺼진 카메라 없음** — 훈련 구간 내내 전 카메라 정상.
- 1280×720 / 30fps / H.264 **High, B-frame 2** / 무음.
  → 파일 소스 모드(기본)는 그대로 읽는다. **RTSP 송출판으로 쓰려면**
  `python tools/rehearsal_prep.py media/vsource/cj/drill-1022 --encode` 로 baseline 재인코딩.
- `grid_preview/` 는 제공사 확인용 3×3 합본(1920×1080). **송출 대상 아니다.**

## 남은 일 — 카메라 26대 매핑

`cameras[].mapping` 이 **전부 비어 있다**. 대응점(CCTV 4~5점 ↔ 도면 좌표)은
:8900 [② 매핑] 에서 사람이 찍어야 한다. 그 전까지 이 패키지는 2D 맵에 아무것도 못 띄운다.

층 도면은 이미 있다 — `floor7`(17층) · `floor8`(16층) · `floor9`(1층).

## 영상 받기

```bash
# .env 의 HF_TOKEN(PIA-SPACE) 이 필요하다. backseollgi 토큰은 이 레포에 권한이 없다.
python - <<'PY'
import re
tok=[re.match(r'\s*HF_TOKEN\s*=\s*(.+)',l).group(1).strip().strip('"\'')
     for l in open('.env') if re.match(r'\s*HF_TOKEN\s*=',l)][0]
from huggingface_hub import snapshot_download
snapshot_download("PIA-SPACE/C-lab-AIHub-2026-10-22", repo_type="dataset", token=tok,
                  allow_patterns=["03_scenarios/**"],
                  local_dir="media/vsource/_raw/cj-2026-10-22")
PY
# 받은 뒤 패키지 배치(하드링크 — 디스크 추가 소모 없음)
```
받은 트리(`03_scenarios/scenario_16F/16F/cam3.mp4`)를 패키지의 평평한 배치
(`scenario_16F/cam3.mp4`)로 하드링크한다. 10 GB 가 두 번 들지 않는다.
