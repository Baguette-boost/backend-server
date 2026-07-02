from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
from backend.services.notification_service import NotificationService

router = APIRouter()

class AlertRequest(BaseModel):
    guardian_id: int
    alert_type: str # zone_exit | low_battery | fall_detected | offline
    patient_name: str
    location: str

@router.post("/trigger")
async def trigger_mock_alert(request: AlertRequest, background_tasks: BackgroundTasks):
    """
    AI 판단 파이프라인에서 위험을 감지했다고 가정하고 호출하는 테스트 엔드포인트.
    API 응답을 즉시 반환하고 알림 처리는 백그라운드로 넘겨 API Latency를 최소화합니다.
    """
    extra_data = {"location": request.location}
    
    # BackgroundTasks를 활용하여 요청은 즉시 응답하고 처리는 비동기로 진행
    background_tasks.add_task(
        NotificationService.process_danger_alert,
        request.guardian_id,
        request.alert_type,
        request.patient_name,
        extra_data
    )
    
    return {"status": "success", "message": "Alert processing started in background."}
