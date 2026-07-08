from fastapi import FastAPI, Depends
from collections import deque
from typing import Dict
import time

from contextlib import asynccontextmanager
from backend.core.scheduler import start_scheduler, stop_scheduler
from backend.services.ai_client import ai_client
from backend.core.security import get_current_user

from backend.routers.realtime import router
from backend.routers.auth import auth_router
from backend.routers.alerts import alert_router
from backend.routers.guardians import guardian_router
from backend.routers.persons import person_router
from backend.routers.telemetry import telemetry_router

from backend.routers.sensor_router import ai_router

import logging
import time
from datetime import datetime
import pytz
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')



# 로그의 시간대를 KST로 변환해주는 커스텀 포매터
class KSTFormatter(logging.Formatter):
    def converter(self, timestamp):
        # 타임스탬프를 KST 시간으로 변환
        dt = datetime.fromtimestamp(timestamp, tz=pytz.utc)
        return dt.astimezone(pytz.timezone('Asia/Seoul')).timetuple()

    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        if datefmt:
            s = time.strftime(datefmt, ct)
        else:
            t = time.strftime("%Y-%m-%d %H:%M:%S", ct)
            s = f"{t}.{int(record.msecs):03d}"
        return s

# 2. 통합 로그 설정 (Uvicorn + App)
def setup_logging():
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    formatter = KSTFormatter(log_format)
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    # Uvicorn 및 root 로거 설정
    for name in [None, "uvicorn", "uvicorn.access", "uvicorn.error"]:
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.handlers = [handler]
        logger.propagate = False

setup_logging()
logger = logging.getLogger("uvicorn") # 통합 로거 사용

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. 백그라운드 스케줄러 시작(AI 연결보다 먼저 가동 -> 의존성 분리)
    logger.info("FastAPI Lifespan: 백그라운드 스케줄러를 시작합니다.")
    start_scheduler()
    
    # 2. AI HTTP 클라이언트 시작
    try:
        logger.info("FastAPI Lifespan: AI 클라이언트 연결을 시도합니다.")
        await ai_client.start()
    except Exception as e:
        logger.error(f"AI 컨테이너 연결 실패 (스케줄러는 정상 가동 유지됨): {e}")    
    
    yield # 서버 실행 중
    
    # 4. 우아한 종료 (Graceful Shutdown)
    stop_scheduler()
    await ai_client.stop()

app = FastAPI(lifespan=lifespan, title="Baguetteboost Backend", version="1.0.0")

# 웹소켓 라우터 등록
app.include_router(router)

# auth 라우터 등록
app.include_router(auth_router)

# guardian 라우터 등록
app.include_router(guardian_router)

# person 라우터 등록
app.include_router(person_router)

# telemetry 라우터 등록
app.include_router(telemetry_router)

# ai 라우터 등록
app.include_router(ai_router)

# alert 라우터 등록
app.include_router(alert_router)

# GPS 인메모리 버퍼 공간 선언
# 구조: {person_id: deque(maxlen=180)} -> 10초 주기 기준 30분 분량 = 180개
# 데이터 포맷 예시: {"lat": 37.123, "lng": 127.123, "timestamp": 1700000000}
gps_inmemory_buffers: Dict[int, deque] = {}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": time.time()}