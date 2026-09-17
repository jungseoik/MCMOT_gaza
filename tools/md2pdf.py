#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""마크다운 보고서 → 흰 배경 PDF (같은 폴더에 저장).

왜 자체 구현인가: 이 서버에 pandoc·wkhtmltopdf·weasyprint 가 없다. 이미 깔려 있는
python-markdown + Playwright(Chromium) 만으로 만든다 — 추가 설치 없이 재현된다.

수식: 문서에 쓰인 LaTeX 는 $$...$$ 블록 두어 개뿐이라 MathJax 를 끌어오지 않고
유니코드로 치환한다. 모르는 명령이 남으면 **원문 LaTeX 를 그대로** 코드체로 보여준다
(조용히 깨뜨리지 않는다).

사용:
    python tools/md2pdf.py docs/deliverables/*/README.md
    python tools/md2pdf.py <md> -o <out.pdf>
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

import markdown

# ------------------------------------------------------------------ 수식
_SUB = {
    r"\mathrm": "", r"\text": "", r"\left": "", r"\right": "", r"\!": "",
    r"\qquad": "\u2003\u2003", r"\quad": "\u2003", r"\,": "\u2009",
    "\\;": "\u2009", "\\:": "\u2009", "\\ ": " ",
    r"\cdot": "·", r"\times": "×", r"\geq": "≥", r"\leq": "≤",
    r"\rho": "ρ", r"\Delta": "Δ", r"\sum": "Σ", r"\int": "∫",
    r"\max": "max", r"\min": "min", r"\infty": "∞",
}
_SUPS = {"0":"⁰","1":"¹","2":"²","3":"³","4":"⁴","5":"⁵","6":"⁶","7":"⁷","8":"⁸","9":"⁹"}


def _tex_to_text(tex: str) -> tuple[str, bool]:
    """LaTeX → 유니코드 근사. (텍스트, 완전변환여부)."""
    t = tex
    t = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", t)
    for k, v in _SUB.items():
        t = t.replace(k, v)
    t = re.sub(r"\^\{?(\d)\}?", lambda m: _SUPS[m.group(1)], t)      # ^2 → ²
    t = re.sub(r"_\{([^{}]*)\}", r"_\1", t)                          # _{x} → _x
    t = t.replace("{", "").replace("}", "")
    t = re.sub(r"[ \t]+", " ", t).strip()
    return t, ("\\" not in t)


def _render_math(md_text: str) -> str:
    """$$블록$$ · $인라인$ 을 HTML 로 미리 바꾼다 (markdown 변환 전).

    블록을 먼저 처리해야 한다 — 인라인 규칙이 $$ 를 빈 수식 두 개로 잘라먹는다.
    """
    def block(m):
        tex = m.group(1).strip()
        txt, clean = _tex_to_text(tex)
        body = f'<div class="formula">{html.escape(txt)}</div>'
        if not clean:        # 변환 실패 — 원문을 그대로 보인다
            body += f'<div class="formula-raw">{html.escape(tex)}</div>'
        return "\n\n" + body + "\n\n"
    out = re.sub(r"\$\$(.+?)\$\$", block, md_text, flags=re.S)

    # 인라인 — 줄바꿈을 넘지 않고, 여는 $ 뒤에 공백이 없을 때만 (금액 표기 오인 방지)
    def inline(m):
        txt, _ = _tex_to_text(m.group(1))
        return f'<span class="form-i">{html.escape(txt)}</span>'
    out = re.sub(r"(?<!\$)\$(?!\s)([^$\n]+?)\$(?!\$)", inline, out)
    return out


CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }
* { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { background:#fff; color:#1a1a1a; margin:0;
  font-family:"NanumGothic","Noto Sans CJK KR","Malgun Gothic",sans-serif;
  font-size:10.5pt; line-height:1.72; }
h1 { font-size:20pt; margin:0 0 6pt; padding-bottom:8pt;
     border-bottom:2.5pt solid #1a1a1a; letter-spacing:-.01em; }
h2 { font-size:14pt; margin:22pt 0 8pt; padding-top:10pt;
     border-top:1pt solid #d0d0d0; page-break-after:avoid; }
h3 { font-size:11.5pt; margin:15pt 0 6pt; color:#000; page-break-after:avoid; }
h4 { font-size:10.5pt; margin:12pt 0 5pt; color:#333; page-break-after:avoid; }
p  { margin:6pt 0; }
strong { color:#000; }
table { border-collapse:collapse; width:100%; margin:9pt 0; font-size:9.5pt;
        page-break-inside:avoid; }
th,td { border:0.6pt solid #b8b8b8; padding:4.5pt 7pt; }
th { background:#f0f0f0; font-weight:700; }
tbody tr:nth-child(even) { background:#fafafa; }
img { max-width:100%; display:block; margin:10pt auto; page-break-inside:avoid; }
blockquote { margin:10pt 0; padding:8pt 12pt; background:#f5f7f9;
  border-left:3pt solid #666; page-break-inside:avoid; }
blockquote h2, blockquote h3 { border:none; margin:0 0 4pt; padding:0; }
code { font-family:"NanumGothicCoding","DejaVu Sans Mono",monospace;
  font-size:9pt; background:#f2f2f2; padding:1pt 3pt; border-radius:2pt; }
pre { background:#f7f7f7; border:0.6pt solid #ddd; border-radius:3pt;
  padding:8pt 10pt; overflow:visible; white-space:pre-wrap; word-break:break-word;
  page-break-inside:avoid; }
pre code { background:none; padding:0; font-size:8.7pt; line-height:1.5; }
em { color:#444; }
hr { border:none; border-top:0.6pt solid #ccc; margin:14pt 0; }
.formula { text-align:center; font-size:11pt; margin:10pt 0; padding:8pt;
  background:#f7f9fb; border:0.6pt solid #dde3e8; border-radius:3pt;
  font-family:"DejaVu Serif","NanumMyeongjo",serif; }
.form-i { font-family:"DejaVu Serif","NanumMyeongjo",serif; }
.formula-raw { text-align:center; font-size:8pt; color:#777; margin-top:-6pt;
  font-family:"DejaVu Sans Mono",monospace; }
a { color:#1a4f8a; text-decoration:none; }
"""


def build_html(md_path: Path) -> str:
    raw = md_path.read_text(encoding="utf-8")
    body = markdown.markdown(
        _render_math(raw),
        extensions=["tables", "fenced_code", "sane_lists", "md_in_html"])
    # 상대 이미지 경로 → 절대 file:// (Chromium 이 로컬 파일을 찾게)
    base = md_path.parent.resolve()
    body = re.sub(r'src="(?!https?://|file://|data:)([^"]+)"',
                  lambda m: f'src="file://{(base / m.group(1)).resolve()}"', body)
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            f'<title>{html.escape(md_path.parent.name)}</title>'
            f'<style>{CSS}</style></head><body>{body}</body></html>')


def to_pdf(md_path: Path, out: Path | None = None) -> Path:
    from playwright.sync_api import sync_playwright
    out = out or md_path.with_suffix(".pdf")
    tmp = md_path.parent / f".{md_path.stem}.print.html"
    tmp.write_text(build_html(md_path), encoding="utf-8")
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True)
            pg = b.new_page()
            pg.goto(f"file://{tmp.resolve()}", wait_until="load")
            pg.wait_for_timeout(700)               # 폰트·이미지 반영
            pg.pdf(path=str(out), format="A4", print_background=True,
                   display_header_footer=True,
                   header_template='<div></div>',
                   footer_template=(
                       '<div style="width:100%;font-size:8px;color:#888;'
                       'padding:0 16mm;font-family:NanumGothic,sans-serif;'
                       'display:flex;justify-content:space-between;">'
                       '<span>MACS-EVAC · 검증 결과보고서</span>'
                       '<span><span class="pageNumber"></span>'
                       ' / <span class="totalPages"></span></span>'
                       '</div>'),
                   margin={"top": "18mm", "bottom": "20mm",
                           "left": "16mm", "right": "16mm"})
            b.close()
    finally:
        tmp.unlink(missing_ok=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="마크다운 보고서 → 흰 배경 PDF")
    ap.add_argument("md", nargs="+", help="변환할 .md (여러 개 가능)")
    ap.add_argument("-o", "--out", help="출력 경로 (md 가 하나일 때만)")
    a = ap.parse_args()
    paths = [Path(p) for p in a.md]
    if a.out and len(paths) != 1:
        print("-o 는 md 가 하나일 때만 쓸 수 있습니다", file=sys.stderr)
        return 2
    for p in paths:
        if not p.is_file():
            print(f"없는 파일: {p}", file=sys.stderr)
            return 1
        out = to_pdf(p, Path(a.out) if a.out else None)
        print(f"{p}  →  {out}  ({out.stat().st_size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
