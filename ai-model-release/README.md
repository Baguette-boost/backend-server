# ICCAS AI 모델 배포 패키지

GPS, IMU 데이터를 사용해 LSTM 기반으로 실시간 배회감지와 낙상감지를 수행하는 AI 모델 패키지입니다. 팀원이 Git에서 받을 때 원본 엑셀 데이터나 실행 결과 파일 없이, AI 코드와 학습 완료 모델만 사용할 수 있도록 구성했습니다.

Git 업로드와 push 방법은 `GIT_UPLOAD.md`를 참고합니다.

## Git에 올릴 파일

이 폴더에서 아래 파일만 Git에 올리면 됩니다.

```text
ai-model-release/
  README.md
  MODEL_CARD.md
  PERFORMANCE.md
  GIT_UPLOAD.md
  metrics.json
  requirements.txt
  .gitignore
  scripts/realtime_sensor_lstm.py
  scripts/segment_situations.py
  scripts/prepare_iccas_v1_dataset.py
  scripts/generate_lstm_simulation_html.py
  scripts/train_parallel_sensor_lstm.py
  scripts/train_specialized_sensor_lstm.py
  scripts/merge_sisfall_imu.py
  scripts/train_sisfall_merged_imu_lstm.py
  scripts/train_mac_mps_max.sh
  models/iccas_sensor_lstm_fall.pt
  models/iccas_sensor_lstm_fall.json
  models/iccas_sensor_lstm_v1.pt
  models/iccas_sensor_lstm_v1.json
  models/iccas_lstm_v1_gps_wandering.pt
  models/iccas_lstm_v1_gps_wandering.json
  models/iccas_lstm_v1_imu_fall.pt
  models/iccas_lstm_v1_imu_fall.json
  models/iccas_sisfall_lstm_imu_fall.pt
  models/iccas_sisfall_lstm_imu_fall.json
```

Git에 올리지 않는 파일은 원본 학습 데이터, 테스트 결과, 캐시 파일입니다.

```text
ICCAS_total_data.xlsx
ICCAS_total_data_with_fall.xlsx
data/
outputs/
__pycache__/
._*
.DS_Store
```

## 맥 개발 환경 준비

모델 성능 지표는 `PERFORMANCE.md`와 `metrics.json`에서 확인합니다.

```bash
cd ai-model-release
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

가상환경에서 나가려면 아래 명령어를 사용합니다.

```bash
deactivate
```

## 엑셀/CSV 데이터 형식

학습 또는 시뮬레이션 입력 파일은 `.xlsx` 또는 `.csv`를 사용할 수 있습니다. 최소 필요 컬럼은 아래와 같습니다.

```text
server_time, device, roll, pitch, yaw, ax, ay, az, wx, wy, wz
```

GPS 기반 배회감지와 지도 표시까지 하려면 아래 컬럼도 같이 있어야 합니다.

```text
lat, lng
```

`latitude`, `longitude`, `timestamp` 컬럼명은 코드에서 각각 `lat`, `lng`, `server_time`으로 자동 변환합니다. `label` 컬럼은 있으면 학습/검증 참고용으로 사용하고, 없어도 실행은 가능합니다.

## 데이터 확인

원본 데이터가 준비되어 있을 때 구조를 먼저 확인합니다. 원본 데이터는 Git에 올리지 말고 팀 내부 공유 드라이브나 로컬 경로에 둡니다.

```bash
python scripts/realtime_sensor_lstm.py inspect \
  --source ../ICCAS_total_data_with_fall.xlsx \
  --sheet data
```

## 이미 학습된 모델로 시뮬레이션

백엔드 없이 로컬에서 추론 결과 CSV만 만들려면 아래처럼 실행합니다.

```bash
python scripts/realtime_sensor_lstm.py replay \
  --source ../ICCAS_total_data_with_fall.xlsx \
  --sheet data \
  --model models/iccas_sensor_lstm_fall.pt \
  --output outputs/replay_results.csv \
  --device cpu \
  --print-events
