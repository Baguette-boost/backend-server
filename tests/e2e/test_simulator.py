from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

# backend 디렉터리의 모듈들을 절대 경로로 임포트
from backend.database import get_db
from backend.services.notification_service import NotificationService

from backend.models.alert import AlertLog 

sim_router = APIRouter(prefix="/test/trigger", tags=["Simulator (E2E Test)"])

@sim_router.post("/fall")
async def test_trigger_fall(
    person_id: str, 
    background_tasks: BackgroundTasks, 
    guardian_id: int = 1,
    db: AsyncSession = Depends(get_db)
):
    """
    [시뮬레이터] 가상 낙상 이벤트 트리거
    """
    try:
        alert_id = str(uuid.uuid4())
        
        # 1. DB 로그 저장 (필요시 활성화)
        # new_alert = AlertLog(id=alert_id, person_id=person_id, alert_type="FALL", ...)
        # db.add(new_alert)
        # await db.commit()

        # 2. NotificationService용 페이로드 구성 (notificication_service 규격)
        payload_data = {
            "id": alert_id,
            "type": "fall_detected",
            "message": "[SIMULATION] 낙상이 감지되었습니다."
        }

        # 3. 백그라운드 태스크로 웹소켓 & 푸시 알림 병렬 전송
        background_tasks.add_task(
            NotificationService.broadcast_event,
            guardian_id=guardian_id,
            event_type="alert",
            person_id=person_id,
            payload_data=payload_data
        )

        return {"status": "success", "message": "Fall event pipeline triggered.", "alert_id": alert_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@sim_router.post("/wandering")
async def test_trigger_wandering(
    person_id: str, 
    background_tasks: BackgroundTasks, 
    guardian_id: int = 1,
    db: AsyncSession = Depends(get_db)
):
    """
    [시뮬레이터] 가상 배회 이벤트 트리거
    """
    try:
        alert_id = str(uuid.uuid4())
        
        payload_data = {
            "id": alert_id,
            "type": "zone_exit", 
            "message": "[SIMULATION] 안전 구역 이탈(배회)이 감지되었습니다."
        }

        background_tasks.add_task(
            NotificationService.broadcast_event,
            guardian_id=guardian_id,
            event_type="alert",
            person_id=person_id,
            payload_data=payload_data
        )

        return {"status": "success", "message": "Wandering event pipeline triggered."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
