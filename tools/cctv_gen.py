#!/usr/bin/env python3
"""
cctv_gen.py — Gemini(Nano Banana) 이미지 생성. 와이어프레임을 레퍼런스로 첨부하면 구조 고정.

키: 환경변수 GEMINI_API_KEY
예)
  # 구조 고정(와이어프레임 첨부)
  python tools/cctv_gen.py --ref cad/cctv_A_persp.png --out cad/cctv_A_gen.png \
     --prompt "이 와이어프레임 구조를 유지한 포토리얼 CCTV ..."
  # 텍스트만
  python tools/cctv_gen.py --out cad/gen.png --prompt "..."
"""
import argparse, os, sys
from google import genai
from google.genai import types

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ref", action="append", default=[], help="레퍼런스 이미지(여러 장 가능)")
    ap.add_argument("--model", default="gemini-3-pro-image")
    ap.add_argument("--n", type=int, default=1, help="생성 장수")
    args = ap.parse_args()

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY 환경변수 없음")
    client = genai.Client(api_key=key)

    parts = []
    for r in args.ref:
        with open(r, "rb") as f:
            data = f.read()
        mime = "image/png" if r.lower().endswith(".png") else "image/jpeg"
        parts.append(types.Part.from_bytes(data=data, mime_type=mime))
    parts.append(types.Part.from_text(text=args.prompt))

    saved = []
    for i in range(args.n):
        resp = client.models.generate_content(model=args.model, contents=parts)
        got = False
        for cand in resp.candidates:
            for p in cand.content.parts:
                if getattr(p, "inline_data", None) and p.inline_data.data:
                    out = args.out if args.n == 1 else args.out.replace(".png", f"_{i+1}.png")
                    with open(out, "wb") as f:
                        f.write(p.inline_data.data)
                    saved.append(out); got = True
                elif getattr(p, "text", None):
                    print("[model text]", p.text[:200])
        if not got:
            print(f"[warn] {i+1}번째 이미지 없음 (안전필터/거부 가능)")
    print("[saved]", *saved if saved else ["(없음)"])

if __name__ == "__main__":
    main()
