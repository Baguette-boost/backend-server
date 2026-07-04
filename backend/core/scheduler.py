import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.buffer import patient_gps_buffer as gps_buffer # dict of deques
from backend.services.ai_client import ai_client
from backend.schemas.ai import AIPredictRequest, GPSPoint
from backend.services.alert_service import save_wandering_alert
from backend.database import get_independent_session
from backend.models.person import TrackedPerson
from backend.models.alert import AlertLog
from backend.services.notification_service import NotificationService, get_guardian_token_and_name, send_emergency_push
import asyncio

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

TIMEOUT_MINUTES = 5 # 오프라인 판정 임계치 (5분)

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

async def monitor_device_heartbeats():
    """
    마지막 GPS 수신 시간이 임계치를 초과한 기기를 탐색하여 
    상태를 offline으로 변경하고 보호자에게 실시간 오프라인 알림 발행
    """
    async with get_independent_session() as db:  # 스케줄러 내부 독립 세션 생성
        current_time = datetime.utcnow()
        threshold_time = current_time - timedelta(minutes=TIMEOUT_MINUTES)
        
        logger.info("## [HeartBeat] 오프라인 스캔 루프 시작 ##")

        # 마지막 수신 시간이 임계치 이전이고, 현재 상태가 active인 환자 조회
        stmt = select(TrackedPerson).where(
            TrackedPerson.updated_at < threshold_time,
            TrackedPerson.is_active == True
        )
        offline_persons = (await db.execute(stmt)).scalars().all()
        
        logger.info(f"[HeartBeat] 탐색된 타임아웃 대상자 수: {len(offline_persons)}명")

        if not offline_persons:
            return
            
        for person in offline_persons:
            # 상태 변경
            person.is_active = False

            alert_msg = f"[경고] {person.name} 어르신의 단말기 통신이 5분 이상 두절되었습니다. 전원 상태를 확인하세요."
            
            # 오프라인 로그 생성
            alert_log = AlertLog(
                person_id=person.id,
                alert_type="offline",
                message=alert_msg,
                created_at=current_time
            )
            db.add(alert_log)         

            # 보호자의 토큰 및 웹소켓 연동용 정보 조회
            token_info = await get_guardian_token_and_name(person.id)
            if token_info:
                expo_token, guardian_id = token_info["expo_token"], token_info["guardian_id"]
                
                # (1) 웹소켓 대시보드 실시간 브로드캐스팅 가동
                payload_data = {"id": alert_log.id, "type": "offline", "message": alert_msg}
                asyncio.create_task(
                    NotificationService.broadcast_event(
                        db=db, guardian_id=guardian_id, event_type="alert",
                        person_id=str(person.id), payload_data=payload_data, payload=None
                    )
                )
                # (2) 분리된 웹훅 구조의 send_emergency_push 백그라운드 호출
                asyncio.create_task(
                    send_emergency_push(
                        expo_token, person.name, "offline"
                    )
                )
        
        await db.commit()

def start_scheduler():
    logger.info("## [스케줄러] start_scheduler() 함수가 호출되었습니다! ##")

    # 1분 주기로 배회 감지 잡 실행
    #scheduler.add_job(check_wandering_job, 'interval', minutes=1, id='wandering_check')
    # 1분 주기로 하트비트 모니터링 루프 가동
    scheduler.add_job(monitor_device_heartbeats, 'interval', minutes=1)
    scheduler.start()

def stop_scheduler():
    scheduler.shutdown()
