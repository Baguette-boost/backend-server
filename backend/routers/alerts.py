from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from backend.schemas.alert import AlertResponse, UnreadCountResponse
from backend.core.security import get_current_user

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from typing import Annotated
from backend.database import get_db
from backend.models.person import TrackedPerson
from backend.models.alert import AlertLog
from backend.models.guardian import Guardian

alert_router = APIRouter(prefix="/alerts", tags=["Alerts"])

@alert_router.get("", response_model=List[AlertResponse])
async def get_alert_logs(
    limit: int = 50,
    current_guardian: Guardian = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """시간 역순 위험 이력 목록 조회"""
    # 1. current_guardian['id']가 관리하는 환자들의 ID 목록 추출
    stmt_ids = select(TrackedPerson.id).where( # current_guardian['user_id']와 jwt 페이로드 키 부분 확인
        TrackedPerson.guardian_id == current_guardian.id
    )
    id_list = (await db.execute(stmt)).scalars().all()

    # 환자가 없으면 빈 리스트 즉시 반환
    if not id_list:
        return []

    # 2. 해당 환자 ID들에 속하는 로그를 시간 역순(ORDER BY timestamp DESC)으로 DB 조회
    logs = [] # 데이터베이스 결과

    # in_() 연산자 이용해 한 번의 쿼리로 모든 환자 로그 조회
    stmt_logs = select(AlertLog).where(
        Alertlog.person_id.in_(id_list)
    ).order_by(AlertLog.created_at.desc()).limit(limit)

    logs = (await db.execute(stmt_logs)).scalars().all()

    return logs

@alert_router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_alerts_count(current_user: Annotated[dict, Depends(get_current_user)], db: AsyncSession = Depends(get_db)):
    # 보호자가 담당하는 모든 피보호자들의 미읽음 알림 총합 계산
    query = (
        select(func.count(AlertLog.id))
        .join(TrackedPerson, AlertLog.person_id == TrackedPerson.id)
        .where(TrackedPerson.guardian_id == current_user["id"], AlertLog.is_read == False)
    )
    result = await db.execute(query)
    return {"unread_count": result.scalar_one() or 0}

@alert_router.patch("/{id}/read", status_code=status.HTTP_200_OK)
async def mark_alert_as_read(id: int, current_user: Annotated[dict, Depends(get_current_user)], db: AsyncSession = Depends(get_db)):
    await db.execute(update(AlertLog).where(AlertLog.id == id).values(is_read=True))
    await db.commit()
    return {"message": f"Alert {id} marked as read"}

@alert_router.post("/read-all", status_code=status.HTTP_200_OK)
async def mark_all_alerts_as_read(current_user: Annotated[dict, Depends(get_current_user)], db: AsyncSession = Depends(get_db)):
    # 보호자에 속한 모든 환자의 알림 일괄 읽음 처리
    subquery = select(TrackedPerson.id).where(TrackedPerson.guardian_id == current_user["id"])
    result = await db.execute(subquery)
    person_ids = result.scalars().all()
    
    await db.execute(update(AlertLog).where(AlertLog.person_id.in_(person_ids)).values(is_read=True))
    await db.commit()
    return {"message": "All alerts marked as read"}