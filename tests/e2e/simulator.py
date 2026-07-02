from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.services.notification_service import NotificationService 
from backend.models.alert import AlertLog
from datetime import datetime
import uuid

sim_router = APIRouter(prefix="/test/trigger", tags=["Simulator (E2E Test)"])

@sim_router.post("/fall")
async def trigger_fall(
    person_id: str = "123",
    guardian_id: int = 1, 
    background_tasks: BackgroundTasks = BackgroundTasks(), 
    db: AsyncSession = Depends(get_db)
):
    """
    [시뮬레이터] 가상 낙상 이벤트 트리거
    호출 시 낙상 로그 저장, 웹소켓 브로드캐스트, Expo 푸시 알림을 원스톱으로 실행합니다.
    """
    try:
        alert_id = str(uuid.uuid4())
        # # DB에 경고 로그 저장 (비동기)
        # new_alert = AlertLog(
        #     id=alert_id,
        #     person_id=person_id,
        #     alert_type="FALL",
        #     timestamp=datetime.utcnow(),
        #     message="[SIMULATION] 낙상이 감지되었습니다."
        # )
        # db.add(new_alert)
        # await db.commit()
        # await db.refresh(new_alert)

        # Expo 푸시 알림 발송 (백그라운드 처리로 논블로킹 유지)
        push_title = "🚨 긴급: 낙상 감지"
        push_body = f"어르신(ID: {person_id})의 낙상이 감지되었습니다. 즉시 확인 바랍니다."
        background_tasks.add_task(NotificationService.broadcast_event, guardian_id=guardian_id, event_type="alert", person_id=person_id, payload_data={ "type": "status", "personId": "1", "status": "alert" })

        return {"status": "success", "message": "Fall event triggered pipeline successfully.", "alert_id": alert_id}

    except Exception as e:
        # await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@sim_router.post("/wandering")
async def trigger_wandering_event(
    person_id: str = "123",
    guardian_id: int = 1, 
    background_tasks: BackgroundTasks = BackgroundTasks(), 
    db: AsyncSession = Depends(get_db)
):
    """
    [시뮬레이터] 가상 배회 이벤트 트리거
    안전 구역 이탈(Geofence out) 등의 배회 상황을 시뮬레이션합니다.
    """
    try:
        alert_id = str(uuid.uuid4())
        # new_alert = AlertLog(
        #     id=alert_id,
        #     person_id=person_id,
        #     alert_type="WANDERING",
        #     timestamp=datetime.utcnow(),
        #     message="[SIMULATION] 안전 구역 이탈(배회)이 감지되었습니다."
        # )
        # db.add(new_alert)
        # await db.commit()
        
        background_tasks.add_task(NotificationService.broadcast_event, guardian_id=guardian_id, event_type="alert", person_id=person_id, payload_data={ "type": "status", "personId": "1", "status": "alert" })

        return {"status": "success", "message": "Wandering event triggered pipeline successfully."}

    except Exception as e:
        # await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
