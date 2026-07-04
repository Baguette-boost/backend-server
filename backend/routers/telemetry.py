from fastapi import APIRouter, BackgroundTasks, Depends, status, HTTPException
from aiomysql import Pool # 기존에 세팅한 커넥션 풀
import logging
from datetime import datetime

from backend.schemas.telemetry import GPSRequest, FallSuspectRequest
from backend.core.buffer import add_gps_to_buffer, get_patient_gps_history
from backend.core.security import verify_device_token # 디바이스 인증 의존성 주입

from backend.services.ai_client import ai_client

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from backend.database import get_db, get_independent_session  # DB 의존성 주입
from backend.models.telemetry import GpsLog
from backend.schemas.ai import AIPredictRequest
from backend.services.notification_service import NotificationService, send_emergency_push, get_guardian_token_and_name

import math
from backend.models.alert import AlertLog
from backend.models.person import TrackedPerson

import os
from dotenv import load_dotenv

load_dotenv() # .env 파일 로드

logger = logging.getLogger(__name__)
telemetry_router = APIRouter(
    prefix="/telemetry",
    tags=["Telemetry"],
    dependencies=[Depends(verify_device_token)]
)

def calculate_haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    두 위경도 좌표 간의 거리를 미터(m) 단위로 계산 (하버사인 공식)
    """
    R = 6371000.0  # 지구 반지름 (미터)
    
    r_lat1 = math.radians(lat1)
    r_lat2 = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_lat / 2) ** 2 +
         math.cos(r_lat1) * math.cos(r_lat2) * math.sin(delta_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c


# Background Task용 비동기 함수들

# DB에 gps 데이터 INSERT
async def async_insert_gps_log(person_id: int, gps_data: dict):
    """(Background) DB의 gps_logs 테이블에 비동기 INSERT 수행"""
    raw_timestamp = gps_data.get("timestamp")

    # datetime 객체로 복구
    if isinstance(raw_timestamp, str):
        raw_timestamp = datetime.fromisoformat(raw_timestamp.replace('Z', ''))
        
    clean_timestamp = raw_timestamp.replace(tzinfo=None) if raw_timestamp else datetime.now()
    
    async with get_independent_session() as db:
        new_gps = GpsLog(
            person_id=person_id,
            latitude=gps_data["latitude"],
            longitude=gps_data["longitude"],
            battery=gps_data["battery"],
            is_fall_detected=gps_data.get("is_fall_detected", False),
            is_wandering_detected=gps_data.get("is_wandering_detected", False),
            created_at=clean_timestamp
        )

        logger.info(f"[DB INSERT] personId: {person_id}, gps: {gps_data}")

        db.add(new_gps)
        await db.commit()

# API Endpoints

@telemetry_router.post("/gps", status_code=status.HTTP_201_CREATED) # POST /telemetry/gps
async def receive_gps(
    request: GPSRequest, 
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """ [디바이스 -> 서버] 10초 주기로 전송되는 GPS 데이터 수신 및 실시간 이탈/오프라인 조건 판정 """
    # 직렬화를 위해 Pydantic 모델을 dict로 변환 (timestamp도 문자열로)
    gps_dict = request.gps.dict()
    gps_dict['timestamp'] = gps_dict['timestamp'].isoformat()

    stmt = select(TrackedPerson).where(
        TrackedPerson.id == request.personId
    )
    
    person = (await db.execute(stmt)).scalar_one_or_none()

    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="등록되지 않은 환자입니다")
    
    current_time = datetime.utcnow()

    # 하버사인 공식을 이용한 거리 계산 및 이탈 판정
    distance = calculate_haversine(
        gps_dict['latitude'], gps_dict['longitude'],
        person.base_lat, person.base_lng
    )

    # 안전구역 반경 넘었는지 확인
    is_in_safe_zone = distance <= person.safe_radius

    # 환자의 활성 상태 및 시간 업데이트    
    update_stmt = (
        update(TrackedPerson)
        .where(TrackedPerson.id == person.id)
        .values(updated_at=current_time, is_active=True)
    )
    await db.execute(update_stmt)
    await db.commit()

    # 이탈 판정 시 긴급 알림 로깅 및 실시간 트리거
    if not is_in_safe_zone:
        alert_msg = f"[긴급] {person.name} 어르신이 안전 구역을 {int(distance - person.safe_radius)}m 벗어났습니다",

        # DB에 알림 로그 적재
        alert_log = AlertLog(
            person_id=person.id,
            alert_type="zone_exit",
            message=alert_msg,
            created_at=current_time
        )
        db.add(alert_log)
        await db.commit()


        # 보호자의 토큰 및 웹소켓 연동용 정보 조회
        token_info = await get_guardian_token_and_name(person.id)
        if token_info:
            expo_token, guardian_id = token_info["expo_token"], token_info["guardian_id"]
            
            # (1) 웹소켓 대시보드 실시간 브로드캐스팅 가동
            payload_data = {"id": alert_log.id, "type": "zone_exit", "message": alert_msg}
            background_tasks.add_task(
                NotificationService.broadcast_event,
                db=db, guardian_id=guardian_id, event_type="alert",
                person_id=str(person.id), payload_data=payload_data, payload=None
            )
            # (2) 분리된 웹훅 구조의 send_emergency_push 백그라운드 호출
            background_tasks.add_task(send_emergency_push, expo_token, person.name, "zone_exit")

        return {"status": "false", "message": "zone_exit"}
    
    ## 정상 범위 내 존재 시 데이터 적재 및 ai 분석

    # 인메모리 전역 deque 버퍼에 즉시 적재 (가장 오래된 데이터는 자동 pop)
    add_gps_to_buffer(request.personId, gps_dict)
    
    # 2. DB Insert는 백그라운드로 넘겨 API 응답 속도 최적화
    background_tasks.add_task(async_insert_gps_log, request.personId, gps_dict)

    # AI 분석용 payload 구성
    clean_timestamp = request.gps.timestamp.isoformat()
    gps_list = get_patient_gps_history(request.personId)
    
    payload = AIPredictRequest(
        personId=request.personId,
        timestamp=clean_timestamp,
        imuData=None,
        gpsData=gps_list
    )
    
    # AI & 알림 로직을 백그라운드로 위임 -> API 응답 속도 최적화
    background_tasks.add_task(NotificationService.broadcast_event, db=None, guardian_id=None, event_type=None, person_id=request.personId, payload_data=None, payload=payload)
    
    return {"status": "success", "message": "Data received and AI analysis started"}


@telemetry_router.post("/fall-suspect", status_code=status.HTTP_202_ACCEPTED) # POST /telemetry/fall-suspect
async def receive_fall_suspect(
    request: FallSuspectRequest, 
    background_tasks: BackgroundTasks
):
    """
    [디바이스 -> 서버] 디바이스 자체 판단 하에 낙상 의심 시점의 IMU 데이터 수신
    (Background) AI 컨테이너로 IMU 및 누적 GPS 데이터를 묶어서 전송
    """
    timestamp_str = request.timestamp.isoformat() + 'Z'
    imu_dict = request.imuData.dict()
    gps_list = get_patient_gps_history(request.personId)

    # AI 서버 규약에 맞게 페이로드 구성
    payload = AIPredictRequest(
        personId=request.personId,
        timestamp=timestamp_str,
        imuData=imu_dict,
        gpsData=gps_list
    )
    
    # 1. 디바이스에는 즉각적으로 202 Accepted 응답을 반환하기 위해 AI 통신을 백그라운드로 위임
    background_tasks.add_task(
        ai_client.predict,
        payload
    )
    
    # 2. 응답 리턴 (FastAPI가 자동으로 202 상태 코드와 함께 아래 JSON을 반환)
    return {"status": "accepted", "message": "Fall suspect data is being processed"}
