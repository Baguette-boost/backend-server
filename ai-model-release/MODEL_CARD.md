# ICCAS GPS + IMU LSTM Model Card

## 포함 파일

- `models/iccas_sensor_lstm_fall.pt`: 학습 완료된 PyTorch LSTM 체크포인트
- `models/iccas_sensor_lstm_fall.json`: 전처리 기준, feature 목록, threshold, route 기준, 평가 결과 메타데이터
- `scripts/realtime_sensor_lstm.py`: 학습, 재생 시뮬레이션, 실시간 stdin 추론 실행 코드

## 현재 학습 상태

- 모델 생성 시간: `2026-07-01T21:46:15`
- 시퀀스 길이: 최근 `8`개 센서 포인트
- LSTM feature 수: `18`
- 이상 판단 threshold: `11.092272758483887`
- 배회 route threshold: `30.0m`
- 합성 이벤트 검증 정확도: `overall_accuracy = 1.0`

합성 이벤트 검증은 정상 데이터에 GPS 이탈 배회 이벤트와 IMU 충격 낙상 이벤트를 넣어 확인한 결과입니다. 실제 현장 정확도는 실제 낙상/배회 데이터가 추가될수록 다시 검증해야 합니다.

## 낙상 판단 기준

실시간 추론에서 낙상은 아래 조건을 함께 봅니다.

1. 최근 센서 시퀀스가 LSTM 기준으로 비정상적인 흐름인지 확인합니다.
2. IMU 충격/회전이 큰지 확인합니다.
3. `accel_norm >= 2.5` 또는 `gyro_norm >= 250.0`이면 낙상 후보 충격으로 봅니다.
4. 낙상 후보 충격과 LSTM 이상 점수가 함께 나타나면 `fall_detected=true`가 됩니다.

따라서 천천히 앉고 몇 초간 안정적으로 대기하는 데이터는 큰 충격/회전이 없어서 낙상으로 판단하지 않는 방향입니다.

## 파일 무결성

```text
1a8a271fef4cbf9a73fd1247e059623ea073c16bb20b4029cf0b99397604f7e5  models/iccas_sensor_lstm_fall.pt
2338e9175a85bb8e1436630206f50923c0b582c84be8949b25c1ebb640a11a0e  models/iccas_sensor_lstm_fall.json
cfbe079b0148a9426aa16087214422e8b98def4565ce87eb0537f66561fadbcd  scripts/realtime_sensor_lstm.py
```
