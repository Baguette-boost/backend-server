from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status

from pydantic import BaseModel
import jwt

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.database import get_db
from backend.models.guardian import Guardian
from backend.core.security import get_current_user, SECRET_KEY, ALGORITHM, DEBUG_MODE # 해싱/검증 유틸

auth_router = APIRouter(prefix="/auth", tags=["Authentication"])

# Pydantic 스키마 정의
class LoginRequest(BaseModel):
    phone: str
    password: str

class TokenResponse(BaseModel):
    accessToken: str
    tokenType: str = "bearer"

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta if expires_delta else timedelta(minutes=60))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

@auth_router.post("/login", response_model=TokenResponse)
async def login(login_data: LoginRequest, db: AsyncSession = Depends(get_db)):
    # 유저 조회
    stmt = select(Guardian).where(Guardian.phone == login_data.phone)
    result = await db.execute(stmt)
    guardian = result.scalar_one_or_none()

    if not guardian: 
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="guardian not found"
        )
    
    # 🚨 [DEBUG MODE] vs [PRODUCTION MODE] 분기 처리
    if DEBUG_MODE:
        # 디버그 모드: 하드코딩된 패스워드 무조건 통과
        if login_data.password != "master1234!": 
            raise HTTPException(status_code=401, detail="Incorrect debug password")
    else:
        # 프로덕션 모드 (False): 실제 DB 비밀번호(해시)와 비교
        # 주의: db 적재 시 비밀번호를 해싱해서 넣지 않았다면 여기서 에러가 날 수 있음
        if not (login_data.password == user.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect phone number or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # JWT 발급
    access_token_expires = timedelta(minutes=60 * 24) # 24시간 넉넉하게
    access_token = create_access_token(
        data={"sub": guardian.phone}, expires_delta=access_token_expires
    )

    return TokenResponse(accessToken=access_token)

@auth_router.get("/test-bypass")
async def test_bypass_logic(current_guardian: Guardian = Depends(get_current_user)):
    """
    [디버깅용] 인증 우회(Mocking)가 잘 작동하는지 확인하는 엔드포인트
    """
    return {
        "message": "인증 샌드박스 통과 성공!",
        "phone": current_user.phone,
        "name": current_user.name
    }