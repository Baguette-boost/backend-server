FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODEL_PATH=models/iccas_final_hybrid_lstm_imu_fall.pt

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY models/iccas_final_hybrid_lstm_imu_fall.pt models/iccas_final_hybrid_lstm_imu_fall.pt
COPY models/iccas_final_hybrid_lstm_imu_fall.json models/iccas_final_hybrid_lstm_imu_fall.json
COPY scripts/hybrid_imu_fall_api.py scripts/hybrid_imu_fall_api.py

EXPOSE 8001

CMD ["uvicorn", "scripts.hybrid_imu_fall_api:app", "--host", "0.0.0.0", "--port", "8001"]
