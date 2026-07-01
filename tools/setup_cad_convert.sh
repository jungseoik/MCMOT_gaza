#!/usr/bin/env bash
# setup_cad_convert.sh — 새 서버에서 CAD 변환 파이프라인을 처음부터 구성한다.
#   1) 파이썬 렌더 의존성(ezdxf, matplotlib) + 한글 폰트
#   2) 오픈소스 변환 백엔드 libredwg(dwg2dxf) — apt 자동설치(주입 불필요)
#   3) (선택) ODA File Converter — 독점 프리웨어라 자동설치 불가.
#      ODA_DEB=<로컬 .deb 경로 또는 http URL> 를 주면 설치, 없으면 안내만 출력.
#
# 사용:
#   bash tools/setup_cad_convert.sh                 # OSS(libredwg)만 설치
#   ODA_DEB=/path/ODAFileConverter_*.deb bash tools/setup_cad_convert.sh
#   PYTHON=~/miniconda3/envs/boosttrack/bin/python bash tools/setup_cad_convert.sh
#
# 왜 ODA는 자동설치가 안 되나:
#   ODA File Converter는 Open Design Alliance의 무료(프리웨어)지만 *오픈소스가 아니고*
#   EULA상 재배포가 금지된다. 그래서 레포에 담을 수 없고 opendesign.com에서 직접 받아
#   외부에서 주입해야 한다. 정합도가 최상이라 정밀 변환엔 권장.
set -euo pipefail

PYTHON="${PYTHON:-python3}"
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

echo "== [1/3] 파이썬 렌더 의존성 =="
"$PYTHON" -m pip install --quiet --upgrade ezdxf matplotlib && echo "  ezdxf/matplotlib OK"

echo "== [2/3] 시스템 의존성(폰트 + ODA용 xvfb/xcb + 빌드도구) =="
if command -v apt-get >/dev/null 2>&1; then
  $SUDO apt-get update -y
  $SUDO apt-get install -y \
    fonts-noto-cjk \
    xvfb libxkbcommon-x11-0 libxcb-icccm4 libxcb-keysyms1 libxcb-xkb1 \
    git gcc make autoconf automake libtool pkg-config texinfo || \
    echo "  [warn] 일부 패키지 설치 실패 — 배포판 패키지명 확인"
else
  echo "  [warn] apt-get 없음 — 위 의존성을 배포판 방식으로 설치하라"
fi

echo "== [2.5/3] OSS 변환 백엔드 libredwg(dwg2dxf) =="
if command -v dwg2dxf >/dev/null 2>&1; then
  echo "  이미 있음: $(command -v dwg2dxf)"
elif command -v apt-get >/dev/null 2>&1 && apt-cache show libredwg-tools >/dev/null 2>&1; then
  echo "  apt로 설치(libredwg-tools 제공 배포판: Debian/일부 Ubuntu)"
  $SUDO apt-get install -y libredwg-tools
else
  # Ubuntu 24.04(noble) 등 apt에 없는 배포판 → 소스 빌드
  echo "  apt에 libredwg 없음 → 소스 빌드(GitHub). 수 분 소요."
  BUILD="${LIBREDWG_SRC:-/tmp/libredwg-build}"
  rm -rf "$BUILD"
  if git clone --depth 1 https://github.com/LibreDWG/libredwg.git "$BUILD"; then
    ( cd "$BUILD" && sh autogen.sh && ./configure --disable-bindings --disable-shared \
        && make -j"$(nproc)" && $SUDO make install ) \
      && $SUDO ldconfig 2>/dev/null || echo "  [warn] libredwg 빌드 실패 — ODA를 대신 쓰라"
  else
    echo "  [warn] GitHub 접근 불가 — libredwg 생략(ODA 사용)"
  fi
fi

echo "== [3/3] ODA File Converter (선택, 독점 프리웨어) =="
if command -v ODAFileConverter >/dev/null 2>&1; then
  echo "  이미 설치됨: $(command -v ODAFileConverter)"
elif [ -n "${ODA_DEB:-}" ]; then
  deb="$ODA_DEB"
  case "$ODA_DEB" in
    http*://*) deb="$(mktemp --suffix=.deb)"; echo "  다운로드: $ODA_DEB"; \
               curl -fSL "$ODA_DEB" -o "$deb" || wget -O "$deb" "$ODA_DEB" ;;
  esac
  echo "  설치: $deb"
  $SUDO dpkg -i "$deb" || $SUDO apt-get -f install -y
else
  cat <<'EOF'
  [건너뜀] ODA 미설치. OSS(libredwg)만으로도 DWG→DXF는 된다.
  ODA를 쓰려면(정합도 최상, 최신 DWG 안정):
    1) https://www.opendesign.com/guestfiles/oda_file_converter 에서
       "ODA File Converter" Linux .deb(QT6 x64)를 받는다(무료, EULA 동의).
    2) 그 파일 경로로 재실행:  ODA_DEB=/받은/경로.deb bash tools/setup_cad_convert.sh
EOF
fi

echo
echo "== 설치 결과 =="
command -v dwg2dxf        >/dev/null 2>&1 && echo "  libredwg(dwg2dxf): $(command -v dwg2dxf)"   || echo "  libredwg: 없음"
command -v ODAFileConverter >/dev/null 2>&1 && echo "  ODA: $(command -v ODAFileConverter)"      || echo "  ODA: 없음(선택)"
echo
echo "이제 변환:"
echo "  $PYTHON tools/cad_convert.py dwg2dxf --in <파일>.dwg --out <출력폴더>   # 엔진 auto"
echo "  $PYTHON tools/cad_convert.py dxf2png --dxf <파일>.dxf --out-prefix <접두어>"
