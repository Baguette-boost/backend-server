from fastapi import WebSocket
from typing import Dict, List
import json
import logging

from backend.core import redis_client

logger = logging.getLogger(__name__)

# 멀티워커 팬아웃용 채널: 소켓이 붙은 워커와 알림을 만든 워커가 다를 수 있으므로,
# 로컬 전송(send_personal_message) 대신 이 채널로 발행해 모든 워커가 받게 한다.
WS_CHANNEL = "ws:alerts"

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


async def publish(payload: dict, guardian_id: int) -> None:
    """알림 발신 호출부는 이걸 쓴다(멀티워커에서 어느 워커가 처리하든 팬아웃되도록).
    실제 로컬 전송은 워커마다 도는 start_redis_listener()가 담당한다."""
    await redis_client.client.publish(
        WS_CHANNEL, json.dumps({"guardian_id": guardian_id, "payload": payload})
    )


async def start_redis_listener() -> None:
    """워커마다 하나씩 기동(main.py lifespan). 채널을 구독해 메시지를 받을 때마다
    로컬로 send_personal_message를 호출한다 — 이 워커가 해당 guardian_id의 소켓을
    안 들고 있으면 send_personal_message가 이미 조용히 무시하므로 별도 라우팅 불필요."""
    pubsub = redis_client.client.pubsub()
    await pubsub.subscribe(WS_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = json.loads(message["data"])
                await manager.send_personal_message(data["payload"], data["guardian_id"])
            except Exception as e:
                logger.error(f"WS Pub/Sub 메시지 처리 실패: {e}")
    finally:
        await pubsub.unsubscribe(WS_CHANNEL)