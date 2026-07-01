We evaluated the TRACE **YOLO26 detector + tracker** pipeline on three AI-Hub crowd sequences, both quantitatively (against the labels) and qualitatively (overlay videos). This report summarises the results.

- **Detectors:** YOLO26-L and YOLO26-N, imgsz 640.
- **Trackers:** BoostTrack++ and BoT-SORT-ReID.
- **Sequences:** EXCO095 (529 frames), EXCO133 (551), Bukchon 203 (558).
- **Results Location:** `172.168.47.51:/volume1/home/jordan/tracking_results/samsung_clab/aihub_crowd_data`

# Eng

## 1. Important note on the labels (why metrics are "point-based")

The AI-Hub ground-truth box is a **head / upper-body box** (its centre is the labelled head point), while YOLO detects the **full body**. The two barely overlap, so standard IoU-based mAP / MOTA / IDF1 come out near **zero** — a *label-definition mismatch, not a model failure*.

!box_semantics_mismatch.jpg

*Red = GT (head box). Green = YOLO (full body). Same people, but IoU ≈ 0.3.*

So all numbers below use **point matching**: a person counts as detected when the GT head point falls **inside** a predicted box. This is faithful to the labels and measures what matters — *is each person found, and does the tracker keep one stable ID per person?*

---

## 2. Quantitative results

### 2.1 Tracking — Bukchon 203 (the only sequence with track-ID GT)

Higher IDF1 / MOTA = better; lower IDS (ID-switches) = better.

| Detector | Tracker | IDF1 | MOTA | MOTP | IDS | Precision (IDP) | Recall (IDR) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| YOLO26-L | **BoostTrack++** | **0.834** | **0.815** | 0.067 | 43 | 0.906 | 0.772 |
| YOLO26-L | BoT-SORT-ReID | 0.791 | 0.812 | 0.067 | 49 | 0.852 | 0.738 |
| YOLO26-N | BoostTrack++ | 0.752 | 0.673 | 0.067 | 63 | 0.899 | 0.646 |
| YOLO26-N | BoT-SORT-ReID | 0.695 | 0.670 | 0.067 | 102 | 0.808 | 0.610 |

**Best: YOLO26-L + BoostTrack++ (IDF1 0.834, MOTA 0.815).** BoostTrack++ beats BoT-SORT for both detectors, and the larger detector (L > N) matters most.

### 2.2 Detection — all three sequences (person, conf ≥ 0.4)

Precision / Recall / F1 from point matching; **Count MAE / RMSE** = error in people-per-frame.

| Sequence | Detector | Precision | Recall | F1 | Count MAE | Count RMSE |
| --- | --- | --- | --- | --- | --- | --- |
| **Bukchon 203** | YOLO26-L | 0.929 | **0.806** | **0.863** | **1.63** | **2.11** |
|  | YOLO26-N | 0.934 | 0.704 | 0.803 | 2.91 | 3.42 |
| **EXCO133** | YOLO26-L | 0.916 | **0.622** | **0.741** | **4.19** | **4.92** |
|  | YOLO26-N | 0.947 | 0.384 | 0.547 | 7.73 | 8.32 |
| **EXCO095** | YOLO26-L | 0.916 | **0.232** | **0.371** | **17.35** | **18.17** |
|  | YOLO26-N | 0.946 | 0.133 | 0.233 | 19.98 | 20.71 |

**Key Points:**

- **Precision is high everywhere (~0.92–0.95)** — boxes the model emits are almost always real people.
- **Recall drops as the crowd gets denser/farther**: Bukchon (moderate, indoor) is strong; EXCO133 is mid; EXCO095 (packed, far-field) is hard — many small/occluded people are missed.
- **YOLO26-L is the most accurate** overall; YOLO26-N is lighter but recalls fewer people, especially in dense crowds.

---

## 3. Qualitative videos

