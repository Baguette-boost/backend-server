#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-../.venv/bin/python}"

# Allows PyTorch to run unsupported MPS ops on CPU instead of failing.
export PYTORCH_ENABLE_MPS_FALLBACK="${PYTORCH_ENABLE_MPS_FALLBACK:-1}"

# Use the Mac performance cores for CPU-side preprocessing / fallback ops.
if [ -z "${OMP_NUM_THREADS:-}" ]; then
  OMP_NUM_THREADS="$(sysctl -n hw.perflevel0.physicalcpu 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 8)"
  export OMP_NUM_THREADS
fi
export VECLIB_MAXIMUM_THREADS="${VECLIB_MAXIMUM_THREADS:-$OMP_NUM_THREADS}"

"$PYTHON_BIN" - <<'PY'
import platform
import torch

print("Python machine:", platform.machine())
print("Torch:", torch.__version__)
print("MPS built:", torch.backends.mps.is_built())
print("MPS available:", torch.backends.mps.is_available())
print("Selected device:", "mps" if torch.backends.mps.is_available() else "cpu")
PY

"$PYTHON_BIN" scripts/train_sisfall_merged_imu_lstm.py \
  --source ../data/iccas_sensor_lstm/iccas_sisfall_imu_merged.csv \
  --model-dir models \
  --report ../data/iccas_sensor_lstm/sisfall_merged_imu_lstm_metrics.json \
  --epochs "${EPOCHS:-25}" \
  --batch-size "${BATCH_SIZE:-512}" \
  --hidden-size "${HIDDEN_SIZE:-96}" \
  --device auto
