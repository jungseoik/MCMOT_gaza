# cad/ — 도면 자산 & 합성 CCTV 테스트영상

17F 평면도(CAD)와, 그 도면을 근거로 분석엔진을 검증할 **합성 CCTV 테스트영상 +
정답(GT)** 을 만드는 작업 폴더. 실제 환경 CCTV 영상이 아직 없어, 도면으로부터
[북극성 5대 추출정보](../docs/requirements/CCTV-영상분석-엔진-필수추출정보.md)
(추적·도면좌표·속도·밀도·이벤트)를 정량 검증할 대체 데이터를 생성한다.

## 폴더 구조

```
cad/
├── README.md                 ← (이 파일)
├── 17F_plan.png              평면도 렌더(깨끗) ※git 추적
├── 17F_plan_scale.png        평면도 + 미터 척도/5m 격자/10m 스케일바 ※git 추적
├── 17F.dwg / 17F.dxf         원본 CAD (대용량 → .gitignore. dxf는 synth 실행에 필요)
├── 17F_Egress Route.pdf      피난동선 원본 (대용량 → .gitignore)
├── ODATrial.tar.gz           ODA 변환 체험판 SDK (3자 바이너리 → .gitignore)
└── synth/                    합성 테스트영상 생성 모듈 (코드만 추적, 산출물은 out/에)
```

> **주의**: `17F.dxf`(18MB)는 용량 때문에 git에서 제외돼 있다. `synth/`를 돌리려면
> 이 파일이 `cad/`에 있어야 한다(로컬 보관, 또는 필요 시 Git LFS로 별도 관리).

## synth/ — 합성영상 모듈 구조

```
synth/
├── README.md          모듈 상세(실행법·GT 구조)
├── camera.py          핀홀 카메라 + 바닥 호모그래피(픽셀↔도면좌표 GT 행렬)
├── scene.py           DXF 로드/벽 압출/원근 배경 + 정적 깊이버퍼
├── raster.py          numpy z-buffer 삼각형 래스터라이저(가림용)
├── crowd.py           도면좌표(m) 위 피난 보행자 시뮬 → GT 궤적
├── gt.py              속도/밀도/피난개시/가상선통과 GT 산출
├── compositor.py      배경+스프라이트 원근 합성(+벽 가림)
├── world.py           공유 시나리오(카메라·군중·구역·출구) 한 곳에서 정의
├── extract_sprites.py 레포 검출기로 샘플영상에서 실제 보행자 RGBA 추출
├── make_clip1.py      [추천1] 2.5D 컴포지팅 단일캠 클립
├── make_clip2.py      [추천2] DXF 압출 3D z-buffer 멀티캠 클립
├── assets_sprites/    추출된 사람 스프라이트 풀(person_*.png) ※git 추적
└── out/               생성 결과(mp4/GT json/프리뷰) ※.gitignore (재생성 가능)
```

## 빠른 실행

```bash
conda activate boosttrack
cd cad/synth
python extract_sprites.py     # (1회) 사람 스프라이트 풀 생성
python make_clip1.py          # 추천1 → out/clip1_camA.{mp4,_gt.json,_plan.png}
python make_clip2.py          # 추천2 → out/clip2_cam{A,B}.mp4 + clip2_multicam_gt.json
# 엔진으로 추적 검증
cd ../..
python -m src.inference -i cad/synth/out/clip1_camA.mp4 \
       -o cad/synth/out/clip1_camA_tracked.mp4 --det_thresh 0.3
```

## 좌표계 / 카메라
- 평면도 **남서코너 = (0,0) m**, 내부단위 mm. 전체 약 **73.2 m × 69.1 m**.
- 카메라/군중/구역/출구 좌표는 `synth/world.py` 상단에서 조정(현재 데모 근사값).
- 카메라는 천장 마운트 가정: 높이 4.6 m, **하방 틸트 47°**(내려다보는 CCTV 시점).
- 정밀 정합은 추후 실제 설치 카메라 파라미터로 재보정 전제.

## 도구(`tools/`)
- `tools/cctv_synth.py` : 도면→가상 CCTV 시점 구조 와이어프레임/배치도(이미지생성기 입력용).
- `tools/cctv_gen.py`   : Gemini로 와이어프레임 기반 포토리얼 CCTV 생성(GEMINI_API_KEY 필요).
