# ICCAS LSTM 성능 지표

이 문서는 `models/iccas_sensor_lstm_fall.pt` 모델의 현재 성능 지표를 정리한 자료입니다. 수치는 모델 파일과 함께 저장된 `models/iccas_sensor_lstm_fall.json` 메타데이터, 그리고 전체 데이터를 replay 한 smoke test 결과를 기준으로 작성했습니다.

## 모델 정보

| 항목 | 값 |
| --- | --- |
| 모델명 | `iccas_sensor_lstm_fall` |
| 생성 시간 | `2026-07-01T21:46:15` |
| 입력 시퀀스 길이 | 최근 `8`개 센서 포인트 |
| 입력 feature 수 | `18`개 |
| LSTM 이상 threshold | `11.092272758483887` |
| threshold quantile | `0.98` |
| 배회 거리 threshold | `30.0m` |

## 학습 데이터 요약

| 항목 | 값 |
| --- | --- |
| 학습 원본 | `ICCAS_total_data_with_fall.xlsx` |
| 학습 행 수 | `2703` |
| 정상 학습 label | `walk` |
| validation error mean | `2.372262954711914` |
| validation error std | `2.9276890754699707` |
| test error mean | `4.917651176452637` |

원본 엑셀 파일은 Git에 포함하지 않습니다. 팀원은 로컬 또는 공유 드라이브에 있는 데이터로 재학습/검증만 수행합니다.

## 합성 이벤트 평가

합성 이벤트 평가는 정상 baseline 데이터에 GPS 이탈 배회 이벤트와 IMU 충격 낙상 이벤트를 결정적으로 추가해서 검증한 결과입니다.

| 지표 | 값 |
| --- | --- |
| Accuracy | `1.0000` |
| Precision | `1.0000` |
| Recall | `1.0000` |
| F1-score | `1.0000` |
| TP | `87` |
| FP | `0` |
| TN | `7926` |
| FN | `0` |

## 이벤트별 결과

| 이벤트 세트 | 포인트 수 | Accuracy | TP | FP | TN | FN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| normal | `2671` | `1.0000` | `0` | `0` | `2671` | `0` |
| wandering | `2671` | `1.0000` | `81` | `0` | `2590` | `0` |
| fall | `2671` | `1.0000` | `6` | `0` | `2665` | `0` |

## 전체 replay smoke test

학습된 모델을 사용해 `ICCAS_total_data_with_fall.xlsx` 전체를 replay 했을 때의 실행 결과입니다.

| 항목 | 값 |
| --- | ---: |
| 전체 포인트 | `2871` |
| 추론 준비 완료 포인트 | `2791` |
| 알람 포인트 | `46` |
| 배회 감지 포인트 | `0` |
| 낙상 감지 포인트 | `30` |

`ready_points`가 전체 포인트보다 적은 이유는 LSTM이 최근 `8`개 포인트를 모은 뒤부터 추론을 시작하기 때문입니다.

## 낙상 판단 기준

낙상은 LSTM 이상 점수와 IMU 충격 조건을 함께 봅니다.

| 조건 | 기준 |
| --- | --- |
| 가속도 충격 | `accel_norm >= 2.5` |
| 자이로 회전 충격 | `gyro_norm >= 250.0` |
| LSTM 이상 점수 | `prediction_error > threshold` 또는 `anomaly_score >= 0.75` 수준 |

천천히 앉고 몇 초간 안정적으로 유지되는 데이터는 큰 IMU 충격이 없기 때문에 낙상으로 판단하지 않는 방향입니다.

## ICCAS_dataV1 실제 sheet 라벨 기준 평가

위의 `1.0000` 지표는 실제 전체 데이터 검증 정확도가 아니라, 학습 코드가 정상 데이터에 합성 이벤트를 넣어 확인한 내부 sanity check 결과입니다. 실제 `ICCAS_dataV1.xlsx`의 sheet를 `walk`, `sit`, `idle`, `wandering`, `fall`로 나눈 뒤 새로 학습한 `models/iccas_sensor_lstm_v1.pt`를 적용하면 아래와 같습니다.

