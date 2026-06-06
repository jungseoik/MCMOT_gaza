# 06 · 설계 결정 & 함정 기록

실제 개발 중 막혔던 지점과 해결. 다시 만들 때 같은 함정을 피하기 위한 기록.

## G1. 브라우저가 결과 mp4를 재생 못 함 (코덱)

- **증상**: 완료 후 `<video>`로 결과를 틀면 첫 프레임에서 멈춤.
- **원인**: OpenCV `VideoWriter`가 `mp4v`(MPEG-4 Part 2)로 저장 → 브라우저 `<video>`는
  H.264(avc1)만 디코딩. (`ffprobe` 없이도 파일 헤더의 `mp41`/`0x01b3` VOP 코드로 확인됨)
- **해결**: `<video>` 대신 **MJPEG 루프**. 서버가 mp4를 cv2로 디코딩(또는 보관한 JPEG를)
  re-emit → 브라우저는 `<img>`로 JPEG만 받음. 코덱 의존 제거.
- **대안**: 진짜 시킹/다운로드용 재생이 필요하면 `conda install -c conda-forge ffmpeg`로
  H.264 트랜스코딩 후 서빙(미채택).

## G2. 루프 재생이 4fps로 느림 (동기 제너레이터 + time.sleep)

- **증상**: 디코딩/인코딩은 346fps인데 스트림은 4fps.
- **원인**: 동기 제너레이터를 `StreamingResponse`가 스레드풀에서 돌리며 `time.sleep`이
  프레임당 페이싱을 망가뜨림.
- **해결**: **async 제너레이터 + `asyncio.sleep`**. 추가로 **고정 스케줄**
  (`next_t += 1/fps`; `sleep(next_t-now)`)로 전송 지연을 흡수.

## G3. 그래도 20fps 천장 (프레임 크기 = 전송 병목)

- **증상**: 디코딩 0(미리 인코딩)인데도 20fps.
- **원인**: 프레임 JPEG이 173KB로 커서 **전송이 병목**(3.3MB/s).
- **측정**: 960px/q80→173KB→~20fps, 854px/q72→108KB→**25fps**, 480px/q50→31KB→여유.
- **해결**: 스트림 프레임을 `STREAM_MAX_WIDTH=854`로 다운스케일 + `JPEG_QUALITY=72`.
  저장 mp4는 원본 해상도 유지.
- **교훈**: "느림"의 원인을 추측 말고 **단계별로 분리 측정**(디코딩/인코딩 vs 전송).

## G4. 디버깅을 흐린 진짜 원인 — 죽지 않은 옛 서버

- **증상**: 코드를 고쳤는데 측정값이 안 변함(20fps 고정).
- **원인**: `pkill -f webui`가 **자기 명령줄("webui" 포함)을 죽이고** 정작 서버는 안 죽음
  → 포트 8000을 옛 서버가 점유, 새 서버는 기동 실패(exit 1) → 옛 코드가 응답.
- **해결**: 자기 매칭 회피용 패턴(`pkill -f "[p]ython -m webui"`) 또는 PID/포트로 확인 후
  종료. 변경 반영 의심되면 **응답으로 새 코드 흔적을 직접 검증**(예: 프레임 크기).

## G5. `/status` 500 — numpy 타입 JSON 직렬화 불가

- **원인**: metrics에 `np.float64`가 섞임 → 기본 json 직렬화 실패.
- **해결**: `metrics()`에서 모든 값을 순수 `float`/`int`로 캐스팅.

## G6. 대시보드가 완료 시점에 멈춤 / 영상과 불일치

- **원인**: `status==done`에 폴링 중단 + 완료 후 metrics 미계산 → 마지막 값 고정.
  영상은 루프라 둘이 **다른 프레임**을 봄.
- **해결**: 프레임별 metrics를 `replay_metrics[]`에 보관(인메모리, DB 불필요) → 완료 후
  `/metrics_all`로 받아 영상과 같은 fps·인덱스로 **동기 재생**. 동시에 영상 오버레이의
  집계(count/avg) 텍스트는 제거하고 **대시보드를 단일 기준**으로.

## G7. 세 화면이 동시에 보임 — `.hidden` 미정의

- **원인**: 이식한 디자인 CSS에 `.hidden`이 없음.
- **해결**: `index.html`에 `.hidden{display:none!important}` 직접 정의.

## G8. 헤드리스 스크린샷 — chromium `libasound.so.2` 없음

- **증상**: playwright chromium 기동 시 `libasound.so.2: cannot open` (sudo 불가).
- **해결**: `conda install -c conda-forge alsa-lib`로 lib 확보 →
  `libasound.so.2`만 별도 폴더에 심볼릭링크하고 `LD_LIBRARY_PATH`에 추가해 실행
  (시스템 전역 오염 회피). 누락 lib은 `ldd <chrome> | grep "not found"`로 확인.
- 비고: playwright/alsa-lib는 **개발/캡처용**이라 `webui/requirements.txt`에는 넣지 않음.

## 설계 결정 요약

| 결정 | 대안 | 선택 이유 |
|------|------|-----------|
| MJPEG `<img>` | `<video>` H.264 | 코덱 의존 0, 즉시 동작 (G1) |
| vanilla 이식 | React 채택 | 오프라인·빌드 불필요, 시안은 CSS만 가치 |
| 잡 직렬화(lock) | 잡별 트래커 인스턴스 | 단순·안전(트래커 상태 공유 문제) |
| 서버 baked 오버레이 | 클라이언트 HTML 박스 | 좌표 동기/전송 부담 없음, 이미 검증됨 |
| 인메모리 지표 | DB | 휘발성으로 충분, 의존성 0 |
| 슬라이딩 윈도우 속도 | 3초 배치(원형) | 대시보드 실시간성 |

## 남은 한계 (의도적으로 남김)

- 트랜스포트바 seek/일시정지 미동작(MJPEG) — 장식
- 재생 동기화는 클라이언트 타이머 기반 → 수백 ms 드리프트 가능
- 단일 뷰어/단일 잡 직렬 가정(동시 다중 사용자는 확장 필요)
- Inter 폰트만 CDN(없으면 폴백)
