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
