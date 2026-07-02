from fastapi import APIRouter, Depends
from typing import List
from schemas.alert import AlertResponse
from core.security import get_current_user

router = APIRouter(prefix="/alerts", tags=["Alerts"])

@router.get("", response_model=List[AlertResponse])
async def get_alert_logs(
    limit: int = 50,
    current_guardian: dict = Depends(get_current_user)
):
    """시간 역순 위험 이력 목록 조회"""
    # 1. current_guardian['id']가 관리하는 환자들의 ID 목록 추출
    # 2. 해당 환자 ID들에 속하는 로그를 시간 역순(ORDER BY timestamp DESC)으로 DB 조회
    logs = [
        # 데이터베이스 결과
    ]
    return logs