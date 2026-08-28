# media/vsource — 송출 준비된 훈련영상

[ADR 08](../../docs/architecture/08-훈련영상-동기송출-설계.md) 동기 송출용 영상 보관소.

```
media/vsource/<시나리오 id>/*.mp4
```

**git에 올리지 않는다**(대용량). HuggingFace `backseollgi/MCMOT` 에 보관하고
`tools/rtsp/fetch_assets.sh` 로 받는다. 이 README만 git 추적.

## `field/` 와의 차이

| | 내용 |
|---|---|
| `field/` | **현장 원본** CCTV (개인정보·대용량) |
| `media/vsource/` | **송출용으로 인코딩·길이 정렬까지 끝낸 준비본** |

## 준비 요건

1. **H.264 baseline** — `tools/rtsp/check_video.sh` 로 확인, 아니면 `encode_video.sh`
2. **같은 순간부터 시작** — 전 채널이 훈련 시작 시점부터 잘려 있어야 한다
3. **길이는 달라도 된다** — 시나리오 단위 사이클로 함께 되감긴다.
   다만 시연용이면 짧은 채널 뒤를 채워 길이를 맞추는 편이 보기 좋다

## 폴더 규칙 — 사이트 / 세트 / 시나리오

```
media/vsource/
  <site>/                  cj (CJ제일제당센터) · aihub (AI Hub) · …
    <set>/                 rehearsal (리허설) · drill (실훈련) · …
      README.md            출처 · 시나리오별 카메라 구성 표 (로컬 전용)
      scenario_NN/         송출 대상 camX.mp4 만
      grid_preview/        몽타주 미리보기 — **송출 금지**
```

- vsource 시나리오 id = `<site>-<set>-NN` (예 `cj-rehearsal-01`) → `data/scenarios/<id>.json`
- RTSP `path` = `<site>_camN` (예 `cj_cam8`). 시나리오가 달라도 같은 cam 번호는 같은 경로 →
  :8900 카메라는 사이트당 한 번만 등록
- **층은 사이트 것을 빌린다** — `cameras[].floor` 에 :8900 ① 맵설정의 층 id(`floor10`).
  도면·구역·출구·경로는 ①에서 관리(CAD 편집기). `floorplan/` 은 패키지가 도면을 직접
  들고 가는 자립 모드(`floors[].image`)에만 쓴다 — 기본은 빌리기.
- 예외: CJ **실훈련** 원본은 역사적 이유로 `field/encoded/` 에 있다 (`drill-16f` 시나리오)

## 보관 중인 영상 묶음 (로컬, git 제외)

| 폴더 | 내용 | 출처 |
|---|---|---|
| `cj/rehearsal/` | **CJ제일제당센터 리허설 영상** — 시나리오 14개(`scenario_01`~`14`), 각 4~5캠(cam1~14 조합). 1080p·30fps·h264·23~33초. `grid_preview/` 14개 별도 | HF dataset `PIA-SPACE/C-lab` / `03_scenarios.7z.001` (토큰: 레포 `.env`의 `HF_TOKEN`) |
| `aihub/rehearsal/` | **AI Hub 리허설 영상** — 예정 | — |

상세는 각 폴더의 `README.md` 참조.
