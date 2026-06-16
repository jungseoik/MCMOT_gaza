# webui 개발 문서 (Live Tracking & Speed Dashboard)

이 폴더는 `webui/` 모듈을 **무엇으로, 어떻게 만들었는지** 처음부터 따라 만들 수 있을
만큼 상세히 기록한 개발 문서입니다. "이 문서만 보고 다시 구현하면 되겠구나"가 목표입니다.

> 사용법(설치/실행)만 필요하면 [`../webui.md`](../webui.md)를 보세요.
> 이 폴더는 **내부 구현·설계·재현**에 초점을 둡니다.

## 읽는 순서

1. **[01-architecture.md](01-architecture.md)** — 전체 그림, 기술 스택과 선택 이유, 데이터 흐름
2. **[02-backend-and-streaming.md](02-backend-and-streaming.md)** — FastAPI 서버, Job 수명주기, MJPEG 라이브→루프 스트리밍, 동기화
3. **[03-speed-and-metrics.md](03-speed-and-metrics.md)** — 속도 추정 알고리즘, ROI·km/h 보정, 대시보드 지표 정의
4. **[04-frontend.md](04-frontend.md)** — 3화면 vanilla 프론트, 디자인 시스템 이식, 캔버스 ROI/보정, 재생 동기화
5. **[05-rebuild-from-scratch.md](05-rebuild-from-scratch.md)** — **빈 레포에서 단계별로 다시 만드는 가이드**
6. **[06-decisions-and-gotchas.md](06-decisions-and-gotchas.md)** — 막혔던 지점과 그 해결(코덱·페이싱·버전 등). 같은 함정 피하기용
7. **[07-depth-mode.md](07-depth-mode.md)** — Depth 자동 모드(Depth-Anything-3, 별도 env, 사람키 앵커, 미리보기 확인)
8. **[08-speed-and-calibration.md](08-speed-and-calibration.md)** — **속도·가속도 공식 + 보정 4가지(보정선/ROI/Depth) 설명 (누구나 읽는 버전)**
9. **[09-rtsp-live.md](09-rtsp-live.md)** — RTSP 라이브 모드(프레임 스킵·정지·자동정지·견고성 한계)
10. **[10-map-view.md](10-map-view.md)** — 2D 맵 뷰(top-down 점+방향벡터, 영상/맵 토글, 리소스 영향)
11. **[11-in-out-counting.md](11-in-out-counting.md)** — 인·아웃 라인 카운팅(재실 추정, 선 2점+안쪽, 음수 경보)
12. **[12-basic-viz-download.md](12-basic-viz-download.md)** — 기본 시각화(다운로드) 모드(ID+박스, H.264, 파일 전용) + 클린 라벨 통일(draw_utils)
13. **[13-alignment.md](13-alignment.md)** — 방향성 정렬도(이동방향 vs 기준 피난방향 코사인, opt-in, 색 3구간, 맵 기준 벡터·사잇각)

## 한 줄 요약

> 핵심 추론 파이프라인(`src.inference_gpu`)을 **재사용만** 하는 독립 FastAPI 모듈.
> 비디오 업로드 → 프레임 단위 TRT 추론 → 객체별 속도·밀도·체류 추정 →
> MJPEG로 실시간 표시(완료 후 결과 루프) + 우측 대시보드. React/빌드/DB 없음.

## 파일 지도

```
webui/
  __main__.py        python -m webui 진입점 (uvicorn 기동)
  server.py          FastAPI 앱: upload/start/status/stream/metrics_all/result
  speed.py           SpeedEstimator(속도·ROI·보정·지표) + annotate(오버레이)
  index.html         3화면(업로드/ROI세팅/분석) 단일 페이지 vanilla
  static/            이식한 디자인 시스템(colors_and_type.css, app.css, fonts/)
  requirements.txt   웹 전용 의존성(fastapi, uvicorn, python-multipart)
  _data/             런타임 업로드/결과(gitignore)

src/inference_gpu.py
  BoostTrackGPUInference.stream()   프레임 제너레이터(webui가 재사용)
```
