from fastapi import Depends, HTTPException, Header, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security_bearer = HTTPBearer()

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