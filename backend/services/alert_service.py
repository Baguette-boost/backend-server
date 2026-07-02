import logging

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.alert import AlertLog

logger = logging.getLogger(__name__)

# 외부에서 비동기 DB 세션을 주입받아 처리
async def save_wandering_alert(db: AsyncSession, person_id: int, probability: float):
    new_alert = AlertLog(
        person_id=person_id,
        alert_type="GEOFENCE_EXIT",
        message=f"배회 감지 시스템: 안심 구역 이탈 의심 (확률 {probability*100:.1f}%)"
    )

    logger.warning(f"🚨 [배회 감지] personId: {person_id}, 확률: {probability:.3f} - DB 기록 완료")

    db.add(new_alert)