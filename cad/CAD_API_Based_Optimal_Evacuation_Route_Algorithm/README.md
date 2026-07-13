# CAD API 기반 최적 피난동선 알고리즘 (TravelDistance_Analyzer)

삼성화재가 자체 개발·사용 중인 **최적 보행(피난)경로 산출 매크로**. AutoCAD 평면도를
격자화(mesh)해 벽체·장애물을 `1`, 이동가능 공간을 `0`으로 두고, 임의 지점에서 Exit까지의
최단 피난거리를 산출한다. 회의 이후 소스를 공유받아 **구조·구현범위를 당사(PIA)에서
분석**하기 위해 이 폴더에 보관한다.

> 이 문서는 공유받은 소스(`TravelDistance_Analyzer.zip` 내 `Class1.cs`, 1,131줄)를
> 직접 열어 읽고, 설명과 실제 구현을 대조해 작성했다. **대조 결과 §4에 정리.**

## 1. 폴더 구성

```
CAD_API_Based_Optimal_Evacuation_Route_Algorithm/
├── README.md                    ← (이 문서)
├── Egress_Review-Test.dwg       테스트 도면(피난검토용, 1.8MB)
└── TravelDistance_Analyzer.zip  공유받은 원본 소스(9.4MB, VS 솔루션 전체)
    └── TravelDistance_Analyzer/
        ├── TravelDistance_Analyzer.slnx
        ├── packages/AutoCAD.NET(.Core/.Model) 22.0.0/   ← ObjectARX 관리형 API
        └── TravelDistance_Analyzer/
            ├── Class1.cs                  ★ 전체 로직(EvacRouteCommands)
            ├── TravelDistance_Analyzer.csproj  (.NET Framework 4.8, x64, Library)
            ├── packages.config
            └── bin/Debug/TravelDistance_Analyzer.dll  (빌드 산출물)
```

## 2. 구현 개요

- **형태**: C# **.NET Framework 4.8 클래스 라이브러리(DLL)**. AutoCAD에 `NETLOAD`로 적재 후
  명령어처럼 실행하는 ObjectARX 관리형(.NET) 플러그인.
- **의존**: `AutoCAD.NET` NuGet **22.0.0**(AcMgd/AcCoreMgd/AcDbMgd 등, net46 라이브러리).
- **진입점(커맨드) 2개** — 모두 `EvacRouteCommands` 클래스의 `[CommandMethod]`:

| 커맨드 | 출발점 방식 | 개요 |
|--------|-------------|------|
| **`EVAC_Occupant`** | **수동 지정** | `Evac_Occupant` 레이어에 미리 찍어둔 꼭짓점마다 최근접 Exit까지 경로 산출 |
| **`EVAC_WorstN`** | **자동 Worst 추출** | 범위 전체를 멀티해상도로 훑어 **가장 먼(worst) N개소**를 분산 추출 |

- **핵심 상수**: 격자 셀 `CELL_SIZE = 50mm`, 장애물 이격 `CLEARANCE = 304.8mm`(=1ft).

### 사전 전제(레이어 규약) — 순수 평면도만으론 동작 안 함
코드는 평면도에 **특정 레이어가 미리 태깅되어 있어야** 동작한다.
- `Evac_Exit` : **Line** 엔티티. 각 선분의 중점을 피난계단(Exit) 위치로 사용.
- `Evac_Occupant` : (EVAC_Occupant 전용) Polyline/Line 꼭짓점 = 검토 출발점.
- 결과 레이어 `Evac_Path_Pass`/`Evac_Path_Fail`(등)은 장애물 수집에서 자동 제외.

## 3. 처리 파이프라인 (실제 코드 흐름)

```
① 범위 지정        사용자가 두 코너를 찍어 탐색 사각형 지정
② Exit 수집        GetExitPoints(): 'Evac_Exit' 레이어 Line 중점 → 다중 출발원(source)
③ 장애물 수집      CollectObstacles(): ModelSpace 순회, Block 재귀분해(최대 depth 10),
                    변환행렬 누적. Line/Polyline/Polyline2d/Arc(12분할)/Circle(16분할) →
                    선분 리스트. Hatch·결과레이어는 skip. (문/불필요요소 자동삭제는 없음)
④ 격자화(mesh)     BuildObstacleGrid2(): 각 선분 bbox+margin 셀만 검사, 셀중심~선분 거리 <
                    CLEARANCE(304.8mm)면 장애물(true). → bool[cols,rows]  (= 벽1/공간0)
⑤ 최단거리         RunDijkstra2(): ★멀티소스 다익스트라. 모든 Exit를 dist=0으로 동시 시드,
                    8방향(직교 cell, 대각 cell·√2), SortedSet 우선순위큐. Exit로부터의
                    최단 피난거리장(dist[])과 역추적 정보(prev[]) 산출.
⑥ 경로 역추적      TracePath(): prev 따라 Exit까지 역추적 후 **String-Pulling**
                    (HasLineOfSight, Bresenham + 대각 코너커팅 방지)으로 경로 평활화.
⑦ 그리기          DrawAllResults(): Polyline(경로) + 시작원(Circle) + 거리 텍스트(DBText)
                    + Wipeout(배경가림). Pass/Fail 레이어로 분리(색: 초록/빨강).
```

