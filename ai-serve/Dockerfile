# AI 추론 HTTP 서버(B/pull) 이미지.
# 빌드 컨텍스트는 repo 루트(.)이며, 팀원 원본 디렉터리(ai-model-release)의
# 모델·스크립트를 '읽기 전용으로 COPY'만 한다 — 원본은 수정하지 않는다.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# 의존성 먼저 설치(레이어 캐싱)
COPY ai-serve/requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 팀원 원본에서 모델과 피처엔지니어링 스크립트를 가져온다.
# predict_server 는 scripts/realtime_sensor_lstm.build_features 를 재사용하므로 scripts/ 필요.
COPY ai-model-release/scripts/ ./scripts/
COPY ai-model-release/models/ ./models/

# 우리 서버 코드
COPY ai-serve/predict_server.py .

# 추론 HTTP 서버 (AI_SERVICE_URL=http://baguetteboost-ai:5000 과 포트 일치)
EXPOSE 5000
CMD ["uvicorn", "predict_server:app", "--host", "0.0.0.0", "--port", "5000"]