```

Mac에서 Apple Silicon GPU를 쓰고 싶으면 `--device mps`를 사용할 수 있습니다. 호환 문제가 있으면 `--device cpu`가 가장 안정적입니다.

MPS 사용 가능 여부는 아래 명령어로 확인합니다.

```bash
python -c "import torch; print(torch.backends.mps.is_built(), torch.backends.mps.is_available())"
```

두 번째 값이 `True`이면 `--device mps` 또는 `--device auto`로 맥 GPU 가속을 사용할 수 있습니다. `False`이면 현재 Python/PyTorch 환경에서는 CPU로 실행됩니다.

맥 성능을 최대한 사용해서 학습하려면 아래 스크립트를 사용합니다.

```bash
cd /Volumes/Hub_1T/ICCAS/ai-model-release
./scripts/train_mac_mps_max.sh
```

이 스크립트는 다음 설정을 자동 적용합니다.

```text
PYTORCH_ENABLE_MPS_FALLBACK=1
OMP_NUM_THREADS=맥 performance core 수
VECLIB_MAXIMUM_THREADS=OMP_NUM_THREADS
--device auto
--batch-size 512
```

MPS가 가능한 맥이면 `mps`를 사용하고, 현재 환경에서 MPS가 잡히지 않으면 `cpu`로 내려갑니다. 더 오래 학습하려면 아래처럼 실행합니다.

```bash
EPOCHS=35 BATCH_SIZE=512 ./scripts/train_mac_mps_max.sh
```

## 백엔드로 결과 전송

백엔드 서버가 먼저 실행되어 있어야 합니다.

```bash
cd ../backend-server/backend
PYTHONPATH=. python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

다른 터미널에서 AI 추론을 실행하면 각 센서 포인트의 추론 결과가 백엔드의 `/api/sensor-result`로 전송됩니다.

```bash
cd ../ai-model-release
source .venv/bin/activate
python scripts/realtime_sensor_lstm.py replay \
  --source ../ICCAS_total_data_with_fall.xlsx \
  --sheet data \
  --model models/iccas_sensor_lstm_fall.pt \
  --backend-url http://127.0.0.1:8000/api/sensor-result \
  --output outputs/backend_replay_results.csv \
  --sleep 0.05 \
  --device cpu \
  --print-events
```

백엔드에서 확인하는 주소는 아래와 같습니다.

```text
http://127.0.0.1:8000/api/sensor-result
http://127.0.0.1:8000/api/sensor-result/latest
http://127.0.0.1:8000/api/sensor-result/history?limit=5000
```

`/api/sensor-result`와 `/latest`는 최신 1건만 보여줍니다. 낙상 이벤트가 지나간 뒤 최신 데이터가 정상이라면 `fall_detected=false`로 보일 수 있습니다. 전체 이벤트 확인은 `/history?limit=5000`에서 봅니다.

## 실시간 JSON 입력 추론

실제 장비나 다른 프로세스에서 센서 JSON을 한 줄씩 넘길 때는 `live` 모드를 사용합니다.

```bash
python scripts/realtime_sensor_lstm.py live \
  --model models/iccas_sensor_lstm_fall.pt \
  --backend-url http://127.0.0.1:8000/api/sensor-result \
  --device cpu
```

입력 JSON 예시는 아래와 같습니다.

```json
{"server_time":"2026-07-01 11:35:56.764000","device":"esp32-1","lat":36.621853,"lng":127.426337,"roll":-12.1,"pitch":9.7,"yaw":8.8,"ax":3.2,"ay":-2.6,"az":4.1,"wx":320.0,"wy":-280.0,"wz":220.0}
```

## 재학습 방법

새로운 학습 데이터가 생기면 원본 데이터는 Git에 올리지 않고 로컬 경로에서만 사용합니다. 아래 명령어는 기존 모델 파일을 새로 학습한 결과로 덮어씁니다.

```bash
python scripts/realtime_sensor_lstm.py train \
  --source ../ICCAS_total_data_with_fall.xlsx \
  --sheet data \
  --model models/iccas_sensor_lstm_fall.pt \
  --epochs 80 \
  --batch-size 32 \
  --sequence-length 8 \
  --threshold-quantile 0.98 \
  --route-threshold-m 30.0 \
  --device cpu
```

