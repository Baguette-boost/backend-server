from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends
from pydantic import BaseModel, Field, ConfigDict, AliasChoices
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
# 실시간 추론 스크립트(realtime_sensor_lstm.py)는 timestamp/device 필드명으로 전송하므로
# alias 로 수용하고, 나머지 추론 부가 필드(gps_valid, anomaly_score 등)는 무시한다.
class SensorResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    server_time: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("server_time", "timestamp"),
    )
    # AI 는 기기 식별자(device, 예: "esp32-1")를 보낸다. device_id 로 대상자를 조회한다.
    device: Optional[str] = None
    # person_id 를 직접 보내는 호출도 계속 지원한다.
    person_id: Optional[str] = None
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
        # 0. 대상자(어르신) 조회: device_id 우선, 없으면 person_id 로 조회
        if result.device is not None:
            stmt_person = select(TrackedPerson).where(
                TrackedPerson.device_id == result.device
            )
        elif result.person_id is not None:
            stmt_person = select(TrackedPerson).where(
                TrackedPerson.id == int(result.person_id)
            )
        else:
            raise HTTPException(
                status_code=422, detail="device 또는 person_id 중 하나는 필요합니다."
            )

        person = (await db.execute(stmt_person)).scalar_one_or_none()
        if not person:
            raise HTTPException(
                status_code=404, detail="해당 기기에 매칭되는 대상자를 찾을 수 없습니다."
            )

        person_pk = person.id

        # 1. DB 저장 로직 (최신 데이터 갱신 및 히스토리 저장)
        # server_time(문자열)을 파싱해 created_at 저장/매칭에 사용한다.
        # created_at 은 초 단위 TIMESTAMP 이므로 마이크로초를 잘라 저장·비교 정밀도를 맞춘다.
        event_time = None
        if result.server_time:
            try:
                event_time = datetime.fromisoformat(result.server_time).replace(microsecond=0)
            except ValueError:
                event_time = None

        gps_id = None
        if event_time is not None:
            stmt = select(GpsLog.id).where(
                GpsLog.person_id == person_pk,
                GpsLog.created_at == event_time
            )
            gps_id = (await db.execute(stmt)).scalar_one_or_none()

        if gps_id:
            # 같은 시각의 데이터가 있다면 감지 플래그만 갱신
            stmt = update(GpsLog).where(
                GpsLog.id == gps_id
            ).values(
                is_fall_detected=result.fall_detected,
                is_wandering_detected=result.wandering_detected
            )
            await db.execute(stmt)
            await db.commit()
        else:
            # 없으면 새 로그 추가 (event_time 이 있으면 created_at 으로 저장, 없으면 DB now())
            new_gps = GpsLog(
                person_id=person_pk,
                latitude=result.lat,
                longitude=result.lng,
                battery=-1,
                is_fall_detected=result.fall_detected,
                is_wandering_detected=result.wandering_detected
            )
            if event_time is not None:
                new_gps.created_at = event_time
            db.add(new_gps)
            await db.commit()
            await db.refresh(new_gps)

        # 2. 위험 상황 판별 및 비동기 푸시 알림 트리거
        if result.fall_detected or result.wandering_detected:
            alert_type = "fall_detected" if result.fall_detected else "zone_exit"

            # 위에서 조회한 대상자의 보호자 Expo Push Token 조회
            stmt_guard = select(Guardian).where(
                Guardian.id == person.guardian_id
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

    except HTTPException:
        # 404/422 등 의도한 응답은 그대로 전달 (아래 500 처리에 삼켜지지 않도록)
        raise
    except Exception as e:
        print(f"데이터 수신 중 에러 발생: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")