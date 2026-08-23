from fastapi import FastAPI, Depends
from collections import deque
from typing import Dict
import asyncio
import time

from contextlib import asynccontextmanager
from backend.services.ai_client import ai_client
from backend.core.security import get_current_user
from backend.core import redis_client
from backend.core.websocket import start_redis_listener

from backend.routers.realtime import router
from backend.routers.auth import auth_router
from backend.routers.alerts import alert_router
from backend.routers.guardians import guardian_router
from backend.routers.persons import person_router
from backend.routers.telemetry import telemetry_router

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
    log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    formatter = logging.Formatter(log_format)
    formatter.converter = time.gmtime            # ③ 로그 시각 UTC 통일(DB·앱과 일치) — msec 유지로 순서 파악 용이
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    # ② 단일 핸들러(root)로 일원화 — 중복 줄 방지
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # uvicorn 로거는 자체 핸들러 제거 후 root 로 전파(중복 방지)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    # ① SQL 로그 억제(engine echo=False 와 이중 안전장치)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

setup_logging()
logger = logging.getLogger("uvicorn") # 통합 로거 사용

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 스케줄러(하트비트/낙상판정/배회enroll)는 멀티워커 중복 실행을 피하기 위해
    # 이 프로세스가 아니라 별도 baguetteboost-scheduler 서비스(backend/scheduler_main.py)에서만 돈다.

    # 1. Redis 클라이언트 시작(GPS 버퍼 + WebSocket Pub/Sub 팬아웃이 공유)
    logger.info("FastAPI Lifespan: Redis 클라이언트를 시작합니다.")
    redis_client.start()
    ws_listener_task = asyncio.create_task(start_redis_listener())

    # 2. AI HTTP 클라이언트 시작
    try:
        logger.info("FastAPI Lifespan: AI 클라이언트 연결을 시도합니다.")
        ai_client.start()  # 동기 메서드 — await 하면 'NoneType await' 에러
    except Exception as e:
        logger.error(f"AI 컨테이너 연결 실패: {e}")

    yield # 서버 실행 중

    # 3. 우아한 종료 (Graceful Shutdown)
    ws_listener_task.cancel()
    await ai_client.stop()
    await redis_client.stop()

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

# alert 라우터 등록
app.include_router(alert_router)

# GPS 인메모리 버퍼 공간 선언 (실사용 버퍼는 core/buffer.py 의 patient_gps_buffer)
# 구조: {person_id: deque(maxlen=45)} -> 20초 주기 기준 15분 분량 = 45개
# 데이터 포맷 예시: {"lat": 37.123, "lng": 127.123, "timestamp": 1700000000}
gps_inmemory_buffers: Dict[int, deque] = {}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": time.time()}