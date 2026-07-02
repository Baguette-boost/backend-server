from fastapi import WebSocket
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        # guardian_id를 key로, 연결된 WebSocket 객체들의 리스트를 value로 가짐
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, guardian_id: int):
        await websocket.accept()
        if guardian_id not in self.active_connections:
            self.active_connections[guardian_id] = []
        self.active_connections[guardian_id].append(websocket)
        logger.info(f"Guardian {guardian_id} connected. Total sessions: {len(self.active_connections[guardian_id])}")

    def disconnect(self, websocket: WebSocket, guardian_id: int):
        if guardian_id in self.active_connections:
            self.active_connections[guardian_id].remove(websocket)
            if not self.active_connections[guardian_id]:
                del self.active_connections[guardian_id]
            logger.info(f"Guardian {guardian_id} disconnected.")

    async def send_personal_message(self, message: dict, guardian_id: int):
        """특정 보호자의 모든 연결된 디바이스로 메시지 전송"""
        if guardian_id in self.active_connections:
            # 여러 세션이 열려있을 수 있으므로 모두에게 전송
            for connection in self.active_connections[guardian_id]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error(f"Error sending message to {guardian_id}: {e}")
                    # 실패한 커넥션 정리 로직 추가 가능

# 싱글톤 인스턴스 생성
manager = ConnectionManager()