from fastapi import Depends, HTTPException, Header, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, OAuth2PasswordBearer
import jwt

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.database import get_db
from backend.models.guardian import Guardian

import os
from dotenv import load_dotenv

load_dotenv()

# JWT 설정 (환경 변수로 관리)
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key") # 못 읽어오면 "your-secret-key" 사용
ALGORITHM = "HS256"
DEBUG_MODE = os.getenv("DEBUG_MODE").lower() == "true" # 기본값 false(.lower() 부분은 getenv가 내뱉는 문자열 "true", "false"를 boolean 값으로 바꾸기 위함)

print(f"현재 디버그 모드 상태: {DEBUG_MODE}")

# auto_error=False로 설정 -> 토큰이 없어도 401을 즉시 뱉지 않고 의존성 함수로 진입
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

# 1. 일반 유저 인증 의존성
async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
):    
    # [DEBUG MODE] 인증 우회
    if DEBUG_MODE:
        master_phone = "010-0000-0000"
        stmt = select(Guardian).where(Guardian.phone == master_phone)
        master_user = (await db.execute(stmt)).scalar_one_or_none()

        if master_user:
            # "Bearer dummy_token"을 보내든, 아예 안 보내든 마스터 통과
            return master_user
        else:
            raise HTTPException(status_code=500, detail="Debug mode: Master user not found in DB.")
    
    # [PRODUCTION MODE] JWT 디코딩 및 유효성 검증 로직 구현(토큰 반드시 필요)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        guardian_phone: str = payload.get("sub")
        if guardian_phone is None:
            raise HTTPException(status_code=401, details="Invalid token payload")
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"}
        )
    stmt = select(Guardian).where(Guardian.phone == guardian_phone)
    guardian = (await db.execute(stmt)).scalar_one_or_none()

    if guardian is None:
        raise HTTPException(status_code=401, detail="Guardian not found")
    
    return guardian

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