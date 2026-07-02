from fastapi import FastAPI, Depends
from collections import deque
from typing import Dict
import time

from contextlib import asynccontextmanager
from core.scheduler import start_scheduler, stop_scheduler
from services.ai_client import ai_client
from core.security import get_current_user, verify_device_token
from routers.realtime import router 
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. AI HTTP 클라이언트 시작
    ai_client.start()
    
    # 2. 백그라운드 스케줄러 시작
    start_scheduler()
    
    yield # 서버 실행 중
    
    # 3. 우아한 종료 (Graceful Shutdown)
    stop_scheduler()
    await ai_client.stop()

app = FastAPI(lifespan=lifespan, title="Baguetteboost Backend", version="1.0.0")

# 웹소켓 라우터 등록
app.include_router(router)

# GPS 인메모리 버퍼 공간 선언
# 구조: {person_id: deque(maxlen=180)} -> 10초 주기 기준 30분 분량 = 180개
# 데이터 포맷 예시: {"lat": 37.123, "lng": 127.123, "timestamp": 1700000000}
gps_inmemory_buffers: Dict[int, deque] = {}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": time.time()}

# 인증 테스트용 임시 라우터
@app.get("/api/v1/guardian/me", dependencies=[Depends(get_current_user)])
async def get_guardian_profile():
    return {"message": "보호자 인증 성공"}

@app.post("/api/v1/device/ping", dependencies=[Depends(verify_device_token)])
async def device_ping():
    return {"message": "디바이스 인증 성공"}
