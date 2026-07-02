from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from backend.core.websocket import manager
from backend.core.security import get_current_user # 자체 구현된 JWT 검증 로직 임포트

router = APIRouter()

async def get_guardian_from_token(token: str) -> int:
    """JWT 토큰에서 guardian_id를 추출하는 가상 함수"""
    # payload = get_current_user(token)
    # return payload.get("user_id")
    
    # 개발 및 테스트를 위해 임시로 토큰 자체를 ID로 사용 (예: ?token=1)
    if token.isdigit():
        return int(token)
    raise ValueError("Invalid token")

@router.websocket("/realtime")
async def websocket_endpoint(websocket: WebSocket):
    # 커스텀 헤더에서 accessToken 추출
    # WebSocket은 초기 Handshake 시 headers를 가져올 수 있음
    access_token = websocket.headers.get("accessToken")
    
    if not access_token:
        # 토큰이 없으면 연결 거부
        await websocket.close(code=1008) 
        return
        
    try:
        # 1. Bearer 토큰(여기서는 Query Param) 검증 및 ID 추출
        guardian_id = await get_guardian_from_token(token)
    except Exception:
        await websocket.close(code=1008) # Policy Violation
        return

    # 2. 매니저에 연결 등록
    await manager.connect(websocket, guardian_id)
    try:
        while True:
            # 클라이언트로부터의 메시지 수신 (필요시 Ping-Pong 처리)
            data = await websocket.receive_text()
            # 서비스 요구사항에 따라 클라이언트 발송 메시지 처리 로직 추가
    except WebSocketDisconnect:
        manager.disconnect(websocket, guardian_id)
