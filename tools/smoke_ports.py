#!/usr/bin/env python3
"""smoke_ports.py — 떠 있는 서비스(:8900 · :8910 · RTSP :8554)에 읽기 전용 요청을 병렬로 쏴서
정리 작업(모델·자산 삭제) 전후에 동작이 그대로인지 확인한다.

- GET 만 보낸다(상태를 바꾸지 않는다). openapi.json 에서 경로를 읽어 자동으로 따라간다.
- 경로 인자({session_id}·{cam_id}·{site})는 목록 API 에서 실제 값을 하나 집어 채운다.
- SSE(/api/map/stream)는 헤더 200 + 첫 바이트만 확인하고 끊는다.
- --save 로 기준선(JSON)을 남기고, --compare 로 그 기준선과 상태코드를 비교한다.

  python tools/smoke_ports.py --save /tmp/base.json
  python tools/smoke_ports.py --compare /tmp/base.json     # 달라진 게 있으면 exit 1
"""
import argparse, json, subprocess, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

HOST = "http://127.0.0.1"
PORTS = {8900: "macs-system", 8910: "evac-editor"}
TIMEOUT = 60


def get(url, stream=False):
    t = time.time()
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            n = len(r.read(64)) if stream else len(r.read())
            return r.status, n, time.time() - t
    except urllib.error.HTTPError as e:
        return e.code, 0, time.time() - t
    except Exception as e:  # 타임아웃·연결거부
        return f"ERR:{type(e).__name__}", 0, time.time() - t


def jget(url):
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            return json.load(r)
    except Exception:
        return None


def first(items, *keys):
    if isinstance(items, dict):
        for v in items.values():
            if isinstance(v, list):
                items = v
                break
    if isinstance(items, list) and items:
        it = items[0]
        if isinstance(it, dict):
            for k in keys:
                if it.get(k):
                    return str(it[k])
        elif isinstance(it, str):
            return it
    return None


def targets():
    out = []
    for port in PORTS:
        base = f"{HOST}:{port}"
        spec = jget(f"{base}/openapi.json")
        if not spec:
            out.append((port, "/openapi.json", None))
            continue
        fill = {}
        if port == 8900:
            fill["cam_id"] = first(jget(f"{base}/api/cameras"), "cam_id")
            fill["session_id"] = first(jget(f"{base}/api/sessions"), "session_id", "id")
            fill["drill_session_id"] = first(jget(f"{base}/api/drills"), "session_id", "id")
        else:
            fill["site"] = "default"
        for path, methods in sorted(spec["paths"].items()):
            if "get" not in methods:
                continue
            url = path
            if "{" in path:
                key = path.split("{")[1].split("}")[0]
                val = fill.get("drill_session_id") if path.startswith("/api/drill/") else None
                val = val or fill.get(key)
                if not val:
                    out.append((port, path, "SKIP"))
                    continue
                url = path.replace("{" + key + "}", val)
            out.append((port, path, url))
    return out


def rtsp_check():
    """pm2 에 등록된 송출 이름으로 ffprobe — 영상 스트림이 잡히는지만 본다."""
    res = {}
    try:
        procs = json.loads(subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=20).stdout)
    except Exception:
        return res
    names = [p["name"] for p in procs
             if "rtsp" in (p["pm2_env"].get("pm_exec_path") or "") and p["pm2_env"]["status"] == "online"]

    def probe(n):
        r = subprocess.run(["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-select_streams", "v:0",
                            "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                            f"rtsp://127.0.0.1:8554/{n}"], capture_output=True, text=True, timeout=25)
        return n, (r.stdout.strip() or "FAIL")
    with ThreadPoolExecutor(12) as ex:
        for n, v in ex.map(lambda n: _safe(probe, n), names):
            res[f"rtsp:{n}"] = v
    return res


def _safe(fn, n):
    try:
        return fn(n)
    except Exception as e:
        return n, f"ERR:{type(e).__name__}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save")
    ap.add_argument("--compare")
    ap.add_argument("--no-rtsp", action="store_true")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args()

    tg = targets()
    result = {}

    def run(t):
        port, path, url = t
        key = f"{port}{path}"
        if url is None:
            return key, "ERR:no-openapi", 0, 0
        if url == "SKIP":
            return key, "SKIP", 0, 0
        st, n, dt = get(f"{HOST}:{port}{url}", stream=path.endswith("/stream"))
        return key, st, n, dt
    with ThreadPoolExecutor(16) as ex:
        for key, st, n, dt in ex.map(run, tg):
            result[key] = st
            if not a.quiet:
                print(f"  {str(st):>14}  {dt:5.2f}s {n:>9}B  {key}")
    if not a.no_rtsp:
        r = rtsp_check()
        result.update(r)
        if not a.quiet:
            for k, v in sorted(r.items()):
                print(f"  {v:>14}  {k}")

    bad = {k: v for k, v in result.items() if isinstance(v, str) and (v.startswith("ERR") or v == "FAIL")
           or isinstance(v, int) and v >= 500}
    print(f"\n요청 {len(result)}건 · 5xx/연결실패 {len(bad)}건")
    for k, v in bad.items():
        print(f"  ✗ {v}  {k}")
    if a.save:
        json.dump(result, open(a.save, "w"), ensure_ascii=False, indent=1)
        print(f"기준선 저장: {a.save}")
    rc = 1 if bad else 0
    if a.compare:
        base = json.load(open(a.compare))
        diff = {k: (base.get(k), result.get(k)) for k in set(base) | set(result) if base.get(k) != result.get(k)}
        print(f"기준선 대비 달라진 항목 {len(diff)}건")
        for k, (b, n) in sorted(diff.items()):
            print(f"  Δ {k}: {b} → {n}")
        rc = 1 if diff else 0
    sys.exit(rc)


if __name__ == "__main__":
    main()
