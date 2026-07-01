import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, status, Request
from fastapi.responses import JSONResponse
from aiomysql import Pool # 기존에 세팅한 커넥션 풀
import logging

from schemas.telemetry import GPSRequest, FallSuspectRequest, AIPredictRequest
from core.buffer import add_gps_to_buffer, get_patient_gps_history
from backend.database import get_db  # DB 의존성 주입
from backend.main import verify_device_token # 디바이스 인증 의존성 주입

import os
from dotenv import load_dotenv

load_dotenv() # .env 파일 로드

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/telemetry",
    tags=["Telemetry"],
    dependencies=[Depends(verify_device_token)]
)

# AI 서비스 컨테이너 URL(환경변수 이용)
AI_SERVICE_URL = os.getenv('AI_SERVICE_URL')


# Background Task 용 비동기 함수들

async def async_insert_gps_log(person_id: int, gps_data: dict, pool: Pool = None):
    """(Background) DB의 gps_logs 테이블에 비동기 INSERT 수행"""
    # 실제: session.execute로 쿼리 실행, session.commit으로 반영
    query = """
        INSERT INTO gps_logs (person_id, timestamp, latitude, longitude)
        VALUES (%s, %s, %s, %s)
    """
    # 임시 Mocking 로직
    logger.info(f"[DB INSERT] personId: {person_id}, gps: {gps_data}")
    # await session.execute(text(query), {"person_id": person_id, "gps": gps_data})
    # await session.commit()

async def async_bypass_to_ai(person_id: int, timestamp: str, imu_data: dict):
    """(Background) AI 컨테이너로 IMU 및 누적 GPS 데이터를 묶어서 전송"""
    # 1. 전역 버퍼에서 환자의 30분치 GPS 데이터 추출
    gps_history = get_patient_gps_history(person_id)
    
    # 2. AI 서버 규약에 맞게 페이로드 구성
    payload = {
        "personId": person_id,
        "timestamp": timestamp,
        "imuData": imu_data,
        "gpsData": gps_history
    }
    
    # 3. httpx.AsyncClient를 이용한 논블로킹 내부 통신
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            logger.info(f"[AI BYPASS] Sending telemetry to AI service for personId: {person_id}")
            response = await client.post(AI_SERVICE_URL, json=payload)
            response.raise_for_status()
            
            ai_result = response.json()
            logger.info(f"[AI RESULT] {ai_result}")
            
            # TODO: AI 결과에 따라 (fall_probability 등) alert_logs 테이블에 인서트하거나 푸시 알림 발송 로직 추가
            
        except httpx.RequestError as exc:
            logger.error(f"[AI BYPASS ERROR] An error occurred while requesting {exc.request.url!r}.")
        except httpx.HTTPStatusError as exc:
            logger.error(f"[AI BYPASS ERROR] Error response {exc.response.status_code} while requesting {exc.request.url!r}.")

# API Endpoints

@router.post("/gps", status_code=status.HTTP_201_CREATED) # POST /telemetry/gps
async def receive_gps(
    request: GPSRequest, 
    background_tasks: BackgroundTasks,
    pool: Pool = Depends(get_db)
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
    background_tasks.add_task(async_insert_gps_log, request.personId, gps_dict, pool)
    
    return {"status": "success", "message": "GPS buffered"}


@router.post("/fall-suspect", status_code=status.HTTP_202_ACCEPTED) # POST /telemetry/fall-suspect
async def receive_fall_suspect(
    request: FallSuspectRequest, 
    background_tasks: BackgroundTasks
):
    """
    [디바이스 -> 서버] 디바이스 자체 판단 하에 낙상 의심 시점의 IMU 데이터 수신
    """
    timestamp_str = request.timestamp.isoformat() + 'Z'
    imu_dict = request.imuData.dict()
    
    # 1. 디바이스에는 즉각적으로 202 Accepted 응답을 반환하기 위해 AI 통신을 백그라운드로 위임
    background_tasks.add_task(
        async_bypass_to_ai, 
        request.personId, 
        timestamp_str, 
        imu_dict
    )
    
    # 2. 응답 리턴 (FastAPI가 자동으로 202 상태 코드와 함께 아래 JSON을 반환)
    return {"status": "accepted", "message": "Fall suspect data is being processed"}