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

WORKDIR /workspace
