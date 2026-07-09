# 1. 베이스 이미지 설정: README에 명시된 파이썬 버전 및 경량화(slim) 이미지 사용
FROM python:3.11-slim

# 2. 컨테이너 내부 작업 디렉토리 설정
WORKDIR /app

# 3. 필수 시스템 패키지 설치 (에러 방지용)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 4. 파이썬 환경 변수 설정
# PYTHONUNBUFFERED=1 설정 시 파이썬 출력이 버퍼링되지 않아 도커 로그를 실시간으로 빠르게 확인할 수 있음
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# 5. 의존성 설치 (레이어 캐싱 최적화)
# requirements.txt를 먼저 복사하여 패키지를 설치하면, 이후 소스코드가 수정되어도 이 단계는 캐싱되어 빌드가 훨씬 빠름
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 6. 소스 코드 및 모델 복사
# predict_server.py 는 scripts/realtime_sensor_lstm.build_features 를 재사용하므로 scripts/ 도 함께 복사
COPY scripts/ ./scripts/
COPY models/ ./models/
COPY predict_server.py .

# 7. 기본 실행 명령어 (B 파이프라인: 추론 HTTP 서버)
# 백엔드(ai_client.predict)가 POST /predict 로 호출하면 컨테이너 안에서 추론해 결과를 반환한다.
# AI_SERVICE_URL(=http://baguetteboost-ai:5000) 과 포트를 맞춘다.
EXPOSE 5000
CMD ["uvicorn", "predict_server:app", "--host", "0.0.0.0", "--port", "5000"]