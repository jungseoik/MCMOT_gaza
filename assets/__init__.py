from huggingface_hub import hf_hub_download
import os

def download_required_files():
    """Initialize required files from Hugging Face Hub"""
    try:
        cache_dir = "external/weights"
        if not os.path.exists(os.path.join(cache_dir, "mot20_sbs_S50.pth")):
            hf_hub_download(
                repo_id="backseollgi/mot20_sbs_S50.pth",
                filename="mot20_sbs_S50.pth",
                # cache_dir=cache_dir,
                local_dir=cache_dir
            )
        print("Required files downloaded successfully")
    except Exception as e:
        print(f"Error downloading required files: {e}")

def download_required_files2():
    """Initialize required files from Hugging Face Hub"""
    try:
        cache_dir = "external/weights"
        if not os.path.exists(os.path.join(cache_dir, "bytetrack_x_mot20.tar")):
            hf_hub_download(
                repo_id="backseollgi/bytetrack_x_mot20.tar",
                filename="bytetrack_x_mot20.tar",
                # cache_dir=cache_dir,
                local_dir=cache_dir
            )
        print("Required files downloaded successfully")
    except Exception as e:
        print(f"Error downloading required files: {e}")

download_required_files()
download_required_files2()