**출발점 자동추출(EVAC_WorstN)** 은 2단계 멀티해상도다:
1. **Phase1(저해상도, 500mm 셀)**: 전 범위 다익스트라 → 거리 상위 ~20% 후보 셀.
2. **Phase2(고해상도, 50mm 셀)**: 후보영역만 정밀 다익스트라 → 각 후보의 국소 최대거리 지점.
3. **분산 Top N**: 이미 뽑힌 지점과 **최소 이격거리**(대각선의 15% 또는 5m 중 큰 값,
   개수 미달 시 0.7배씩 완화) 이상 떨어진 지점만 선택 → 한 구석에 몰리지 않게 분산.

### 거리 기준(Pass/Fail)
- 사용자가 **기준 피난거리**(기본 30m)를 입력. `distMm ≤ threshold`면 Pass(초록),
  초과면 Fail(빨강). 거리 텍스트 높이(기본 750mm)도 입력받음.
- 그려지는 **거리 텍스트는 AutoCAD 필드**(`%<\AcObjProp … .Length …>%`)로,
  실제 그려진 Polyline의 길이를 표시한다.

## 4. 설명 ↔ 실제 코드 대조 (검증 결과)

전달받은 표를 실제 소스와 대조했다. **대부분 정확**하며, 아래 몇 가지만 보정·보강이 필요하다.

| 항목 | 전달 설명 | 실제 코드 | 판정 |
|------|-----------|-----------|------|
| 입력 | AutoCAD 평면도 | 평면도 + **사전 레이어 태깅**(`Evac_Exit`,`Evac_Occupant`) 필수 | ⚠ **보강** |
| 전처리 | 문 등 삭제, mesh 변환 | mesh 변환 ✓. **문 삭제는 자동화 없음**(Hatch만 자동 skip) → 수동 전처리로 추정 | ⚠ **보강** |
| 장애물 clearance | 약 300mm | **정확히 304.8mm(=1ft)** | ✅ (수치 확정) |
| 경로 산출 | 출발→Exit 최적경로 | ✅ 그대로 | ✅ |
| 알고리즘 | 다익스트라 "추정" | **확정: 멀티소스 다익스트라**(8방향·SortedSet PQ) + String-Pulling 평활화 | ✅ **확정** |
| 출력 | polyline + 거리텍스트 | ✅ Polyline + Circle + **필드 기반 거리텍스트** + Wipeout, Pass/Fail 레이어 분리 | ✅ |
| 출발점 | worst 추출 또는 수동 | ✅ 두 커맨드로 구현. 단 worst는 **2단계 멀티해상도 + 분산 로직** | ✅ (상세 보강) |
| Exit 처리 | 여러 Exit 중 최근접 | ✅ **멀티소스 다익스트라**라 자연히 최근접 Exit까지 거리 산출 | ✅ |
| 구현형태 | C# DLL, 로드 후 명령 | ✅ .NET 4.8 DLL, `[CommandMethod]` | ✅ |
| AutoCAD 버전 | 2022 | 참조는 **AutoCAD.NET 22.0.0**(net46 lib). 패키지버전≠제품연도일 수 있어 확인 권장 | ⚠ **확인** |

### 실제로 열어보고 발견한 코드상 결함·주의점
1. **레이어 생성 버그(EVAC_Occupant)**: `FindWorstEvacRoute()`가 Fail 레이어를
   `"spPath_Fail"`(색1)로 만들지만(줄 109), 정작 그릴 땐 `"Evac_Path_Fail"`을 참조
   (`DrawAllResults`, 줄 415). → **오타로 보이며**, Fail 경로가 색 지정 안 된 자동생성
   레이어에 그려질 수 있음. (EVAC_WorstN 쪽은 `"Evac_Path_Fail"`로 올바름, 줄 370.)
2. **미사용(dead) 코드**: `DrawWorst5Results()`(`Evac_Worst5` 레이어, `#순위` 라벨)가
   정의돼 있으나 어디서도 호출 안 됨 — EVAC_WorstN도 `DrawAllResults`를 재사용(줄 387).
   → `Evac_Worst5` 레이어와 순위 표기는 현재 산출물에 안 나옴.
