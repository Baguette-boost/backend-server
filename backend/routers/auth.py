from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Header, status

from pydantic import BaseModel
import jwt

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from backend.database import get_db
from backend.models.guardian import Guardian
from backend.models.setting import UserSettings
from backend.core.security import get_current_user, SECRET_KEY, ALGORITHM, DEBUG_MODE, create_jwt_tokens, verify_refresh_token, get_password_hash, verify_password # 해싱/검증 유틸
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
    # 디버그: 평문 저장 / 프로덕션: bcrypt 해시 저장
    password_to_store = payload.password if DEBUG_MODE else get_password_hash(payload.password)
    new_guardian = Guardian(
        password=password_to_store,
        phone=payload.phone,
        name=payload.name,
        expo_token=payload.expo_token,
        access_token="",
        refresh_token=""
    )
    db.add(new_guardian)
    await db.commit()
    await db.refresh(new_guardian)

    # 기본 사용자 설정 행 생성
    db.add(UserSettings(user_id=new_guardian.id))
    await db.commit()

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
        # 디버그 모드: 평문 저장 -> 평문 비교
        password_ok = (login_data.password == guardian.password)
    else:
        # 프로덕션 모드: bcrypt 해시 비교
        password_ok = verify_password(login_data.password, guardian.password)

    if not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect phone number or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # # JWT 발급
    # access_token_expires = timedelta(minutes=60 * 24) # 24시간 넉넉하게
    # access_token = create_access_token(
    #     data={"sub": guardian.phone}, expires_delta=access_token_expires
    # )
    result = await create_jwt_tokens(guardian.id)
    stmt = (
        update(Guardian)
        .where(Guardian.id == guardian.id)
        .values(access_token=result.access_token, refresh_token=result.refresh_token)
    )
    await db.execute(stmt)
    await db.commit()

    return result

    # return TokenResponse(access_token=access_token)

@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_token: str = Header(...), db: AsyncSession = Depends(get_db)):
    user_id = await verify_refresh_token(refresh_token, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    return await create_jwt_tokens(id=user_id)

@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(current_user: Annotated[Guardian, Depends(get_current_user)], db: AsyncSession = Depends(get_db)):
    # Redis 또는 DB내 Refresh Token 무효화 처리 진행 예정 단락
    stmt = (
        update(Guardian)
        .where(Guardian.id == current_user.id)
        .values(access_token="", refresh_token="")
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
        "phone": current_guardian.phone,
        "name": current_guardian.name
    }