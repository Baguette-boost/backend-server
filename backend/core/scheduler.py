import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from core.buffer import gps_buffer # dict of deques
from services.ai_client import ai_client
from schemas.ai import AIPredictRequest, GPSPoint
from services.alert_service import save_wandering_alert

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

async def check_wandering_job():
    logger.info("배회 감지 스케줄러 실행 중...")
    
    for person_id, deque_buffer in gps_buffer.items():
        # 순회 중 버퍼 변경 방지를 위해 리스트로 복사
        current_gps_data = list(deque_buffer)
        
        if not current_gps_data:
            continue
            
        # Pydantic 모델에 맞게 변환 (만약 buffer 내 객체가 이미 dict 형태라면 바로 매핑)
        gps_points = [
            GPSPoint(
                timestamp=point['timestamp'],
                latitude=point['latitude'],
                longitude=point['longitude']
            ) for point in current_gps_data
        ]

        request_payload = AIPredictRequest(
            personId=person_id,
            timestamp=datetime.utcnow(),
            gpsData=gps_points
            # 배회 스케줄러이므로 imuData는 None
        )

        # AI 서버로 비동기 요청 (병목 방지)
        response = await ai_client.predict(request_payload)
        
        if response and response.wandering_detection:
            if response.wandering_detection.is_triggered:
                await save_wandering_alert(
                    person_id=person_id,
                    probability=response.wandering_detection.probability
                )

def start_scheduler():
    # 1분 주기로 배회 감지 잡 실행
    scheduler.add_job(check_wandering_job, 'interval', minutes=1, id='wandering_check')
    scheduler.start()

def stop_scheduler():
    scheduler.shutdown()