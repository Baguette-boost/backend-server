from fastapi import APIRouter, BackgroundTasks, Depends, status, HTTPException
from aiomysql import Pool # 기존에 세팅한 커넥션 풀
import logging

from backend.schemas.telemetry import GPSRequest, FallSuspectRequest
from backend.core.buffer import add_gps_to_buffer, get_patient_gps_history
from backend.main import verify_device_token # 디바이스 인증 의존성 주입

from backend.services.ai_client import predict

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.database import get_db, get_independent_session  # DB 의존성 주입
from backend.models.telemetry import GpsLog

import os
from dotenv import load_dotenv

load_dotenv() # .env 파일 로드

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/telemetry",
    tags=["Telemetry"],
    dependencies=[Depends(verify_device_token)]
)


# Background Task 용 비동기 함수

async def async_insert_gps_log(person_id: int, gps_data: dict):
    """(Background) DB의 gps_logs 테이블에 비동기 INSERT 수행"""
    async with get_independent_session() as db:
        new_gps = GpsLog(
            "person_id"=person_id,
            "latitude"=gps_data["latitude"],
            "longitude"=gps_data["longitude"],
            "battery"=gps_data["battery"],
            "is_fall_detected"=gps_data.get("is_fall_detected", False),
            "is_wandering_detected"=gps_data.get("is_wandering_detected", False),
            "created_at"=gps_data.get("timestamp")
        )

        logger.info(f"[DB INSERT] personId: {person_id}, gps: {gps_data}")

        db.add(new_gps)

# API Endpoints

@router.post("/gps", status_code=status.HTTP_201_CREATED) # POST /telemetry/gps
async def receive_gps(
    request: GPSRequest, 
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    [디바이스 -> 서버] 10초 주기로 전송되는 GPS 데이터 수신
    """
    # 직렬화를 위해 Pydantic 모델을 dict로 변환 (timestamp도 문자열로)
    gps_dict = request.gps.dict()
    gps_dict['timestamp'] = gps_dict['timestamp'].isoformat() + 'Z'
    
    # 1. 인메모리 전역 deque 버퍼에 즉시 적재 (가장 오래된 데이터는 자동 pop)
    add_gps_to_buffer(request.personId, gps_dict)
    
    # 2. DB Insert는 백그라운드로 넘겨 API 응답 속도 최적화
    background_tasks.add_task(async_insert_gps_log, request.personId, gps_dict)
    
    return {"status": "success", "message": "GPS buffered"}


@router.post("/fall-suspect", status_code=status.HTTP_202_ACCEPTED) # POST /telemetry/fall-suspect
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
        "personId"=request.personId,
        "timestamp"=timestamp_str,
        "imuData"=imu_dict,
        "gpsData"=gps_list
    )
    
    # 1. 디바이스에는 즉각적으로 202 Accepted 응답을 반환하기 위해 AI 통신을 백그라운드로 위임
    background_tasks.add_task(
        predict,
        payload
    )
    
    # 2. 응답 리턴 (FastAPI가 자동으로 202 상태 코드와 함께 아래 JSON을 반환)
    return {"status": "accepted", "message": "Fall suspect data is being processed"}