평가 기준:

- 실제 positive: `fall` 또는 `wandering`
- 실제 negative: `walk`, `sit`, `idle`
- 예측 positive: `fall_detected=true` 또는 `wandering_detected=true`
- 평가 대상: LSTM 추론 준비가 완료된 `ready=true` 포인트

| 지표 | 값 |
| --- | ---: |
| Accuracy | `0.935289` |
| Precision | `1.000000` |
| Recall | `0.916777` |
| F1-score | `0.956582` |
| TP | `13803` |
| FP | `0` |
| TN | `4307` |
| FN | `1253` |

`alarm_active`까지 positive로 포함하면 아래와 같습니다.

| 지표 | 값 |
| --- | ---: |
| Accuracy | `0.939421` |
| Precision | `0.996780` |
| Recall | `0.925080` |
| F1-score | `0.959592` |
| TP | `13928` |
| FP | `45` |
| TN | `4262` |
| FN | `1128` |

라벨별 적용 결과:

| 실제 라벨 | ready 포인트 | alarm | wandering_detected | fall_detected |
| --- | ---: | ---: | ---: | ---: |
| `walk` | `2671` | `30` | `0` | `0` |
| `sit` | `643` | `15` | `0` | `0` |
| `idle` | `993` | `0` | `0` | `0` |
| `wandering` | `12329` | `11920` | `11863` | `11` |
| `fall` | `2727` | `2003` | `1935` | `47` |

따라서 정확하게 말하면, 현재 V1 데이터 기준으로 보고할 수 있는 F1-score는 `1.0`이 아니라 `0.956582` 또는 alarm 기준 `0.959592`입니다.

## ICCAS + SisFall 병합 IMU 낙상 LSTM 평가

Kaggle SisFall 원본 IMU 데이터를 ICCAS 전처리 데이터와 병합한 뒤, IMU/Gyro 낙상 전용 binary LSTM을 다시 학습했습니다. SisFall에는 GPS가 없으므로 이 모델은 낙상 감지 전용이며, GPS 배회 감지에는 사용하지 않습니다.

모델 파일:

```text
models/iccas_sisfall_lstm_imu_fall.pt
models/iccas_sisfall_lstm_imu_fall.json
```

학습/평가 방식:

- 입력: `roll`, `pitch`, `yaw`, `ax`, `ay`, `az`, `wx`, `wy`, `wz`, `accel_norm`, `gyro_norm`, `dt_s`
- positive label: `fall`
- sequence length: `32`
- model: bidirectional LSTM + attention pooling
- SisFall은 원본 파일 단위로 train / validation / test를 분리
- ICCAS는 각 시나리오 내부 시간 순서 기준으로 train / validation / test를 분리

전체 Test 성능:

| 지표 | 값 |
| --- | ---: |
| Accuracy | `0.923891` |
| Precision | `0.906607` |
| Recall | `0.910013` |
| F1-score | `0.908307` |
| Threshold | `0.90` |
| TP | `4844` |
| FP | `499` |
| TN | `7028` |
| FN | `479` |

데이터셋별 Test 성능:

| 데이터셋 | Accuracy | Precision | Recall | F1-score | TP | FP | TN | FN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ICCAS | `0.975309` | `1.000000` | `0.825243` | `0.904255` | `85` | `0` | `626` | `18` |
| SisFall | `0.920799` | `0.905097` | `0.911686` | `0.908379` | `4759` | `499` | `6402` | `461` |

결과 JSON:

```text
data/iccas_sensor_lstm/sisfall_merged_imu_lstm_metrics.json
```

## 주의사항

현재 Accuracy, Precision, Recall, F1-score는 합성 이벤트 검증 기준입니다. 실제 사용자 착용 위치, 센서 노이즈, 낙상 자세, GPS 품질에 따라 성능이 달라질 수 있습니다. 실제 현장 데이터가 추가되면 같은 명령어로 재학습한 뒤 이 문서와 `metrics.json`을 갱신해야 합니다.
