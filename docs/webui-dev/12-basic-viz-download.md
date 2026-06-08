# 12 · 기본 시각화(다운로드) 모드 + 클린 라벨

속도·카운팅 같은 부가 분석 없이 **ID+박스만** 그려 추론하고, 결과를 **H.264로 저장·
다운로드**하는 파일 전용 모드. "그냥 추적 결과 영상만 받고 싶다"를 위한 독립 모드.

## 동작

- **파일 업로드 전용.** RTSP(라이브)에선 저장/다운로드가 없으므로 이 모드는 **세팅 화면에서
  숨겨진다**(파일이면 노출 + 기본 선택; RTSP면 숨기고 속도/밀도로 자동 전환).
- 분석: 프레임마다 **ID + 박스만** 그림(속도/대시보드 없음). 분석 화면에선 대시보드를 숨겨
  **영상이 전체 폭**으로 깔끔하게.
- 완료 시 결과 mp4를 **H.264로 트랜스코딩** → 어디서든·브라우저에서 재생 가능. **다운로드
  버튼**(우상단)으로 `tracked_{id}.mp4` 저장.

## 흐름 / 코드

```
업로드 → 분석모드 "기본 시각화(다운로드)" → 시작
  POST /start {basic:true}
  워커: stream → draw_basic(ID+박스) → mp4v 저장
        완료 후 status "encoding" → _transcode_h264(mp4v→H.264) → "done"
  GET /result/{id}?download=1  (Content-Disposition: attachment)
```

- `webui/basic_viz.py` — `draw_basic(frame, targets)`: ID+박스만(공용 클린 헬퍼 사용).
- `webui/server.py`:
  - `Job.basic`, `/start {basic:true}` 분기, `_make_analyzer`→`"basic"` 센티넬, `_process`가 `draw_basic` 호출.
  - `_transcode_h264(path)` — ffmpeg `libx264 -pix_fmt yuv420p -movflags +faststart`로 in-place 변환.
  - `/result?download=1` → `FileResponse(filename=...)`로 첨부 다운로드.
  - 완료 직전 `status="encoding"`(프론트가 "H.264 인코딩 중" 표시).
- 프론트(`index.html`): 분석모드 라디오에 basic 추가(파일 기본), 그 모드면 대시보드 숨김 +
  완료 시 다운로드 버튼 노출. RTSP면 basic 옵션 숨김.

## 클린 라벨 통일 (`webui/draw_utils.py`)

ID 글자/박스 렌더링을 **basic·speed·counter 공통**으로 통일:
- **안티에일리어싱**(`cv2.LINE_AA`), **해상도 비례 폰트/두께**(`font_scale`/`box_thickness`),
  **채운 라벨 태그 + 명도대비 글자색**.
- `draw_id_box(vis, x1,y1,x2,y2, tid)` 로 세 모듈이 동일 스타일.
- 비용: 그리기 파라미터 변경뿐, 프레임당 <1ms(추론 ~60ms 대비 무시). fps 영향 없음.

> 미리보기(MJPEG)는 854px 축소+JPEG라 다소 부드럽고, **다운로드 파일은 원본 해상도+H.264**라 가장 선명.

## 한계 (정직하게)

- **retention(자동 정리) 미적용** — 결과 mp4가 `webui/_data/outputs/`에 계속 쌓임(gitignore). 필요 시 추가.
- H.264 트랜스코딩에 완료 후 추가 시간(영상 길이 비례, 단편은 수 초).
- ffmpeg 없으면 변환 생략(mp4v 유지) — 브라우저 재생 불가할 수 있음.
- RTSP+basic은 UI에서 차단(라이브엔 저장/다운로드 개념 없음).
