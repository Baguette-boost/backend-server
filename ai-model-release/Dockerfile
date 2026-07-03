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
# 무거운 원본 데이터(xlsx)나 불필요한 캐시 파일은 제외하고 실행에 필요한 디렉토리만 복사
COPY scripts/ ./scripts/
COPY models/ ./models/
COPY ICCAS_total_data_with_fall.xlsx .

# 7. 기본 실행 명령어 설정 (Live 모드)
# 컨테이너 실행 시 실시간 추론 스크립트를 구동하고, 분석 결과를 백엔드 컨테이너로 전송하도록 세팅
# 호스트명은 docker-compose.yml의 백엔드 컨테이너 이름(baguetteboost-backend)을 사용
CMD ["python", "scripts/realtime_sensor_lstm.py", "replay", \
     "--source", "ICCAS_total_data_with_fall.xlsx", \
     "--sheet", "data", \
     "--model", "models/iccas_sensor_lstm_fall.pt", \
     "--backend-url", "http://baguetteboost-backend:8000/api/sensor-result", \
     "--device", "cpu"]