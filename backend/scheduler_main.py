"""독립 스케줄러 프로세스 진입점 (Issue #4 2단계).

멀티워커(gunicorn -w 4)로 가면 FastAPI lifespan이 워커마다 한 번씩 돌아 APScheduler 잡도
워커 수만큼 중복 실행된다(오프라인/낙상판정 중복 알림, 배회 enroll 중복 호출). 이를 막기
위해 스케줄러만 별도 프로세스(HTTP 서빙 없음, docker-compose의 baguetteboost-scheduler
서비스)로 분리한다. APScheduler(AsyncIOScheduler)는 백그라운드 스레드에서 잡을 실행하므로
메인 코루틴은 그냥 살아있기만 하면 된다.
"""
import asyncio
import logging
import time

from backend.core.scheduler import start_scheduler, stop_scheduler
from backend.services.ai_client import ai_client
from backend.core import redis_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logging.Formatter.converter = time.gmtime  # 로그 시각 UTC 통일(backend와 동일 규약)
logger = logging.getLogger("scheduler_main")


async def main() -> None:
    logger.info("독립 스케줄러 프로세스를 시작합니다.")
    # check_wandering(Issue #6)이 공용 ai_client(배회 /detect)와 GPS 버퍼(Redis)를 쓰므로
    # 이 프로세스에서도 초기화해야 한다 — 안 하면 client가 None이라 조용히 실패/에러난다.
    redis_client.start()
    ai_client.start()
    start_scheduler()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        stop_scheduler()
        await ai_client.stop()
        await redis_client.stop()


if __name__ == "__main__":
    asyncio.run(main())
