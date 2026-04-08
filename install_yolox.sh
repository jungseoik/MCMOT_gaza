#!/bin/bash
set -e
# yolox requires torch at build time, so install separately after requirements.txt
pip install yolox==0.3.0 --no-build-isolation --no-deps
