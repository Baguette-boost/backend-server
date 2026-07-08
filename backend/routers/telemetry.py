from fastapi import APIRouter, BackgroundTasks, Depends, status, HTTPException
from aiomysql import Pool # 기존에 세팅한 커넥션 풀
import logging
from datetime import datetime

from backend.utils.time import to_naive_utc, utcnow

from backend.schemas.telemetry import GPSRequest, FallSuspectRequest
from backend.core.buffer import add_gps_to_buffer, get_patient_gps_history
from backend.core.security import verify_device_token # 디바이스 인증 의존성 주입

from backend.services.ai_client import ai_client

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from backend.database import get_db, get_independent_session  # DB 의존성 주입
from backend.models.telemetry import GpsLog, ImuLog
from backend.schemas.ai import AIPredictRequest
from backend.services.notification_service import NotificationService

import math
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

LAT_METER_PER_DEGREE = 111000.0
LON_METER_PER_DEGREE = 88800.0

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

    # 문자열/aware/naive 무엇이 오든 방어적으로 UTC(naive)로 변환해 저장한다.
    clean_timestamp = to_naive_utc(raw_timestamp) if raw_timestamp else utcnow()
    
    async with get_independent_session() as db:
        new_gps = GpsLog(
            person_id=person_id,
            latitude=gps_data["latitude"],
            longitude=gps_data["longitude"],
            is_fall_detected=gps_data.get("is_fall_detected", False),
            is_wandering_detected=gps_data.get("is_wandering_detected", False),
            created_at=clean_timestamp
        )

        logger.info(f"[DB INSERT] personId: {person_id}, gps: {gps_data}")

        db.add(new_gps)
        await db.commit()

# 낙상 의심 이벤트 처리: 멱등 저장 + AI 낙상 판정 + 판정 결과 저장 + (낙상 시) 알림 1회
async def process_fall_suspect(person_id: int, recorded_at, imu_dict: dict, sample_count: int, payload: AIPredictRequest):
    """(Background) imu_logs 저장(재전송 멱등) → AI 낙상 판정 → predicted_label 기록 → 낙상 시 알림"""
    guardian_id = None
    name = ""
    predicted = None
    async with get_independent_session() as db:
        # 재전송 멱등 처리: 동일 (person_id, recorded_at) 이 이미 있으면 저장·판정·알림 모두 skip
        dup = await db.execute(
            select(ImuLog.id).where(
                ImuLog.person_id == person_id,
                ImuLog.recorded_at == recorded_at,
            )
        )
        if dup.scalar_one_or_none() is not None:
            logger.info(f"[FALL] 재전송 감지, skip personId={person_id}, recorded_at={recorded_at}")
            return

        # AI 낙상 판정 (미연결 시 mock 이 비트리거 반환, 초기화 실패 등으로 None 이면 미판정)
        ai_result = await ai_client.predict(payload)
        predicted = (
            ai_result.fall_detection.is_triggered
            if (ai_result and ai_result.fall_detection is not None) else None
        )

        # IMU 원본 + AI 예측 저장 (판정 결과와 무관하게 재학습용 원본은 항상 보존)
        db.add(ImuLog(
            person_id=person_id,
            recorded_at=recorded_at,    # 낙상 의심 감지 시각 (naive UTC)
            imu_data=imu_dict,          # {ax, ay, az, wx, wy, wz}
            sample_count=sample_count,
            predicted_label=predicted,  # AI 낙상 예측 (미판정 시 None)
        ))

        # 알림용 보호자 정보 확보
        person = (await db.execute(
            select(TrackedPerson).where(TrackedPerson.id == person_id)
        )).scalar_one_or_none()
        if person:
            guardian_id = person.guardian_id
            name = person.name

        logger.info(f"[FALL] personId={person_id}, predicted={predicted}, samples={sample_count}")
        await db.commit()

    # 낙상으로 판정된 경우에만 알림 1회 (WebSocket + Expo Push)
    if predicted and guardian_id:
        await NotificationService._notify(
            person_id, guardian_id, "fall_detected", f"{name}님의 낙상이 감지되었습니다"
        )

# API Endpoints

