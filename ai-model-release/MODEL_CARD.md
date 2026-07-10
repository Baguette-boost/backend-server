# ICCAS IMU Fall LSTM Model Card

## 최종 사용 모델

실제 서버 적용과 데모에서는 아래 모델을 사용합니다.

```text
models/iccas_final_hybrid_lstm_imu_fall.pt
models/iccas_final_hybrid_lstm_imu_fall.json
```

파일명에는 과거 실험명으로 `hybrid`가 남아 있지만, 최종 선택된 결합 가중치는 LSTM 중심입니다. 따라서 발표와 문서에서는 `전처리된 IMU feature 기반 LSTM 낙상 감지 모델`로 설명합니다.

## 선택 이유

낙상 감지는 단순 Accuracy보다 Recall과 F1-score가 중요합니다. 낙상 이벤트를 놓치는 False Negative가 실제 서비스에서 더 위험하기 때문입니다.

```text
Accuracy  : 0.8799
Precision : 0.8498
Recall    : 0.8863
F1-score  : 0.8677
Threshold : 0.35
```

Accuracy만 보면 `models/iccas_final_lstm_imu_fall.pt`가 더 높지만, 최종 적용 모델은 Recall과 F1-score가 더 높은 `models/iccas_final_hybrid_lstm_imu_fall.pt`입니다.

## 학습 데이터

```text
../data/iccas_sensor_lstm/final_iccas_sisfall_imu_merged.csv
```

이 파일은 직접 취득한 ICCAS IMU 데이터와 SisFall IMU 낙상 데이터를 병합한 학습 데이터입니다. 원본 데이터 파일은 Git에 올리지 않고, 학습 완료 모델과 메타데이터만 Git에 포함합니다.

## 입력 Feature

모델 입력은 전처리된 12차원 IMU feature입니다.

```text
roll, pitch, yaw,
ax, ay, az,
wx, wy, wz,
accel_norm, gyro_norm, dt_s
```

전처리 feature:

```text
accel_norm = sqrt(ax^2 + ay^2 + az^2)
gyro_norm  = sqrt(wx^2 + wy^2 + wz^2)
dt_s       = (t_ms[i] - t_ms[i-1]) / 1000
```

학습 전에는 feature별 robust scaling을 적용합니다.

```text
x_scaled = (x - median) / IQR
```

## 모델 구조

```text
Task            : imu_fall
Architecture    : LSTM binary classifier
Sequence length : 50
Sequence stride : 4
Input shape     : [50 timesteps, 12 features]
Hidden size     : 64
LSTM layers     : 2
Dropout         : 0.25
Positive label  : fall
Negative label  : normal
```

시퀀스 라벨은 window 안에 `fall` 샘플이 하나라도 있으면 positive로 처리합니다.

```text
y_window = max(fall_target[start:end])
```

## 서버 적용

AI 추론 또는 Docker API에서 IMU 낙상 모델을 지정할 때는 아래 파일을 사용합니다.

```bash
--model models/iccas_final_hybrid_lstm_imu_fall.pt
```

실시간 입력은 최소 50개 IMU 샘플이 쌓인 뒤부터 안정적으로 LSTM 추론이 가능합니다.

## 발표용 문장

> 최종 IMU 낙상 감지 모델은 전처리된 12차원 IMU feature를 사용하는 LSTM 기반 모델이다. 직접 취득 데이터와 SisFall 데이터를 병합해 학습했으며, F1-score 0.8677과 Recall 0.8863을 기록해 실제 낙상 탐지 목적에 적합하다.