학습이 끝나면 아래 두 파일이 갱신됩니다.

```text
models/iccas_sensor_lstm_fall.pt
models/iccas_sensor_lstm_fall.json
```

그 다음 이 두 파일과 필요한 코드 변경만 Git에 커밋합니다.

```bash
git add README.md MODEL_CARD.md requirements.txt scripts/realtime_sensor_lstm.py models/iccas_sensor_lstm_fall.pt models/iccas_sensor_lstm_fall.json
git commit -m "Add ICCAS LSTM fall and wandering model"
git push
```

## ICCAS_dataV1 전처리

`ICCAS_dataV1.xlsx`처럼 여러 sheet가 상황별로 나뉘어 있는 경우 아래 명령어로 라벨을 분리하고 통합 학습 데이터를 만들 수 있습니다.

```bash
python scripts/prepare_iccas_v1_dataset.py \
  --source ../ICCAS_dataV1.xlsx \
  --raw-output ../data/iccas_sensor_lstm/iccas_dataV1_labeled.csv \
  --features-output ../data/iccas_sensor_lstm/iccas_dataV1_lstm_features.csv \
  --summary-output ../data/iccas_sensor_lstm/iccas_dataV1_preprocess_summary.json \
  --split-dir ../data/iccas_sensor_lstm/iccas_dataV1_split
```

V1 데이터로 학습한 모델은 아래 파일입니다.

```text
models/iccas_sensor_lstm_v1.pt
models/iccas_sensor_lstm_v1.json
```

GPS와 IMU/Gyro를 분리해서 병렬 서버 구조용 모델을 만들려면 아래 명령어를 사용합니다.

```bash
python scripts/train_parallel_sensor_lstm.py \
  --source ../ICCAS_dataV1.xlsx \
  --model-dir ../models \
  --report ../data/iccas_sensor_lstm/parallel_sensor_lstm_metrics.json \
  --scenario-dir ../data/iccas_sensor_lstm/scenarios \
  --epochs 35 \
  --batch-size 128 \
  --sequence-length 16 \
  --device cpu
```

생성되는 병렬 추론용 모델:

```text
models/iccas_lstm_v1_gps.pt
models/iccas_lstm_v1_gps.json
models/iccas_lstm_v1_imu_gyro.pt
models/iccas_lstm_v1_imu_gyro.json
```

성능을 더 높이려면 서버 역할에 맞춰 전용 binary 모델을 학습합니다.

```bash
python scripts/train_specialized_sensor_lstm.py \
  --source ../ICCAS_dataV1.xlsx \
  --model-dir ../models \
  --report ../data/iccas_sensor_lstm/specialized_sensor_lstm_metrics.json \
  --epochs 45 \
  --batch-size 128 \
  --sequence-length 16 \
  --device cpu
```

권장 최종 모델:

```text
GPS 배회 전용: models/iccas_lstm_v1_gps_wandering.pt
IMU 낙상 전용: models/iccas_lstm_v1_imu_fall.pt
```

현재 전용 모델 성능:

```text
GPS Wandering LSTM  Accuracy 0.9360, F1 0.9477
IMU Fall LSTM       Accuracy 0.9311, F1 0.7768
```

## SisFall 데이터 병합

Kaggle SisFall 원본 데이터셋을 받은 경우, IMU/Gyro 낙상 모델 보강용으로 병합할 수 있습니다. SisFall에는 GPS가 없으므로 GPS 배회 모델에는 사용하지 않습니다.

```bash
python scripts/merge_sisfall_imu.py \
  --sisfall-dir ../SisFall_dataset \
  --iccas-source ../data/iccas_sensor_lstm/iccas_dataV1_labeled.csv \
  --sisfall-output ../data/iccas_sensor_lstm/sisfall_imu_converted.csv \
  --merged-output ../data/iccas_sensor_lstm/iccas_sisfall_imu_merged.csv \
  --summary-output ../data/iccas_sensor_lstm/sisfall_merge_summary.json \
  --max-files-per-label 200 \
  --max-rows-per-file 3000 \
  --stride 4
```

