# Git 업로드 가이드

이 폴더는 ICCAS 프로젝트의 AI 모델 배포용 패키지입니다.

원본 엑셀, CSV 학습 데이터, 중간 결과 데이터는 Git에 올리지 않고, 팀원이 실행에 필요한 코드, 학습 완료 모델, 성능 문서, 시각화 자료만 올립니다.

## 업로드 대상

Git에 올라가는 핵심 파일은 아래와 같습니다.

```text
README.md
MODEL_CARD.md
PERFORMANCE.md
GIT_UPLOAD.md
metrics.json
requirements.txt
.gitignore

scripts/
  realtime_sensor_lstm.py
  prepare_iccas_v1_dataset.py
  train_parallel_sensor_lstm.py
  train_specialized_sensor_lstm.py
  train_sisfall_merged_imu_lstm.py
  train_mac_mps_max.sh
  merge_sisfall_imu.py
  generate_lstm_simulation_html.py
  generate_performance_visualization.py
  generate_performance_png.py
  segment_situations.py

models/
  iccas_final_lstm_gps_wandering.pt
  iccas_final_lstm_gps_wandering.json
  iccas_final_sisfall_lstm_imu_fall.pt
  iccas_final_sisfall_lstm_imu_fall.json
  iccas_final_lstm_gps.pt
  iccas_final_lstm_gps.json
  iccas_final_lstm_imu_gyro.pt
  iccas_final_lstm_imu_gyro.json
  iccas_final_lstm_imu_fall.pt
  iccas_final_lstm_imu_fall.json

docs/
  FINAL_DATA_TRAINING_REPORT.md

assets/
  final_model_performance_dashboard.png
  final_model_performance_dashboard.svg
  final_model_performance_summary.csv
```

## 업로드 제외

아래 파일은 Git에 올리지 않습니다.

```text
../ICCAS_final_data.xlsx
../ICCAS_dataV1.xlsx
../SisFall_dataset/
../data/
../outputs/
__pycache__/
._*
.DS_Store
```

원본 데이터는 용량이 크고 개인정보/센서 원본 이슈가 있을 수 있으므로 Git이 아니라 공유 드라이브나 로컬 경로로 관리합니다.

## 최종 권장 모델

서버에 적용할 최종 권장 모델은 아래 두 개입니다.

```text
GPS 배회 감지:
models/iccas_final_lstm_gps_wandering.pt
models/iccas_final_lstm_gps_wandering.json

IMU 낙상 감지:
models/iccas_final_sisfall_lstm_imu_fall.pt
models/iccas_final_sisfall_lstm_imu_fall.json
```

## 최종 성능

```text
GPS Wandering, 서버 task 기준
Accuracy  0.9544
Precision 0.9368
Recall    0.9854
F1-score  0.9605

IMU Fall, ICCAS + SisFall 기준
Accuracy  0.8814
Precision 0.8879
Recall    0.8392
F1-score  0.8629
```

성능 시각화 이미지는 아래 파일입니다.

```text
assets/final_model_performance_dashboard.png
```

## Git 상태 확인

```bash
cd /Volumes/Hub_1T/ICCAS/ai-model-release
git status
```

## Git에 추가

`.gitignore`가 allowlist 방식이라 아래처럼 전체 추가해도 원본 데이터는 제외됩니다.

```bash
git add -A
git status
```

상태에서 `ICCAS_final_data.xlsx`, `SisFall_dataset`, `data/` 같은 원본 데이터가 보이면 안 됩니다.

## Commit

```bash
git commit -m "Add final ICCAS AI models and performance assets"
```

## Push

원격 저장소가 이미 연결되어 있으면:

```bash
git push -u origin main
```

원격 저장소를 다시 확인하려면:

```bash
git remote -v
```

원격 주소가 잘못되어 있으면:

```bash
git remote set-url origin https://github.com/Baguette-boost/ai-model-release.git
git push -u origin main
```

## 팀원이 받은 뒤 실행

```bash
git clone https://github.com/Baguette-boost/ai-model-release.git
cd ai-model-release

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

MPS 확인:

```bash
python -c "import torch; print(torch.backends.mps.is_built(), torch.backends.mps.is_available())"
```

성능 시각화 PNG 다시 생성:

```bash
python scripts/generate_performance_png.py \
  --metrics metrics.json \
  --svg-output assets/final_model_performance_dashboard.svg \
  --png-output assets/final_model_performance_dashboard.png
```
