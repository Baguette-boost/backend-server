# ICCAS_final_data 최종 전처리 및 학습 리포트

## 입력 데이터

```text
ICCAS_final_data.xlsx
```

GPS 데이터가 추가된 최종 엑셀 파일을 sheet별 상황으로 나누고, 기존 V1과 동일한 기준으로 라벨을 재정의했습니다.

## Sheet 라벨 매핑

| Sheet | Label | Rows |
| --- | --- | ---: |
| 추가된 학교 데이터 | walk | 5300 |
| 집 앞 walk | walk | 1603 |
| 학교 walk | walk | 1100 |
| 새로운 경로에서 배회 | wandering | 3801 |
| 새로운 경로 1.2km, 새로운 경로 600m | wandering | 8552 |
| 떨어졌다가 앞으로 걸어가고 떨어졌다 앞으로 걸어감 | fall | 1251 |
| 제자리에서 떨어짐1,2 | fall | 1500 |
| sit | sit | 651 |
| idle | idle | 1001 |

## 전처리 결과

```text
전체 row: 24,759
walk: 8,003
wandering: 12,353
fall: 2,751
sit: 651
idle: 1,001
```

생성 파일:

```text
data/iccas_sensor_lstm/iccas_final_labeled.csv
data/iccas_sensor_lstm/iccas_final_lstm_features.csv
data/iccas_sensor_lstm/iccas_final_preprocess_summary.json
data/iccas_sensor_lstm/iccas_final_split/
```

## 병렬 5-class LSTM

GPS와 IMU/Gyro를 분리해서 5-class 분류 모델을 학습했습니다.

| Model | Accuracy | Macro F1 | Weighted F1 |
| --- | ---: | ---: | ---: |
| GPS 5-class | 0.8883 | 0.6758 | 0.8930 |
| IMU/Gyro 5-class | 0.7660 | 0.6179 | 0.7675 |

모델 파일:

```text
ai-model-release/models/iccas_final_lstm_gps.pt
ai-model-release/models/iccas_final_lstm_gps.json
ai-model-release/models/iccas_final_lstm_imu_gyro.pt
ai-model-release/models/iccas_final_lstm_imu_gyro.json
```

## 서버용 Binary LSTM

서버 병렬 구조에서는 5-class보다 전용 binary 모델을 권장합니다.

| Model | Accuracy | Precision | Recall | F1 | Threshold |
| --- | ---: | ---: | ---: | ---: | ---: |
| GPS Wandering | 0.9115 | 0.8586 | 0.9854 | 0.9177 | 0.08 |
| IMU Fall | 0.9621 | 0.8150 | 0.8509 | 0.8325 | 0.82 |

GPS 배회 모델은 서버 병렬 구조에서 낙상 판단을 담당하지 않습니다. 낙상은 IMU Fall 서버가 담당하므로, GPS 서버의 실제 역할 범위에 맞춰 평가하면 아래와 같습니다.

| GPS 평가 기준 | Accuracy | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: |
| 전체 sheet 기준 | 0.9115 | 0.8586 | 0.9854 | 0.9177 |
| fall sheet 제외, GPS task 기준 | 0.9544 | 0.9368 | 0.9854 | 0.9605 |
| 이동 경로 sheet 기준 | 0.9878 | 0.9945 | 0.9854 | 0.9899 |

모델 파일:

```text
ai-model-release/models/iccas_final_lstm_gps_wandering.pt
ai-model-release/models/iccas_final_lstm_gps_wandering.json
ai-model-release/models/iccas_final_lstm_imu_fall.pt
ai-model-release/models/iccas_final_lstm_imu_fall.json
```

## 최종 ICCAS + SisFall 낙상 모델

낙상 데이터 보강을 위해 최종 ICCAS 데이터와 SisFall IMU 데이터를 다시 병합했습니다.

```text
전체 병합 row: 349,560
normal: 174,800
fall: 152,752
wandering: 12,353
walk: 8,003
idle: 1,001
sit: 651
```

최종 ICCAS + SisFall IMU Fall 성능:

| Dataset | Accuracy | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: |
| Overall | 0.8814 | 0.8879 | 0.8392 | 0.8629 |
| ICCAS | 0.9666 | 0.9286 | 0.7573 | 0.8342 |
| SisFall | 0.8733 | 0.8871 | 0.8410 | 0.8634 |

추가 튜닝 결과:

```text
hidden_size=128, epochs=45 -> Overall F1 0.8578
hidden_size=64, epochs=35  -> Overall F1 0.8605
```

두 실험 모두 기존 최종 모델의 Overall F1 0.8629보다 낮아서 최종 모델로 채택하지 않았습니다. 다만 hidden_size=64 실험은 ICCAS-only test F1이 0.8646으로 높았으므로, 현장 ICCAS 데이터가 더 늘어나면 작은 모델을 다시 검토할 수 있습니다.

모델 파일:

```text
ai-model-release/models/iccas_final_sisfall_lstm_imu_fall.pt
ai-model-release/models/iccas_final_sisfall_lstm_imu_fall.json
```

## 실행 환경

최종 학습은 Mac MPS에서 수행했습니다.

```text
device: mps
```

## 권장 서버 적용

```text
배회 감지: iccas_final_lstm_gps_wandering.pt
낙상 감지: iccas_final_sisfall_lstm_imu_fall.pt
```

GPS 배회 감지는 최종 GPS 데이터가 반영된 `iccas_final_lstm_gps_wandering.pt`를 권장합니다. 낙상 감지는 ICCAS만 사용한 모델보다 데이터가 많은 ICCAS+SisFall 모델을 우선 후보로 둡니다.

서버 적용 시에는 GPS 배회 감지와 IMU 낙상 감지를 병렬로 돌리고, 최종 이벤트 병합 단계에서 낙상 이벤트가 활성화된 구간의 GPS 배회 알림은 낮은 우선순위로 처리하는 것을 권장합니다.

## 시각화 자료

최종 성능지표는 HTML 대시보드와 CSV 요약표로도 생성했습니다.

```text
data/iccas_sensor_lstm/final_model_performance_dashboard.png
data/iccas_sensor_lstm/final_model_performance_dashboard.svg
data/iccas_sensor_lstm/final_model_performance_dashboard.html
data/iccas_sensor_lstm/final_model_performance_summary.csv
```

대시보드에는 모델별 Accuracy, Precision, Recall, F1 비교, 라벨 분포, 주요 confusion matrix, 서버 적용 구조가 포함됩니다. PNG 파일은 발표 자료나 보고서에 바로 삽입할 수 있습니다.
