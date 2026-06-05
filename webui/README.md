# Live Tracking Web UI

비디오를 업로드하면 TRT 추적 결과를 실시간으로 보여주고, 완료되면 결과를
무한 루프 재생하는 독립형 웹 UI. 코어 파이프라인(`src/`)을 **재사용만** 하며
README의 기존 워크플로는 바꾸지 않습니다.

## Quick start

```bash
pip install -r webui/requirements.txt   # 웹 전용 의존성 (1회)
python -m src.build_trt --fp16           # TRT 엔진 (없으면 1회)
python -m webui                          # http://localhost:8000
```

## 상세 문서

설치/실행 옵션, 동작 원리(MJPEG·페이싱·튜닝), 엔드포인트, 제약은
**[`docs/webui.md`](../docs/webui.md)** 참조.
