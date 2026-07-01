import logging

logger = logging.getLogger(__name__)

async def save_wandering_alert(person_id: int, probability: float):
    # TODO: 비동기 DB 세션을 주입받아 처리
    # 예: async with get_db() as db:
    #         await db.execute(insert(AlertLog).values(...))
    logger.warning(f"🚨 [배회 감지] personId: {person_id}, 확률: {probability:.3f} - DB 기록 완료")