3. **거리 표시 불일치 가능성**: Pass/Fail 판정은 다익스트라 격자거리(`distMm`)로 하지만,
   화면 텍스트는 **평활화된 Polyline의 실제 길이 필드**를 쓴다. String-Pulling으로
   경로가 짧아지므로 **판정 기준거리와 표시 숫자가 미세하게 다를 수 있음**.
4. **`BuildConnectedComponents`(연결요소 BFS)** 도 정의만 되어 있고 호출부 없음(dead).
5. **격자 상한** 5,000,000셀(EVAC_Occupant). 50mm 셀 기준 약 12.5m×12.5m를 넘는 큰
   범위는 Phase 분할(WorstN) 없이는 부담 → 대형 평면도는 WorstN 경로 권장.

## 5. PIA 관점 활용 메모

- 이 매크로의 **출력(피난거리·최적경로 Polyline)** 은 우리 북극성 지표 중
  **EPFI(예상 피난시간)·경로 길이**의 *정답/기준(reference)* 으로 쓸 수 있다.
  → [삼성화재 4대지표 요구사항](../../docs/requirements/삼성화재-피난훈련-정량평가-4대지표-요구사항.md).
- 좌표계·단위(mm)가 우리 `cad/17F.dxf` 파이프라인과 동일 → `Evac_Exit`/장애물 레이어를
  그대로 재활용해 **동일 평면도에서 알고리즘 경로 vs 실측 추적 경로** 비교 가능.
- **재구현 관점**: 순수 기하+격자+다익스트라라 AutoCAD 없이 Python으로 이식 가능
  (장애물 선분은 우리 `tools/cad_convert.py`의 `_load_segments`가 이미 DXF에서 추출).
  AutoCAD 종속부는 ①엔티티→선분 수집 ②결과 Polyline/텍스트 그리기뿐.

## 6. 빌드/실행 (윈도우 + AutoCAD 필요)

> 이 저장소(리눅스)에선 빌드 불가. 아래는 삼성화재 환경(Windows + AutoCAD 2022) 기준.

```
1) Visual Studio에서 TravelDistance_Analyzer.slnx 열기 → 빌드(x64, .NET 4.8)
   → bin\Debug\TravelDistance_Analyzer.dll
2) AutoCAD에서  NETLOAD  → 위 DLL 선택
3) 명령창에  EVAC_Occupant  또는  EVAC_WorstN  입력
   - 탐색범위 두 코너 클릭 → (WorstN이면) Worst 개수 → 기준거리(m) → 텍스트높이(mm)
4) Evac_Path_Pass(초록)/Evac_Path_Fail(빨강) 레이어에 경로·거리 생성
```

## 7. 파이썬/리눅스 포팅 — `evac/` 모듈 (AutoCAD 불필요)

원본 로직을 **AutoCAD 없이 파일(DXF)만으로** 재현하고, 타 파이프라인에서 가져다 쓸 수
있도록 **모듈화**한 패키지. 동일 상수(50mm 셀·304.8mm 이격)·동일 절차(격자화→멀티소스
다익스트라 8방향→prev 역추적→String-Pulling). 결과는 `17F_plan_scale.png` 스타일 PNG.

> **API·명세·통합 포인트는 [기능명세.md](기능명세.md) 참조.**

```
evac/  core(순수 알고리즘) · cad(DXF입출력) · render · pick(GUI) · cli
evac_route.py  = CLI 얇은 진입점(python -m evac.cli 와 동일)
```

```bash
PY=~/miniconda3/envs/boosttrack/bin/python
cd cad/CAD_API_Based_Optimal_Evacuation_Route_Algorithm
# 0) dwg → dxf (한 번, 상위 cad-convert 파이프라인)
$PY ../../tools/cad_convert.py dwg2dxf --in Egress_Review-Test.dwg --out .
# 1) route: Occupant 모드(+매크로 원본 겹쳐 비교)
$PY evac_route.py route --dxf Egress_Review-Test.dxf --out evac_occupant.png --show-ref
# 2) route: Worst-N 자동추출
$PY evac_route.py route --dxf Egress_Review-Test.dxf --out evac_worstn.png --mode worstn --worst-n 8
# 3) pick: 맵 클릭으로 Exit N개 지정 → JSON + DXF 역주입 (디스플레이 필요)
$PY evac_route.py pick --dxf plan.dxf --n 2 --out-json exits.json --write-exits-dxf plan_tagged.dxf
# 4) connect: 보행공간 연결성 진단(도면 품질 점검)
$PY evac_route.py connect --dxf 17F.dxf --out conn.png
```
- 의존: `ezdxf` · `numpy` · `scipy`(dijkstra `min_only`, ndimage) · `matplotlib`.
- Exit 우선순위: DXF `Evac_Exit` 레이어 > `--exits`(미터) > `--exits-json`.
  헤드리스 서버는 `pick`(GUI) 대신 `--exits`/`--exits-json` 사용.
