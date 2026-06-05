"""Entry point: `python -m webui` launches the live tracking web UI."""
import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="BoostTrack live tracking web UI")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="dev auto-reload")
    args = parser.parse_args()

    uvicorn.run("webui.server:app", host=args.host, port=args.port,
                reload=args.reload)


if __name__ == "__main__":
    main()
