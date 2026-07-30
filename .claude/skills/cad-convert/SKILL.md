---
name: cad-convert
description: >-
  CAD 도면 변환 파이프라인. AutoCAD DWG를 열 수 있는 DXF로 바꾸고, DXF를 평면도 PNG와
  미터 척도(격자·스케일바) PNG로 렌더한다. "이 dwg 변환해줘", "캐드 파일 열 수 있게 바꿔줘",
  "도면 png로 뽑아줘", "도면에 거리 척도 표시해줘", "평면도 이미지 만들어줘", "dxf 렌더",
  "figure/도면 스케일 이미지" 같은 요청에 사용. DWG→DXF는 ODAFileConverter(GUI)를 xvfb로
  헤드리스 실행, DXF→PNG는 ezdxf+matplotlib. tools/cad_convert.py 위에서 동작한다 —
  새로 구현하지 말 것. (CAD conversion, DWG, DXF, floor plan, scale, ODA, ezdxf)
---

# CAD 변환 (DWG → DXF → PNG/척도)

AutoCAD `.dwg`를 열 수 있는 `.dxf`로 변환하고, 그 도면을 (1)깨끗한 평면도 PNG,
(2)미터 척도·격자·스케일바가 붙은 PNG로 렌더한다. 정합(호모그래피)·합성영상
(`cad/synth/`)이 모두 이 좌표계를 공유한다. **아래 커밋된 스크립트를 쓸 것.**

## 변환 백엔드 2가지 (오픈소스 + ODA)
| 엔진 | 라이선스 | 설치 | 정합도 | 비고 |
|------|----------|------|--------|------|
| **libredwg** (`dwg2dxf`) | 오픈소스(GPL) | apt(있는 배포판) / **소스빌드**(Ubuntu 24.04) | 보통 | 주입 불필요 |
| **ODAFileConverter** | 독점 프리웨어 | **수동 .deb 주입**(`dpkg -i`) | 최상 | 최신 DWG 안정, xvfb 필요 |

> **ODA는 오픈소스가 아니다.** 무료지만 EULA상 재배포 금지 → 레포에 못 담고,
> [opendesign.com](https://www.opendesign.com/guestfiles/oda_file_converter)에서
> 직접 받아 **외부 주입**(`sudo dpkg -i ODAFileConverter_*.deb`)해야 한다. 이 머신도
> 그렇게 설치됨(dpkg -i, apt 아님). `cad/ODATrial.tar.gz`는 SDK라 변환기와 무관.
>
> **libredwg는 Ubuntu 24.04(noble) apt엔 없다** → `setup_cad_convert.sh`가 GitHub에서
> 소스 빌드한다(수 분, 검증됨: dwg2dxf 0.14로 17F.dwg 변환 확인). Debian 등은 apt로 설치.
> `dwg2dxf`(변환기 CLI)는 `--engine auto`가 ODA 있으면 ODA, 없으면 libredwg를 쓴다.

## 새 서버에서 처음부터 (설치 → 변환)
```bash
# 1) 설치. 인터넷 있으면 libredwg는 apt/소스빌드로 자동:
bash tools/setup_cad_convert.sh                                   # 온라인, OSS 자동
ODA_DEB=/store/ODAFileConverter_QT6_lnxX64_*.deb bash tools/setup_cad_convert.sh  # +ODA
# 오프라인/고정 재현 — "미리 챙긴 파일" 주입(인터넷·빌드 불필요):
LIBREDWG_BIN=/store/dwg2dxf bash tools/setup_cad_convert.sh       # OSS 정적바이너리 복사
# PYTHON 지정: PYTHON=~/miniconda3/envs/boosttrack/bin/python bash tools/setup_cad_convert.sh

# 2) 변환 (아래 1)·2)단계)
```

### 오프라인 재현 — 무엇을 보관하나
| 백엔드 | 보관물 | 이유 |
|--------|--------|------|
| **libredwg** | `dwg2dxf` **정적 바이너리**(~50MB, libc/libm만 의존) | 복사만으로 동작(검증됨). 원본 소스/설치파일 불필요. GPL이라 재배포 자유 |
| **ODA** | `ODAFileConverter_*.deb` | 독점 → 레포 불가, 사설 저장소(Git LFS/내부 파일서버/버킷)에 보관 후 `ODA_DEB=`로 주입 |
> 인터넷이 되는 서버면 libredwg는 보관 없이 스크립트가 빌드/설치한다. 오프라인이거나
> 버전을 고정하고 싶을 때만 위 파일을 챙긴다. **git 본체엔 넣지 말 것**(용량·라이선스).
- **이 개발 머신엔 ODA가 이미 설치됨**(`/usr/bin/ODAFileConverter` v27.1.0.0) — 재설치 금지.
- 파이썬: `~/miniconda3/envs/boosttrack/bin/python`(`ezdxf`,`matplotlib`). 한글 폰트
  `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`.

## 1) DWG → DXF (또는 반대)
```bash
PY=~/miniconda3/envs/boosttrack/bin/python
$PY tools/cad_convert.py dwg2dxf --in cad/17F.dwg --out cad/           # --engine auto
$PY tools/cad_convert.py dwg2dxf --in a.dwg --out out/ --engine libredwg   # OSS 강제
# → <출력폴더>/<이름>.dxf 생성 (ODA 출력버전 기본 ACAD2018, --out-ver 로 변경)
```
- `--engine`: `auto`(기본, ODA>libredwg) / `oda` / `libredwg`.
- ODA는 **폴더 단위** GUI라 스크립트가 입력을 임시폴더에 넣고 `xvfb-run`으로 호출한다.
  libredwg는 단일파일 CLI(`dwg2dxf -o out.dxf in.dwg`)라 xvfb 불필요.
- 입력이 `.dxf`면 ODA 엔진으로 DWG 역변환(libredwg는 DWG→DXF만).

## 2) DXF → PNG (평면도 + 미터 척도)
```bash
$PY tools/cad_convert.py dxf2png --dxf cad/17F.dxf --out-prefix cad/17F_plan
# → cad/17F_plan.png        (깨끗한 도면, 축/격자 없음)
#   cad/17F_plan_scale.png  (미터 좌표축 + 5m 격자 + 10m 스케일바)
# 격자 간격 변경: --grid-m 2 ,  해상도: --dpi 300 ,  단위 강제: --units m
```
- **단위 자동감지**: DXF `$INSUNITS`로 mm/cm/m/inch/ft를 판별해 미터 척도를 맞춘다.
  감지 실패 시 mm로 가정하며, 틀리면 `--units {mm,cm,m,in,ft}`로 강제한다.
- **블록(INSERT) 자동 전개**: 형상이 블록참조 안에 있어도 재귀 전개해 렌더한다
  (`virtual_entities`). 17F는 블록 없이 top-level 형상이라 전개 0개.
- 좌표계: 평면도 **남서(좌하단) 코너 = (0,0) m**. 범위는 `$EXTMIN/$EXTMAX` 헤더 우선,
  없으면 형상에서 계산. 17F는 약 73.2 × 69.1 m(mm 단위).
- 지원 엔티티: LINE / LWPOLYLINE / POLYLINE / ARC / CIRCLE / ELLIPSE / SPLINE
  (TEXT·치수는 렌더 안 함 — 외곽/구획선 위주).

## 관련
- 이 좌표계를 그대로 쓰는 합성 CCTV 영상: `cad/synth/`(README 참조).
- 도면→가상 CCTV 시점 와이어프레임: `tools/cctv_synth.py`.
