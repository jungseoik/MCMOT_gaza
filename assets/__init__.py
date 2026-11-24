"""가중치 자동 확보 — import 시 external/weights/ 에 없으면 HuggingFace 에서 받는다.

1차: backseollgi/MCMOT/weights/<파일>  (비공개 — HF_TOKEN 또는 레포 루트 .env, `hf auth login`)
2차: 예전 공개 개별 레포 backseollgi/<파일>  (토큰 없이도 받아진다)
"""
import os
import shutil

from huggingface_hub import hf_hub_download

WEIGHTS_DIR = "external/weights"
FILES = ("mot20_sbs_S50.pth", "bytetrack_x_mot20.tar")


def _token():
    tok = os.environ.get("HF_TOKEN")
    if tok:
        return tok
    env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        with open(env) as f:
            for line in f:
                if line.startswith("HF_TOKEN="):
                    return line.split("=", 1)[1].strip() or None
    except OSError:
        pass
    return None      # 없으면 hf auth login 저장 토큰을 라이브러리가 쓴다


def _fetch(name: str) -> None:
    dest = os.path.join(WEIGHTS_DIR, name)
    if os.path.exists(dest):
        return
    os.makedirs(WEIGHTS_DIR, exist_ok=True)
    try:
        src = hf_hub_download(repo_id="backseollgi/MCMOT", filename=f"weights/{name}", token=_token())
        shutil.copyfile(src, dest)
    except Exception:
        hf_hub_download(repo_id=f"backseollgi/{name}", filename=name, local_dir=WEIGHTS_DIR)


def download_required_files():
    """Initialize required files from Hugging Face Hub"""
    try:
        for name in FILES:
            _fetch(name)
        print("Required files downloaded successfully")
    except Exception as e:
        print(f"Error downloading required files: {e}")


download_required_files()
