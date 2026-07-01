from fastapi import FastAPI, Depends, HTTPException, Header, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from collections import deque
from typing import Dict
import time

from config import settings
from database import get_db

app = FastAPI(title="Baguetteboost Backend", version="1.0.0")

security_bearer = HTTPBearer()

# GPS 인메모리 버퍼 공간 선언
# 구조: {person_id: deque(maxlen=180)} -> 10초 주기 기준 30분 분량 = 180개
# 데이터 포맷 예시: {"lat": 37.123, "lng": 127.123, "timestamp": 1700000000}
gps_inmemory_buffers: Dict[int, deque] = {}

# 1. 일반 유저 인증 의존성 (Bearer 토큰)
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security_bearer)):
    token = credentials.credentials
    # TODO: JWT 디코딩 및 유효성 검증 로직 구현 예정
    if token == "invalid-token":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return {"user_id": 1, "role": "guardian"}

# 2. HW 디바이스 인증 의존성 (Authorization: Device <deviceToken>)
async def verify_device_token(authorization: str = Header(...)):
    if not authorization.startswith("Device "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid authorization header format. Expected 'Device <token>'"
        )
    
    device_token = authorization.split(" ")[1]
    # TODO: DB 조회를 통한 등록된 device_token 유효성 검증 로직 구현 예정
    if device_token == "invalid-device-token":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unregistered Device")
        
    return {"device_id": "HW-DEV-001", "person_id": 1}


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