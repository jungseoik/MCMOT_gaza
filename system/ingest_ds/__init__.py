"""DeepStream 기반 인제스트+추론 워커 (INGEST_BACKEND=deepstream 경로).

기존 경로(system/ingest + system/tracking — 카메라별 ffmpeg/GStreamer 디코드
→ 풀해상도 BGR 파이프 → 직렬 TRT 추론)를 대체할 수 있는 zero-copy 배치 경로:

  nvurisrcbin(N) → nvstreammux(batch) → RGBA(NVMM, unified) → appsink
  → GPU letterbox 전처리 → dynamic-batch YOLOX(TRT) → 카메라별 BoostTrack
  → TrackedObject dict를 ZMQ PUSH (프레임 픽셀은 프로세스 밖으로 안 나감)

- worker.py  : 컨테이너(macs-deepstream:9.0) 안에서 도는 메인 (GPU 상주)
- bridge.py  : 호스트(conda) 측 ZMQ PULL → on_tracks 콜백 어댑터
- 실행법·엔진 빌드법·제약은 README.md 참조

주의: worker.py와 그 하위 모듈(trt_infer, gpu_embedding, yolox_post)은
컨테이너 전용이다 — 호스트 conda 환경에서는 bridge.py만 import할 것.
"""
