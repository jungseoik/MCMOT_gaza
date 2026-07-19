# MACS DeepStream 인제스트 실행 이미지 — DS 9.0 + pyds + 프로젝트 파이썬 의존
# 빌드:  docker build -t macs-deepstream:9.0 system/ingest_ds/docker
# 실행:  tools/run_ds_pipeline.sh (GPU별 워커 기동)
FROM nvcr.io/nvidia/deepstream:9.0-triton-multiarch

# DS 추가 컴포넌트 + pyds 바인딩 (NVIDIA 공식 설치 스크립트)
# -b 는 wheel 빌드까지만 성공하고 pip install을 건너뛰는 경우가 있어 명시 설치.
RUN /opt/nvidia/deepstream/deepstream/user_additional_install.sh || true
RUN /opt/nvidia/deepstream/deepstream/user_deepstream_python_apps_install.sh -b || true
# import 검증은 런타임에서만 가능(libcuda는 --gpus 실행 시 주입됨)
RUN pip install --no-cache-dir \
    /opt/nvidia/deepstream/deepstream/sources/deepstream_python_apps/bindings/dist/pyds-*.whl

# 파이프라인 워커가 쓰는 최소 파이썬 의존 (프레임 밖 좌표·임베딩만 다룸)
RUN pip install --no-cache-dir numpy opencv-python-headless pyzmq

# 추론·추적 스택 (system/ingest_ds/worker.py):
#  - torch/torchvision cu130 — 호스트 requirements.txt와 동일 버전 고정
#  - tensorrt-cu13 — 파이썬 바인딩. 이미지의 trtexec(10.14.1.48)와 동일 버전
#    고정 필수: 컨테이너에서 빌드한 external/weights/trt_ds/*.engine 로드용
#  - cupy-cuda13x — NvBufSurface zero-copy 래핑 (pyds.get_nvds_buf_surface_gpu)
#  - scipy·lap — tracker/(BoostTrack) import 최소 의존, msgpack — ZMQ 직렬화 옵션
RUN pip install --no-cache-dir torch==2.9.0+cu130 torchvision==0.24.0+cu130 \
    --extra-index-url https://download.pytorch.org/whl/cu130
RUN pip install --no-cache-dir tensorrt-cu13==10.14.1.48.post1 cupy-cuda13x scipy lap msgpack

WORKDIR /workspace
