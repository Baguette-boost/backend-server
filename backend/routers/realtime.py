from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from backend.core.websocket import manager
from backend.core.security import resolve_guardian_from_access_token # HTTP와 동일한 JWT 검증 로직 공유
from backend.database import get_independent_session
from backend.models.guardian import Guardian

router = APIRouter()

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
        # REST(get_current_user)와 동일한 검증 로직(DEBUG 더미 토큰 / production JWT)
        async with get_independent_session() as db:
            guardian = await resolve_guardian_from_access_token(access_token, db)
        guardian_id = guardian.id
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