생성 결과:

```text
SisFall 변환 row: 324801
ICCAS + SisFall 병합 row: 344260
```

병합 데이터로 IMU/Gyro 낙상 LSTM을 다시 학습하려면 아래 명령어를 사용합니다.

```bash
python scripts/train_sisfall_merged_imu_lstm.py \
  --source ../data/iccas_sensor_lstm/iccas_sisfall_imu_merged.csv \
  --model-dir models \
  --report ../data/iccas_sensor_lstm/sisfall_merged_imu_lstm_metrics.json \
  --epochs 25 \
  --device auto
```

`--device auto`는 Apple Silicon Mac에서 PyTorch MPS가 가능하면 `mps`를 자동 사용하고, 불가능하면 `cpu`로 실행합니다. 직접 지정하려면 `--device mps`를 사용합니다.

생성되는 모델:

```text
models/iccas_sisfall_lstm_imu_fall.pt
models/iccas_sisfall_lstm_imu_fall.json
```

현재 병합 모델 테스트 성능:

```text
Accuracy  0.9239
Precision 0.9066
Recall    0.9100
F1-score  0.9083
```

데이터셋별 테스트 성능:

```text
ICCAS   Accuracy 0.9753, Precision 1.0000, Recall 0.8252, F1 0.9043
SisFall Accuracy 0.9208, Precision 0.9051, Recall 0.9117, F1 0.9084
```

## ICCAS_final_data 최종 학습

GPS 데이터가 추가된 `ICCAS_final_data.xlsx`는 sheet별 상황을 다시 매핑해서 전처리했습니다.

```text
추가된 학교 데이터 -> walk
집 앞 walk -> walk
학교 walk -> walk
새로운 경로에서 배회 -> wandering
새로운 경로 1.2km, 새로운 경로 600m -> wandering
떨어졌다가 앞으로 걸어가고 떨어졌다 앞으로 걸어감 -> fall
제자리에서 떨어짐1,2 -> fall
sit -> sit
idle -> idle
```

전처리 결과:

```text
전체 row: 24,759
walk: 8,003
wandering: 12,353
fall: 2,751
sit: 651
idle: 1,001
```

최종 서버 권장 모델:

```text
GPS 배회 감지:
models/iccas_final_lstm_gps_wandering.pt
models/iccas_final_lstm_gps_wandering.json

IMU 낙상 감지:
  models/iccas_final_sisfall_lstm_imu_fall.pt
  models/iccas_final_sisfall_lstm_imu_fall.json
  docs/FINAL_DATA_TRAINING_REPORT.md
  assets/final_model_performance_dashboard.png
  assets/final_model_performance_dashboard.svg
  assets/final_model_performance_summary.csv
```

최종 성능:

```text
GPS Wandering
Accuracy 0.9115, Precision 0.8586, Recall 0.9854, F1 0.9177

GPS Wandering, 서버 task 기준
Accuracy 0.9544, Precision 0.9368, Recall 0.9854, F1 0.9605

Final ICCAS + SisFall IMU Fall
Accuracy 0.8814, Precision 0.8879, Recall 0.8392, F1 0.8629
```

최종 리포트:

```text
data/iccas_sensor_lstm/FINAL_DATA_TRAINING_REPORT.md
```

성능 시각화:

```text
data/iccas_sensor_lstm/final_model_performance_dashboard.png
data/iccas_sensor_lstm/final_model_performance_dashboard.svg
data/iccas_sensor_lstm/final_model_performance_dashboard.html
data/iccas_sensor_lstm/final_model_performance_summary.csv
```

## 프론트 화면 흐름

AI 스크립트가 백엔드로 결과를 보내면 프론트는 백엔드의 `/api/sensor-result/latest`와 `/api/sensor-result/history?limit=5000`을 주기적으로 조회합니다. 지도 화면에서는 `lat`, `lng`, `fall_detected`, `wandering_detected`, `risk_level`, `detection_type` 값을 사용해 실시간 위치와 알림을 표시합니다.
