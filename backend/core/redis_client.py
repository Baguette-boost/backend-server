import redis.asyncio as redis
from backend.config import settings

# 멀티워커 전 프로세스가 공유해야 하는 상태(GPS 버퍼, WebSocket Pub/Sub 팬아웃)를
# 담아두는 단일 Redis 클라이언트. ai_client.py와 동일하게 lifespan에서 start/stop.
client: redis.Redis | None = None


def start() -> None:
    global client
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)


async def stop() -> None:
    global client
    if client is not None:
        await client.aclose()
        client = None
