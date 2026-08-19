# 디폴트 세팅(seed) 버전 보관소

`data/seed/<site>/` 는 UI **[Reset]** 버튼(`POST /api/site/reset-seed`)이 복원하는
디폴트 세팅이다. **한 벌뿐이라 바꾸면 이전 디폴트로 못 돌아간다.**
이 폴더는 그 시점의 seed 를 이름 붙여 보관해 두는 곳이다.

도구: [`tools/seed_version.py`](../../tools/seed_version.py)

```bash
python tools/seed_version.py list                 # 보관 목록 + 현재 seed 요약
python tools/seed_version.py show v1              # 한 버전 상세(층·축척·카메라)
python tools/seed_version.py save v2 --note "..." # 현재 seed 를 v2 로 보관
python tools/seed_version.py save v2 --source live --note "..."
                                                  # 지금 돌고 있는 라이브를 보관
python tools/seed_version.py restore v1           # seed 만 v1 로 교체 (라이브 유지)
python tools/seed_version.py restore v1 --apply   # 라이브까지 즉시 복원 (= [Reset])
```

- `restore` 는 되돌리기 **직전 seed 를 `auto-YYYYmmdd-HHMMSS` 로 자동 보관**한다.
  실수로 눌러도 잃지 않는다. (생략: `--no-backup`)
- 세션 녹화본(`sessions/`)은 어느 명령에서도 건드리지 않는다.
- 버전 1개당 약 3 MB(맵 png 포함). **git 에 커밋해야** 다른 서버에서도 복원된다.

## 디폴트를 새로 바꾸는 절차

1. 지금 디폴트를 보관 — `python tools/seed_version.py save vN --note "..."`
2. UI(`:8900`)에서 원하는 상태로 맞춘다 (층·맵·카메라·매핑·구역…)
3. 라이브를 seed 로 승격 — `bash tools/seed_snapshot.sh`
4. 커밋 — `git add data/seed data/seed_versions && git commit`

이후 [Reset] 을 누르면 3번에서 만든 상태가 나온다.
되돌리려면 `restore vN --apply`.

## 보관된 버전

| 이름 | 저장 | 내용 |
|------|------|------|
| `v4` | 2026-08-19 | **★ 현재 디폴트([Reset] 대상)** — 16F 완성: CAD(2000×1887) + 구역 9 · 병목 4(ρcrit 2.0) · 출입구 2 · 경로 8(자동 5 + 수동 r1~r3), 현장 6채널 매핑. 17F 는 시드 도면 + 3채널 매핑 |
| `v3` | 2026-08-19 | **현재 운영 구성** — 17F=시드 도면(3400×3207, 수동 2점 0.02391 m/px)에 3채널(cam01~03) 매핑, 16F=CAD(2000×1887, 자동 0.03662 m/px)에 현장 6채널(cam04~09) 매핑·구역 9, 지상1층=CAD·미매핑 |
| `v2` | 2026-08-19 | **16F CAD 적용 + 현장 6채널 매핑** — 17F·16F 모두 CAD(2000×1887, 자동 0.03662 m/px), 16F 구역 9·출입구 2·경로 5, 카메라 12대 중 cam04~cam09(16F 현장 6채널) 매핑 완료. cam01~03·1F 3채널은 미매핑 |
| `v1` | 2026-08-19 | 삼성화재 PoC 초기 3층 구성 — 17F·19F 시드맵(3400×3207, 수동 2점 0.02391 m/px) + 지상1층 CAD(2346×1672, 자동 0.04 m/px), 카메라 3대(cam01·cam02·cam03, 전부 매핑됨) |

> `pre-*` 는 위험한 작업 직전 안전망으로 남긴 것, `auto-*` 는 `restore` 직전에
> 자동으로 남은 것이라 표에 적지 않는다.
> 필요 없으면 폴더째 지우면 된다.
