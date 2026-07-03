from fastapi import APIRouter, Depends, HTTPException
from typing import List
from backend.schemas.alert import AlertResponse
from backend.core.security import get_current_user

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

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