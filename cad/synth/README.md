# cad/synth — 합성 CCTV 테스트영상 생성 모듈

실제 환경 CCTV 영상이 **아직 없으므로**, 도면(`cad/17F.dxf`)을 근거로 분석엔진을
검증할 **합성 테스트영상 + 정답(Ground Truth)** 을 만든다. 합성이라 실제 영상에선
얻기 힘든 **프레임별 완전한 정답**(도면좌표·속도·밀도·이벤트·카메라행렬)이 동봉된다.
→ [북극성 5대 추출정보](../../docs/requirements/CCTV-영상분석-엔진-필수추출정보.md)를
정량 평가할 수 있는 대체 데이터셋.

## 두 가지 생성 방식

| | 추천1 (`make_clip1.py`) | 추천2 (`make_clip2.py`) |
|---|---|---|
| 방식 | 2.5D 컴포지팅(배경+스프라이트) | DXF 압출 3D + z-buffer |
| 카메라 | 단일(camA) | 멀티(camA·camB) |
| 벽 가림 | 없음(painter's order만) | **있음**(벽 뒤 사람 가림) |
| 용도 | 빠른 단일캠 5대기능 검증(M1) | 멀티캠·가림·**ID 병합** GT |

두 방식은 `world.py`의 **같은 피난 시나리오/같은 군중**을 공유 → GT 일관.

## 파이프라인 (어떻게 만들어지나)

```
17F.dxf ──load_segments──> 선분(mm)+범위
   │                          │
   │              camera.py(핀홀+바닥 호모그래피)
   │                          │
scene.py: 원근 배경 + 정적 깊이버퍼(벽)
   │
crowd.py: 도면좌표(m) 위 피난 보행자 시뮬 ──> GT 궤적
   │
compositor.py: 발끝/머리 투영 원근스케일 + (zbuf 가림) ──> 프레임
   │
gt.py: 속도/밀도/개시시점/통과 GT 산출
   ▼
out/  영상(mp4) + GT(json) + 배치도(png)
```

사람 스프라이트는 `extract_sprites.py`가 **레포 자체 검출기(YOLOX MOT20)** 로
기존 샘플영상에서 실제 보행자를 오려 만든다(완전 오프라인). 같은 검출기가
합성영상에서도 잡으므로 검출→추적 파이프라인이 성립한다.

## 실행

```bash
conda activate boosttrack
cd cad/synth
python extract_sprites.py     # 1회: assets_sprites/ 사람 스프라이트 풀
python make_clip1.py          # 추천1 → out/clip1_camA.{mp4,_gt.json,_plan.png}
python make_clip2.py          # 추천2 → out/clip2_cam{A,B}.mp4 + clip2_multicam_gt.json
# 엔진 검증
python -m src.inference -i out/clip1_camA.mp4 -o out/clip1_camA_tracked.mp4 --det_thresh 0.3
```

## GT(json) 구조 핵심
- `camera.H_img_to_world_mm` : 픽셀→도면좌표 **정답 호모그래피**(엔진의 카테고리2 검증).
  역변환 라운드트립 오차 실측 ≈ 평균 0.35 cm.
- `per_frame[].agents[]` : `xy_m`(도면좌표), `foot_px`/`bbox_xywh`(픽셀), `visible`.
- `ground_truth_summary` : 피크 밀도/인원, 개시 프레임, 가상선 통과 프레임, 고유 통과수.
- 추천2 `per_frame[].shared_visible_ids` : 두 카메라 동시 가시 = **ID 병합 정답**.

## 좌표계 / 주의
- 평면도 **남서코너=(0,0) m**, 내부단위 mm. 카메라/구역/출구 좌표는 `world.py`.
- 현 수치는 **데모 근사**(시나리오 검증용). 실제 설치 카메라 파라미터로 재보정 전제.
- 카메라 위치/화각/군중 규모는 `world.py` 상단 상수로 조정.
```bash
# 다른 구역/카메라로 재생성 예: world.py의 build_cameras / SPAWN_RECT / EXIT_XY 수정
```