@telemetry_router.post("/gps", status_code=status.HTTP_201_CREATED) # POST /telemetry/gps
async def receive_gps(
    request: GPSRequest, 
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """ [디바이스 -> 서버] 주기적으로 전송되는 GPS 데이터 수신 → 버퍼 적재 및 AI 배회 분석 위임 """
    # 직렬화를 위해 Pydantic 모델을 dict로 변환 (timestamp도 문자열로)
    gps_dict = request.gps.dict()
    gps_dict['timestamp'] = gps_dict['timestamp'].isoformat()

    stmt = select(TrackedPerson).where(
        TrackedPerson.id == request.personId
    )
    
    person = (await db.execute(stmt)).scalar_one_or_none()

    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="등록되지 않은 환자입니다")
    
    current_time = utcnow()

    # 환자의 활성 상태 및 시간 업데이트    
    update_stmt = (
        update(TrackedPerson)
        .where(TrackedPerson.id == person.id)
        .values(updated_at=current_time, is_active=True)
    )
    await db.execute(update_stmt)
    await db.commit()

    # 수신 데이터 버퍼 적재 및 AI(배회) 분석 위임

    # 인메모리 전역 deque 버퍼에 즉시 적재 (가장 오래된 데이터는 자동 pop)
    add_gps_to_buffer(request.personId, gps_dict)
    
    # 2. DB Insert는 백그라운드로 넘겨 API 응답 속도 최적화
    background_tasks.add_task(async_insert_gps_log, request.personId, gps_dict)

    # AI 분석용 payload 구성 (datetime 객체를 그대로 전달)
    gps_list = get_patient_gps_history(request.personId)

    payload = AIPredictRequest(
        personId=request.personId,
        timestamp=request.gps.timestamp,
        imuData=None,
        gpsData=gps_list
    )
    
    # AI & 알림 로직을 백그라운드로 위임 -> API 응답 속도 최적화
    background_tasks.add_task(NotificationService.broadcast_event, db=None, guardian_id=None, event_type=None, person_id=request.personId, payload_data=None, payload=payload)
    
    return {"status": "success", "message": "Data received and AI analysis started"}


@telemetry_router.post("/fall-suspect", status_code=status.HTTP_202_ACCEPTED) # POST /telemetry/fall-suspect
async def receive_fall_suspect(
    request: FallSuspectRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    [디바이스 -> 서버] 디바이스 자체 판단 하에 낙상 의심 시점의 IMU 데이터 수신
    (Background) AI 컨테이너로 IMU 및 누적 GPS 데이터를 묶어서 전송
    """
    # 등록된 환자인지 검증 (미등록 personId의 낙상 데이터가 AI로 흘러가는 것을 차단)
    person = (
        await db.execute(
            select(TrackedPerson).where(TrackedPerson.id == request.personId)
        )
    ).scalar_one_or_none()

    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="등록되지 않은 환자입니다")

    try:
        imu_dict = request.imuData.dict()
        gps_list = get_patient_gps_history(request.personId)

        # AI 서버 규약에 맞게 페이로드 구성
        # datetime 객체를 그대로 전달한다. (문자열로 isoformat()+'Z' 하면 tz-aware 입력에서
        #  '+00:00Z' 이중 표기가 되어 재파싱이 실패한다. 직렬화는 model_dump(mode='json')가 처리.)
        payload = AIPredictRequest(
            personId=request.personId,
            timestamp=request.timestamp,
            imuData=imu_dict,
            gpsData=gps_list
        )
    except Exception as e:
        # 버퍼에 손상된 GPS 데이터가 섞이는 등 payload 구성 단계에서 발생하는 예외를
        # 트레이스백 노출 없이 로깅 후 500으로 반환한다.
        logger.error(f"[fall-suspect] payload 구성 실패 personId={request.personId}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="낙상 의심 데이터 처리 중 오류가 발생했습니다"
        )

    # 디바이스에는 즉시 202 Accepted 를 반환하고, 저장·AI 판정·알림은 단일 백그라운드 태스크로 위임
    # (멱등 저장 → AI 낙상 판정 → predicted_label 기록 → 낙상 시 푸시 1회)
    background_tasks.add_task(
        process_fall_suspect,
        request.personId,
        request.timestamp,
        imu_dict,
        len(request.imuData.ax),
        payload,
    )

    # 응답 리턴 (FastAPI가 자동으로 202 상태 코드와 함께 아래 JSON을 반환)
    return {"status": "accepted", "message": "Fall suspect data is being processed"}
