from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status

from pydantic import BaseModel
import jwt

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from backend.database import get_db
from backend.models.guardian import Guardian
from backend.core.security import get_current_user, SECRET_KEY, ALGORITHM, DEBUG_MODE, create_jwt_tokens, verify_refresh_token # 해싱/검증 유틸
from backend.schemas.guardian import TokenResponse, SignUpRequest, LoginRequest

from typing import Annotated

auth_router = APIRouter(prefix="/auth", tags=["Authentication"])

# Pydantic 스키마 정의

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta if expires_delta else timedelta(minutes=60))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

@auth_router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignUpRequest, db: AsyncSession = Depends(get_db)):
    # 유저 조회
    stmt = select(Guardian).where(Guardian.phone == payload.phone)
    result = await db.execute(stmt)
    guardian = result.scalar_one_or_none()

    if guardian:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 사용 중인 아이디입니다."
        )        

    # 유저 생성 ORM 로직    
    new_guardian = Guardian(
        password=payload.password,
        phone=payload.phone,
        name=payload.name,
        access_token="",
        refresh_token=""
    )
    db.add(new_guardian)
    await db.commit()
    await db.refresh(new_guardian)

    # 토큰 발행
    tokens = await create_jwt_tokens(new_guardian.id)

    stmt = (
        update(Guardian)
        .where(Guardian.id == new_guardian.id)
        .values(access_token=tokens.access_token, refresh_token=tokens.refresh_token)
    )
    await db.execute(stmt)
    await db.commit()

    return tokens

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

    return TokenResponse(access_token=access_token)

@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_token: str, db: AsyncSession = Depends(get_db)):
    user_id = await verify_refresh_token(refresh_token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    return await create_jwt_tokens(user_id=user_id)

@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(current_user: Annotated[dict, Depends(get_current_user)]):
    # Redis 또는 DB내 Refresh Token 무효화 처리 진행 예정 단락
    stmt = (
        update(Guardian)
        .where(Guardian.id == current_user["id"])
        .values(refresh_token="")
    )
    await db.execute(stmt)
    await db.commit()
    return

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