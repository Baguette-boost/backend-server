# Base image: Python Slim(경량화)
FROM python:3.11-slim

# 시스템 환경 변수 설정
# Python이 pyc 파일을 쓰지 않도록 설정하고, stdout/stderr를 버퍼링 없이 출력
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

# 작업 디렉토리 설정
WORKDIR /app

# 시스템 패키지 업데이트 및 필수 의존성 설치 (설치 후 캐시 삭제 -> 이미지 경량화)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 파이썬 패키지 의존성 복사 및 설치
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 애플리케이션 소스 코드 복사
COPY . .

# 8000번 포트를 쓸 것임을 명시
EXPOSE 8000

# 컨테이너 헬스체크 설정 (FastAPI 서버가 정상 응답하는지 확인)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/me || exit 1

# 컨테이너 실행 명령어(FastAPI를 uvicorn으로 구동)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]