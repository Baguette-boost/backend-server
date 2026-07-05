from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from backend.services.notification_service import send_emergency_push
from backend.database import get_db
from backend.models.telemetry import GpsLog
from backend.models.person import TrackedPerson
from backend.models.guardian import Guardian

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

ai_router = APIRouter()

# AI 모델이 전송하는 데이터 스키마 정의
class SensorResult(BaseModel):
    server_time: Optional[str] = None
    person_id: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    fall_detected: bool = False
    wandering_detected: bool = False
    risk_level: Optional[str] = "LOW"
    detection_type: Optional[str] = "NORMAL"

@ai_router.post("/api/sensor-result")
async def receive_sensor_result(
    result: SensorResult,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    try:
        # 1. DB 저장 로직 (최신 데이터 갱신 및 히스토리 저장)
        stmt = select(GpsLog.id).where(
            GpsLog.person_id == int(result.person_id),
            GpsLog.created_at == result.server_time
        )
        gps_id = (await db.execute(stmt)).scalar_one_or_none()

        if gps_id:
            # 기존 gps 데이터가 있다면 업데이트
            stmt = update(GpsLog).where(
                GpsLog.id == gps_id
            ).values(
                is_fall_detected=result.fall_detected,
                is_wandering_detected=result.wandering_detected
            )
            await db.execute(stmt)
            await db.commit()
        else:
            # 기존 데이터가 없다면 추가(임시)
            new_gps = GpsLog(
                person_id=int(result.person_id),
                latitude=result.lat,
                longitude=result.lng,
                battery=-1,
                is_fall_detected=result.fall_detected,
                is_wandering_detected=result.wandering_detected
            )
            db.add(new_gps)
            await db.commit() 
            await db.refresh(new_gps)

        # 2. 위험 상황 판별 및 비동기 푸시 알림 트리거
        if result.fall_detected or result.wandering_detected:
            alert_type = "fall_detected" if result.fall_detected else "zone_exit"
            
            # result.person_id(환자 ID)를 기반으로 DB에서 어르신 정보와 보호자의 Expo Push Token 조회
            stmt_person = select(TrackedPerson).where(
                TrackedPerson.id == int(result.person_id)
            )

            person = (await db.execute(stmt_person)).scalar_one_or_none()
            if not person:
                return {"status": "failed", "message": "no person available"}

            guardian_id = person.guardian_id
            stmt_guard = select(Guardian).where(
                Guardian.id == guardian_id
            )

            guardian = (await db.execute(stmt_guard)).scalar_one_or_none()

            if not guardian:
                return {"status": "failed", "message": "no guardian available"}
            
            elder_name = person.name
            expo_token = guardian.expo_token
            
            print(f"🚨 [긴급] {alert_type} 감지됨! 보호자 푸시 발송 대기열 추가.")
            
            # API 지연을 막기 위해 백그라운드 태스크로 푸시 전송 위임
            background_tasks.add_task(send_emergency_push, expo_token, elder_name, alert_type)

        return {"status": "success", "message": "Sensor data received successfully"}
        
    except Exception as e:
        print(f"데이터 수신 중 에러 발생: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")