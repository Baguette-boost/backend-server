from fastapi import Depends, HTTPException, Header, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

from passlib.context import CryptContext

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.config import settings
from backend.database import get_db
from backend.models.guardian import Guardian
from backend.models.person import TrackedPerson
from backend.schemas.guardian import TokenResponse

# JWT / 설정 값은 config.py의 settings를 단일 소스로 사용
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"
DEBUG_MODE = settings.DEBUG_MODE

print(f"현재 디버그 모드 상태: {DEBUG_MODE}")

# 비밀번호 해싱 컨텍스트 (프로덕션 전용, 디버그는 평문)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

# HTTPBearer: Swagger Authorize에 '토큰 붙여넣기' 필드 제공. auto_error=False로 미인증도 함수 진입
bearer_scheme = HTTPBearer(auto_error=False)


def _parse_debug_token(token: str, expected_suffix: str) -> int:
    """
    디버그 더미 토큰 '{guardian_id}_{SUFFIX}' 를 파싱한다.
    접미사 검증 + id int 파싱을 DB 조회보다 먼저 수행해, 빈/공백/형식오류 토큰을
    access_token="" 같은 행에 오매칭시키지 않고 401로 선차단한다.
    """
    parts = token.rsplit("_", 1)
    if len(parts) != 2 or parts[1] != expected_suffix:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return int(parts[0])
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ─────────────────────────────────────────────────────────────────────────────
# 인증 조회 키 계약(Contract):
#   - DEBUG      : 토큰 = "{guardian_id}_ACCESS" (더미). Guardian.access_token 컬럼과
#                  정확히 일치하는 행을 조회한다(로그아웃 시 access_token=""로 무효화됨).
#   - PRODUCTION : 토큰 = 진짜 JWT. payload["sub"] = phone 으로 Guardian을 조회한다.
#                  ※ create_jwt_tokens가 아직 더미라 프로덕션 경로는 미완성(보류).
# ─────────────────────────────────────────────────────────────────────────────

# 1. 일반 유저 인증 의존성
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db)
):
    # Authorization: Bearer <token> 에서 토큰만 추출 (없으면 credentials=None)
    token = credentials.credentials if credentials else None
    # 토큰이 없으면 모드와 무관하게 401
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # [DEBUG MODE] 더미 토큰 검증(JWT 서명 검증만 건너뜀)
    if DEBUG_MODE:
        guardian_id = _parse_debug_token(token, expected_suffix="ACCESS")
        stmt = select(Guardian).where(Guardian.access_token == token)
        guardian = (await db.execute(stmt)).scalar_one_or_none()
        if guardian is None or guardian.id != guardian_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return guardian

    # [PRODUCTION MODE] JWT 디코딩 및 유효성 검증 (토큰 반드시 필요)
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        guardian_phone: str = payload.get("sub")
        if guardian_phone is None:
            raise HTTPException(status_code=401, detail="Invalid token payload")
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    stmt = select(Guardian).where(Guardian.phone == guardian_phone)
    guardian = (await db.execute(stmt)).scalar_one_or_none()

    if guardian is None:
        raise HTTPException(status_code=401, detail="Guardian not found")

    return guardian

# 2. HW 디바이스 인증 의존성 (Authorization: Device <deviceToken>) — telemetry 라우터에서 사용
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

# 3. 디바이스 토큰 검증 헬퍼 (POST /persons/verify 전용) — 조회만 하고 부작용 없음
async def verify_device_by_token(device_token: str, db: AsyncSession) -> int:
    """TrackedPerson.device_token과 대조해 매핑된 person.id를 반환. 검증 실패 시 400."""
    stmt = select(TrackedPerson).where(TrackedPerson.device_token == device_token)
    person = (await db.execute(stmt)).scalar_one_or_none()
    if person is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Device Token")
    return person.id


async def verify_refresh_token(refresh_token: str, db: AsyncSession) -> int:
    """
    [DEBUG] 더미 refresh 토큰 검증.
    접미사(REFRESH) + id 파싱 후 Guardian.refresh_token == token 무효화 비교까지 수행한다
    (로그아웃 시 refresh_token=""로 무효화되므로 재발급이 막힌다).
    ※ 프로덕션(진짜 JWT) refresh 경로는 보류.
    """
    guardian_id = _parse_debug_token(refresh_token, expected_suffix="REFRESH")
    stmt = select(Guardian).where(Guardian.refresh_token == refresh_token)
    guardian = (await db.execute(stmt)).scalar_one_or_none()
    if guardian is None or guardian.id != guardian_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    return guardian.id


async def create_jwt_tokens(id: int):
    # TODO: 디버그 동작 완성 후 프로덕션용 진짜 JWT 발급으로 교체 예정 (현재는 더미 토큰)
    return TokenResponse(
        access_token=str(id) + "_ACCESS",
        refresh_token=str(id) + "_REFRESH",
        token_type="Bearer",
    )