- **Exit 워크플로**: `pick`(또는 `--exits`)로 찍은 좌표를 `--write-exits-dxf`로 도면에
  `Evac_Exit` 선으로 **역주입** → 매크로 규약 도면이 되어 재사용·검증 가능(왕복 확인됨).
- 출력물: `evac_occupant.png`·`evac_worstn.png`(초록 Pass/빨강 Fail + 거리라벨 + Exit +
  미터격자·스케일바), `--show-ref` 시 매크로 원본 경로를 파란 점선으로 겹침.

### C# 원본과 다른 점(의도적)
| | C# 매크로 | 파이썬 포팅 |
|---|---|---|
| 탐색범위 | 사용자가 두 코너 클릭 | 도면 `$EXTMIN/$EXTMAX` 자동(없으면 Exit/Occupant bbox) |
| WorstN | 2단계 멀티해상도(속도용) | 단일 고해상도 전탐색(scipy로 빠름 → **더 정확**) |
| 거리 표시 | 그려진 폴리라인 실제길이(필드) | **다익스트라 격자거리**(=매크로의 Pass/Fail 판정 기준과 동일) |

### 검증 결과 (매크로 원본 vs 포팅) — `Egress_Review-Test.dxf`
파일에 매크로 실제 출력 경로가 남아있어(`Evac_Path_Pass` 3 + `Evac_Path_Fail` 11) 직접 대조:

- **입력이 겹치는 10개 출발점 전부에서 Pass/Fail 판정 100% 일치**(PASS 3·FAIL 7).
- **거리 오차 +0.4 ~ +0.9 m**(20~50 m 경로 기준 ≈ 1.5%). 포팅이 일관되게 근소하게 큰데,
  이는 포팅이 **격자거리**(=매크로 판정기준)를, 매크로 화면라벨이 **평활화 폴리라인 길이**
  (더 짧음)를 쓰기 때문 — §4-③에서 지적한 그 차이와 정확히 부합.
- **Worst-N 자동추출**이 잡은 최원거리 구석(좌측 X≈420~444 k, 우측 끝)이 매크로 원본에서
  운영자가 수동 선택했던 좌측 점들과 **같은 영역** → worst-case 추출 로직도 재현 확인.

> 결론: **동일 로직·동일 결과.** AutoCAD 종속 없이 리눅스/파이썬에서 재현·확장 가능.
> 미구현/차이는 위 표의 3개(의도적)뿐이며 판정 결과에는 영향 없음.

### 입력 규약(회의 확인) — 사람이 도면에 주석을 달아야 함
매크로/포팅 모두 아래를 전제한다(2026-07 회의록과 일치, 코드로도 확인):
- **Exit**: 캐드에서 `Evac_Exit` 레이어에 **선(Line)을 그으면 그 중점**이 도착점.
  Exit가 여러 개면 각각 선을 긋고, **멀티소스 다익스트라가 자동으로 최근접 Exit**를 택함.
- **출발점**: `Evac_Occupant` 레이어(점/폴리라인 꼭짓점).
- **장애물**: 위 특수레이어(및 결과레이어)를 **제외한 나머지 전부**.

주석 공급 방법 3가지(구현 관점):
1. 캐드에서 직접 그림 → DXF export → `evac_route.py --dxf ...` (원본 UX 그대로).
2. **CLI 주입**(캐드 불필요): `--exits "x,y;..." --starts "x,y;..."`(SW코너=0 미터). 구현됨.
3. ezdxf로 DXF에 `Evac_Exit`/`Evac_Occupant` 엔티티를 코드로 써넣기(재현·버전관리).

### 17F 평면도엔 바로 적용 불가 — 주석만으론 부족(실측)
`cad/17F.dxf`는 **PDF→벡터 트레이스**본이라 (a)`Evac_Exit`/`Evac_Occupant` 태그가 없고
(주입으로 해결) **(b)문 개구부가 벡터에 없다** — 방들이 닫힌 외곽선으로 그려져 있다.
결과적으로 이격 25~305mm 어디서도 보행공간이 **~1,800개 조각**으로 분리되고(외곽 blob +
큰 내부구역 여러 개 + 방 조각들), **내부 재실자가 Exit까지 도달하는 통로가 격리**된다
(진단: `evac_17F_connectivity.png` — 초록=최대연결영역, 주황=고립된 방/집기).
→ 17F로 의미있는 결과를 내려면 **깨끗한 원본 벡터 CAD(벽 레이어+문 개구부)** 를 받거나,
17F를 전처리(문 열기·집기 제거·외곽 정리)해야 한다. 삼성 매크로가 "필요 레이어 제외하고"
돌릴 수 있는 건 **이미 정리된 CAD**에서 실행하기 때문. `Egress_Review-Test.dwg`가 그 예.
