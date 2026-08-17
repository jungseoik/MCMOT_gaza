#!/usr/bin/env python3
"""CSV → MACS 카메라 일괄 등록.

현장 NVR 채널표(CSV) 한 장으로 카메라 N대를 한 번에 등록한다.
웹 UI에서 한 대씩 추가하면 DS 워커가 매번 재시작돼 같은 GPU 슬롯의 기존
채널이 전부 ~8s 끊긴다(실측 채널당 ~14s). 40채널이면 ~9.5분 + 40회 단절.
벌크 등록은 이를 1회로 줄인다.

두 가지 모드:
  api      (기본) 실행 중 서버의 POST /api/cameras/bulk 호출 — 서버 재기동 불필요
  offline  --offline. 서버가 안 떠 있을 때 카메라 JSON을 직접 생성.
           다음 서버 기동 시 일괄 반영된다.

CSV 컬럼 (헤더 필수, 순서 무관):
  rtsp                RTSP 주소. 이게 있으면 아래 nvr/port/track/stream은 무시.
  nvr, port, track, stream
                      rtsp 없을 때 NVR URL을 조립
                      → rtsp://<user>:<pass>@<nvr>:<port>/trackID=<track>&streamID=<stream>
                      (port 기본 554, stream 기본 2 = 서브스트림)
  name                표시 이름 (선택)
  analyze_fps         분석 fps (선택, 기본 5.0)
  floor_id            소속 층 id (선택, 비우면 default 층)
  min_conf            카메라별 최소 검출 신뢰도 (선택, 비우면 사이트값 상속)
  enabled             true/false (선택, 기본 true)

계정은 CSV에 넣지 않는다 — 환경변수로 주입한다(비밀번호 커밋 금지):
  NVR_USER, NVR_PASS

사용:
  python tools/bulk_register_cams.py cams.csv --dry-run
  NVR_USER=pia NVR_PASS='1q2w3e4r!' python tools/bulk_register_cams.py cams.csv
  python tools/bulk_register_cams.py cams.csv --offline --site default

주의: mapping(호모그래피 대응점)은 자동 생성이 불가능하다. 등록 후 웹 UI에서
카메라별로 찍어야 하며, mapping이 없는 카메라는 맵 투영에서 제외된다.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_API = "http://127.0.0.1:8900"
TRUE_WORDS = {"1", "true", "yes", "y", "t", "o", "on"}


def _s(row: dict, key: str) -> str:
    return (row.get(key) or "").strip()


def build_rtsp(row: dict, user: str, password: str, lineno: int) -> str:
    """rtsp 컬럼을 그대로 쓰거나, nvr/port/track/stream으로 조립."""
    if _s(row, "rtsp"):
        return _s(row, "rtsp")

    nvr, track = _s(row, "nvr"), _s(row, "track")
    if not nvr or not track:
        raise ValueError(f"{lineno}행: rtsp 또는 (nvr, track)이 필요")
    port = _s(row, "port") or "554"
    stream = _s(row, "stream") or "2"          # 기본 서브스트림
    if not user:
        raise ValueError(
            f"{lineno}행: NVR URL 조립에 계정이 필요 — NVR_USER/NVR_PASS 설정")
    # 비밀번호의 @ : / ? & 등은 URL을 깨뜨리므로 퍼센트 인코딩
    cred = f"{urllib.parse.quote(user, safe='')}:{urllib.parse.quote(password, safe='')}"
    return f"rtsp://{cred}@{nvr}:{port}/trackID={track}&streamID={stream}"


def parse_csv(path: Path, user: str, password: str) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"빈 CSV: {path}")

    out: list[dict] = []
    for i, row in enumerate(rows, start=2):        # 2행 = 첫 데이터행
        item: dict = {"rtsp": build_rtsp(row, user, password, i)}
        if _s(row, "name"):
            item["name"] = _s(row, "name")
        if _s(row, "analyze_fps"):
            item["analyze_fps"] = float(_s(row, "analyze_fps"))
        if _s(row, "floor_id"):
            item["floor_id"] = _s(row, "floor_id")
        if _s(row, "min_conf"):
            item["min_conf"] = float(_s(row, "min_conf"))
        if _s(row, "enabled"):
            item["enabled"] = _s(row, "enabled").lower() in TRUE_WORDS
        out.append(item)
    return out


def mask(url: str) -> str:
    """로그 출력용 — 자격증명 가림."""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    _, _, host = rest.partition("@")
    return f"{scheme}://***:***@{host}"


def register_api(base: str, items: list[dict]) -> list[dict]:
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/cameras/bulk",
        data=json.dumps({"cameras": items}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise SystemExit(f"등록 실패 (HTTP {e.code}): {body}") from None
    except urllib.error.URLError as e:
        raise SystemExit(
            f"서버에 접속할 수 없습니다 ({base}): {e.reason}\n"
            f"서버가 안 떠 있으면 --offline 모드를 쓰세요.") from None


def register_offline(site: str, items: list[dict]) -> list[dict]:
    cam_dir = REPO_ROOT / "data" / "sites" / site / "cameras"
    cam_dir.mkdir(parents=True, exist_ok=True)
    used = {p.stem for p in cam_dir.glob("cam*.json")}
    nums = [int(s[3:]) for s in used if s[3:].isdigit()]
    n = max(nums) + 1 if nums else 1

    saved = []
    for item in items:
        cam_id = f"cam{n:02d}"
        n += 1
        cfg = {"cam_id": cam_id, "name": item.get("name", ""),
               "rtsp": item["rtsp"], "enabled": item.get("enabled", True),
               "analyze_fps": item.get("analyze_fps", 5.0),
               "floor_id": item.get("floor_id"), "mapping": None,
               "valid_roi": None, "min_conf": item.get("min_conf")}
        (cam_dir / f"{cam_id}.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        saved.append(cfg)
    return saved


def main() -> int:
    ap = argparse.ArgumentParser(
        description="CSV로 MACS 카메라 일괄 등록",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("csv", type=Path, help="채널표 CSV 경로")
    ap.add_argument("--api", default=os.environ.get("MACS_API", DEFAULT_API),
                    help=f"서버 주소 (기본 {DEFAULT_API})")
    ap.add_argument("--offline", action="store_true",
                    help="서버 호출 없이 카메라 JSON 파일만 생성")
    ap.add_argument("--site", default=os.environ.get("SITE_ID", "default"),
                    help="offline 모드의 site_id (기본 default)")
    ap.add_argument("--dry-run", action="store_true",
                    help="등록하지 않고 파싱 결과만 출력")
    args = ap.parse_args()

    if not args.csv.is_file():
        print(f"CSV를 찾을 수 없습니다: {args.csv}", file=sys.stderr)
        return 2
    try:
        items = parse_csv(args.csv, os.environ.get("NVR_USER", ""),
                          os.environ.get("NVR_PASS", ""))
    except (ValueError, KeyError) as e:
        print(f"CSV 오류: {e}", file=sys.stderr)
        return 2

    print(f"▶ {args.csv} — 카메라 {len(items)}대")
    for i, it in enumerate(items, 1):
        print(f"  {i:3}. {it.get('name', ''):24} {mask(it['rtsp'])}"
              f"  fps={it.get('analyze_fps', 5.0)}"
              f"{' floor=' + it['floor_id'] if it.get('floor_id') else ''}")
    if args.dry_run:
        print("\n(--dry-run — 등록하지 않음)")
        return 0

    if args.offline:
        saved = register_offline(args.site, items)
        print(f"\n✅ 카메라 JSON {len(saved)}개 생성 — "
              f"data/sites/{args.site}/cameras/")
        print("   서버를 재기동하면 일괄 반영됩니다: pm2 restart macs-system")
    else:
        saved = register_api(args.api, items)
        print(f"\n✅ {len(saved)}대 등록 완료 — "
              f"{', '.join(c['cam_id'] for c in saved)}")

    print("\n⚠️  mapping(평면도 대응점)은 아직 비어 있습니다. 웹 UI에서 카메라별로"
          " 지정해야 맵 투영·지표 산출에 포함됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
