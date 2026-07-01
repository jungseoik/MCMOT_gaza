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

## 환경 (이미 설치됨 — 재설치 금지)
- **파이썬**: `~/miniconda3/envs/boosttrack/bin/python` (`ezdxf`, `matplotlib`).
- **ODAFileConverter**: 시스템 설치됨(`/usr/bin/ODAFileConverter`, v27.1.0.0). GUI 앱이라
  **`xvfb-run`으로 헤드리스** 실행한다.
- **한글 폰트**: `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`.

### 새 환경에 처음 설치할 때만
```bash
# 1) ODA File Converter .deb 설치 (ODA 사이트에서 QT6 Linux .deb 받아서)
sudo dpkg -i ODAFileConverter_QT6_lnxX64_8.3dll_27.1.deb || sudo apt -f install -y
# 2) GUI 헤드리스 실행에 필요한 Qt/xcb 의존성
sudo apt install -y xvfb libxkbcommon-x11-0 libxcb-icccm4 libxcb-keysyms1 libxcb-xkb1
# 확인
which ODAFileConverter xvfb-run
```
> 주의: `cad/ODATrial.tar.gz`는 ODA **SDK 체험판**이지 위 변환기 .deb가 아니다.
> 변환기는 위 .deb로 별도 설치한다.

## 1) DWG → DXF (또는 반대)
```bash
PY=~/miniconda3/envs/boosttrack/bin/python
$PY tools/cad_convert.py dwg2dxf --in cad/17F.dwg --out cad/
# → cad/17F.dxf 생성 (출력버전 기본 ACAD2018, --out-ver 로 변경)
```
- ODA는 **폴더 단위** 변환기라 스크립트가 입력파일을 임시폴더에 넣고
  `xvfb-run ODAFileConverter <in> <out> <ver> <type> <recurse> <audit> <filter>` 형태로 호출한다.
- 입력이 `.dxf`면 자동으로 DWG로 역변환한다.

## 2) DXF → PNG (평면도 + 미터 척도)
```bash
$PY tools/cad_convert.py dxf2png --dxf cad/17F.dxf --out-prefix cad/17F_plan
# → cad/17F_plan.png        (깨끗한 도면, 축/격자 없음)
#   cad/17F_plan_scale.png  (미터 좌표축 + 5m 격자 + 10m 스케일바)
# 격자 간격 변경: --grid-m 2 ,  해상도: --dpi 300
```
- 좌표계: 평면도 **남서(좌하단) 코너 = (0,0) m**, DXF 내부단위 mm. 17F는 약 73.2 × 69.1 m.
- 렌더는 `$EXTMIN/$EXTMAX`(DXF 헤더)로 범위를 잡고 `ezdxf`로 선형 엔티티를 평탄화한다.

## 관련
- 이 좌표계를 그대로 쓰는 합성 CCTV 영상: `cad/synth/`(README 참조).
- 도면→가상 CCTV 시점 와이어프레임: `tools/cctv_synth.py`.
