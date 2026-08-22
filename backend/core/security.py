from fastapi import Depends, HTTPException, Header, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader
import jwt
import secrets
from datetime import datetime, timedelta, timezone

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
#   - PRODUCTION : 토큰 = 진짜 서명된 JWT(exp 포함). payload["sub"] = phone 으로 Guardian을
#                  조회한다(스테이트리스 — DB의 access_token 컬럼은 보지 않는다).
# ─────────────────────────────────────────────────────────────────────────────

async def resolve_guardian_from_access_token(token: str, db: AsyncSession) -> Guardian:
    """access_token 문자열로부터 Guardian을 조회한다. HTTP(get_current_user)와
    WebSocket(/realtime)이 동일한 검증 로직을 쓰기 위해 공유하는 함수."""
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


# 1. 일반 유저 인증 의존성
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db)
) -> Guardian:
    # Authorization: Bearer <token> 에서 토큰만 추출 (없으면 credentials=None)
    token = credentials.credentials if credentials else None
    return await resolve_guardian_from_access_token(token, db)

# 2. HW 디바이스 인증 의존성 (Authorization: Device <deviceToken>) — telemetry 라우터에서 사용
# 일반 Header(...) 대신 APIKeyHeader 보안 스킴을 써야 Swagger Authorize에 입력칸이 노출되고
# 요청에 Authorization 헤더가 실린다. auto_error=False로 미인증 시 아래에서 401을 직접 반환.
device_token_scheme = APIKeyHeader(
    name="Authorization",
    scheme_name="DeviceToken",
    auto_error=False,
    description="형식: Device <device_token>",
)

async def _find_person_by_device_token(device_token: str, db: AsyncSession) -> TrackedPerson | None:
    """TrackedPerson.device_token 과 대조해 매핑된 person 을 반환(없으면 None)."""
    stmt = select(TrackedPerson).where(TrackedPerson.device_token == device_token)
    return (await db.execute(stmt)).scalar_one_or_none()


async def verify_device_token(
    authorization: str | None = Depends(device_token_scheme),
    db: AsyncSession = Depends(get_db),
) -> int:
    """telemetry 라우터 인증 의존성. 유효한 device_token 이면 매핑된 person.id 를 반환한다."""
    if not authorization or not authorization.startswith("Device "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected 'Device <token>'"
        )

    device_token = authorization.split(" ")[1]
    person = await _find_person_by_device_token(device_token, db)
    if person is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Device Token")

    return person.id

# 3. 디바이스 토큰 검증 헬퍼 (POST /persons/verify 전용) — 조회만 하고 부작용 없음
async def verify_device_by_token(device_token: str, db: AsyncSession) -> int:
    """TrackedPerson.device_token과 대조해 매핑된 person.id를 반환. 검증 실패 시 400."""
    person = await _find_person_by_device_token(device_token, db)
    if person is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Device Token")
    return person.id


async def verify_refresh_token(refresh_token: str, db: AsyncSession) -> Guardian:
    """
    Refresh 토큰을 검증하고 매핑된 Guardian을 반환한다(재발급 시 phone 등 추가 조회 없이
    바로 create_jwt_tokens에 넘기기 위해 int가 아닌 Guardian을 반환).

    [DEBUG] 접미사(REFRESH) + id 파싱 후 Guardian.refresh_token == token 무효화 비교까지 수행.
    [PRODUCTION] refresh_token은 서명 토큰이 아닌 불투명 랜덤값이라 접미사 파싱 없이
    Guardian.refresh_token == token 으로 직접 대조한다.
    로그아웃 시 refresh_token=""로 무효화되므로 두 모드 모두 재발급이 막힌다.
    """
    if DEBUG_MODE:
        guardian_id = _parse_debug_token(refresh_token, expected_suffix="REFRESH")
        stmt = select(Guardian).where(Guardian.refresh_token == refresh_token)
        guardian = (await db.execute(stmt)).scalar_one_or_none()
        if guardian is None or guardian.id != guardian_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token",
            )
        return guardian

    stmt = select(Guardian).where(Guardian.refresh_token == refresh_token)
    guardian = (await db.execute(stmt)).scalar_one_or_none()
    if guardian is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    return guardian


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """서명된 JWT access token 을 발급한다(HS256, exp 포함)."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def create_jwt_tokens(guardian: Guardian) -> TokenResponse:
    """DEBUG_MODE: 더미 토큰("{id}_ACCESS"/"{id}_REFRESH").
    PRODUCTION: 서명된 JWT access token + 불투명 랜덤 refresh token."""
    if DEBUG_MODE:
        return TokenResponse(
            access_token=f"{guardian.id}_ACCESS",
            refresh_token=f"{guardian.id}_REFRESH",
            token_type="Bearer",
        )

    access_token = create_access_token({"sub": guardian.phone})
    refresh_token = secrets.token_urlsafe(32)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="Bearer",
    )