**12 videos** in `videos/<detector>/<tracker>/<sequence>.mp4` — 2 detectors (YOLO26-L, YOLO26-N) × 2 trackers (BoostTrack++, BoT-SORT-ReID) × 3 sequences. H.264, 24 fps (matching the source). Each overlays the prediction **bbox + track ID (`id=N`) + trajectory trail** (the line traces each person's feet, i.e. their path on the floor).

For **Bukchon**, the **ground truth is overlaid too** — red GT boxes + red head dots + `GT<id>` labels — so prediction vs. GT is directly comparable.

!bukchon_overlay_sample.jpg

*Bukchon, YOLO26-L + BoostTrack++. Coloured = prediction (`id=N`) with feet-trail; red = ground truth (`GT<id>`).*

Recommended starting point: `videos/yolo26l/boosttrack/Bukchon203.mp4` (best config).

# Kor

## 1. 라벨에 관한 중요 참고사항 (지표가 "포인트 기반"인 이유)

AI-Hub 정답(GT) 박스는 **머리/상반신 박스**(박스 중심이 라벨링된 머리 포인트)인 반면, YOLO는 **전신**을 검출합니다. 두 박스는 거의 겹치지 않기 때문에 표준 IoU 기반 mAP / MOTA / IDF1은 **거의 0**에 가깝게 나옵니다 — 이는 *모델의 실패가 아니라 라벨 정의의 불일치*입니다.

!box_semantics_mismatch.jpg

*빨강 = GT(머리 박스). 초록 = YOLO(전신). 동일 인물이지만 IoU ≈ 0.3.*

따라서 아래의 모든 수치는 **포인트 매칭**을 사용합니다: GT 머리 포인트가 예측 박스 **안쪽에** 들어오면 해당 인물을 검출된 것으로 간주합니다. 이는 라벨에 충실하며 핵심을 측정합니다 — *각 인물이 발견되는가, 그리고 트래커가 인물당 하나의 안정적인 ID를 유지하는가?*

---

## 2. 정량 결과

### 2.1 추적(Tracking) — 북촌 203 (트랙 ID GT가 존재하는 유일한 시퀀스)

IDF1 / MOTA는 높을수록 좋음; IDS(ID 전환)는 낮을수록 좋음.

| 검출기 | 트래커 | IDF1 | MOTA | MOTP | IDS | 정밀도(IDP) | 재현율(IDR) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| YOLO26-L | **BoostTrack++** | **0.834** | **0.815** | 0.067 | 43 | 0.906 | 0.772 |
| YOLO26-L | BoT-SORT-ReID | 0.791 | 0.812 | 0.067 | 49 | 0.852 | 0.738 |
| YOLO26-N | BoostTrack++ | 0.752 | 0.673 | 0.067 | 63 | 0.899 | 0.646 |
| YOLO26-N | BoT-SORT-ReID | 0.695 | 0.670 | 0.067 | 102 | 0.808 | 0.610 |

**최고 성능: YOLO26-L + BoostTrack++ (IDF1 0.834, MOTA 0.815).** BoostTrack++는 두 검출기 모두에서 BoT-SORT를 능가하며, 더 큰 검출기(L > N)의 영향이 가장 큽니다.

### 2.2 검출(Detection) — 세 시퀀스 전체 (person, conf ≥ 0.4)

포인트 매칭 기반 정밀도 / 재현율 / F1; **Count MAE / RMSE** = 프레임당 인원 수 오차.

| 시퀀스 | 검출기 | 정밀도 | 재현율 | F1 | Count MAE | Count RMSE |
| --- | --- | --- | --- | --- | --- | --- |
| **북촌 203** | YOLO26-L | 0.929 | **0.806** | **0.863** | **1.63** | **2.11** |
|  | YOLO26-N | 0.934 | 0.704 | 0.803 | 2.91 | 3.42 |
| **EXCO133** | YOLO26-L | 0.916 | **0.622** | **0.741** | **4.19** | **4.92** |
|  | YOLO26-N | 0.947 | 0.384 | 0.547 | 7.73 | 8.32 |
| **EXCO095** | YOLO26-L | 0.916 | **0.232** | **0.371** | **17.35** | **18.17** |
|  | YOLO26-N | 0.946 | 0.133 | 0.233 | 19.98 | 20.71 |

**핵심 포인트:**

- **정밀도는 모든 시퀀스에서 높음(~0.92–0.95)** — 모델이 출력하는 박스는 거의 항상 실제 인물입니다.
- **군중이 밀집하거나 멀어질수록 재현율이 하락**: 북촌(중간 밀도, 실내)은 우수; EXCO133은 중간; EXCO095(밀집, 원거리)는 어려움 — 작거나 가려진 인물이 다수 누락됩니다.
- **YOLO26-L이 전반적으로 가장 정확**; YOLO26-N은 더 가볍지만 특히 밀집 군중에서 더 적은 인물을 검출(재현)합니다.

---

## 3. 정성 영상

`videos/<detector>/<tracker>/<sequence>.mp4` 경로에 **영상 12개** — 검출기 2종(YOLO26-L, YOLO26-N) × 트래커 2종(BoostTrack++, BoT-SORT-ReID) × 시퀀스 3개. H.264, 24 fps(소스와 동일). 각 영상은 예측 결과의 **bbox + 트랙 ID(`id=N`) + 궤적 트레일**을 오버레이합니다(선은 각 인물의 발을 따라 그려지며, 즉 바닥에서의 이동 경로를 나타냅니다).

**북촌**의 경우 **정답(GT)도 함께 오버레이**됩니다 — 빨간 GT 박스 + 빨간 머리 점 + `GT<id>` 라벨 — 따라서 예측과 GT를 직접 비교할 수 있습니다.

!bukchon_overlay_sample.jpg

*북촌, YOLO26-L + BoostTrack++. 컬러 = 예측(`id=N`) 및 발 궤적; 빨강 = 정답(`GT<id>`).*

권장 시작점: `videos/yolo26l/boosttrack/Bukchon203.mp4` (최적 구성).