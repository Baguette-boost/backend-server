import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_independent_session
from backend.models.person import TrackedPerson
from backend.models.alert import AlertLog
from backend.models.telemetry import GpsLog
from backend.services.notification_service import NotificationService, get_guardian_token_and_name, send_emergency_push
from backend.utils.time import utcnow
from backend.utils.geo import clean_track, radius_of_gyration_m
import asyncio

# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

TIMEOUT_MINUTES = 5 # 오프라인 판정 임계치 (5분)

# 낙상 후 부동(이동 없음) 판정 파라미터 (실외 낙상 대상, GPS 궤적 분산 기반)
FALL_EPISODE_MINUTES = 10   # 마지막 낙상 판정 후 이 시간 동안 추가 낙상이 없으면 종료 판정(기준: https://www.movementdisordersclinic.com/how-to-safely-get-up-after-a-fall-a-guide-for-seniors/)
FALL_MIN_VALID_POINTS = 10  # 회전반경 계산에 필요한 최소 유효 GPS 점 수 (미만이면 판단 불가) — Rg 안정성 위해 상향
FALL_RG_THRESHOLD_M = 20.0  # 회전반경이 이 값 이하이면 '아직 부동' → is_fall 유지

# 배회 감지는 GPS 수신 경로(receive_gps → broadcast_event)에서 매 핑마다 처리하므로
# 별도 스케줄러 잡(check_wandering_job)은 제거되었다.

async def monitor_device_heartbeats():
    """
    마지막 GPS 수신 시간이 임계치를 초과한 기기를 탐색하여 
    상태를 offline으로 변경하고 보호자에게 실시간 오프라인 알림 발행
    """
    async with get_independent_session() as db:  # 스케줄러 내부 독립 세션 생성
        current_time = utcnow()
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

            alert_msg = f"[Warning] {person.name}'s device has been offline for more than {TIMEOUT_MINUTES} minutes. Please check the power."
            
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

async def monitor_fall_episodes():
    """
    낙상 후 부동(이동 없음) 판정 루프.
    마지막 낙상 판정 후 FALL_EPISODE_MINUTES 동안 추가 낙상이 없으면,
    에피소드 윈도우 내 GPS 궤적의 회전반경으로 is_fall(여전히 쓰러짐)을 유지/해제한다.
    실외 낙상 대상 — 유효 GPS 가 부족하면 판단 불가로 보고 is_fall 을 해제한다.
    (최초 낙상 알림은 감지 시점에 이미 발송되었으므로, 여기서 해제는 '상태' 종료를 의미)
    """
    async with get_independent_session() as db:
        now = utcnow()
        deadline = now - timedelta(minutes=FALL_EPISODE_MINUTES)

        stmt = select(TrackedPerson).where(
            TrackedPerson.fall_pending == True,
            TrackedPerson.fall_last_at <= deadline,
        )
        persons = (await db.execute(stmt)).scalars().all()
        if not persons:
            return

        for person in persons:
            # 에피소드 윈도우 [fall_started_at, now] 내 GPS 조회 (시간 오름차순)
            gstmt = (
                select(GpsLog.latitude, GpsLog.longitude, GpsLog.created_at)
                .where(
                    GpsLog.person_id == person.id,
                    GpsLog.created_at >= person.fall_started_at,
                    GpsLog.created_at <= now,
                )
                .order_by(GpsLog.created_at)
            )
            rows = (await db.execute(gstmt)).all()

            # (0,0)·None 제거 + 속도 이상치 제거
            points = clean_track([(r[0], r[1], r[2]) for r in rows])

            if len(points) < FALL_MIN_VALID_POINTS:
                # 유효 GPS 부족 → 실외 부동 판단 불가 → 비대상으로 보고 해제
                person.is_fall = False
                logger.info(f"[FALL-EPISODE] personId={person.id} 유효 GPS 부족({len(points)}) → is_fall 해제")
            else:
                rg = radius_of_gyration_m(points)
                if rg <= FALL_RG_THRESHOLD_M:
                    person.is_fall = True  # 거의 이동 없음 → 여전히 쓰러짐 (유지)
                    logger.warning(f"[FALL-EPISODE] personId={person.id} 부동 지속 (Rg={rg:.1f}m) → is_fall 유지")
                else:
                    person.is_fall = False  # 이동 감지 → 회복
                    logger.info(f"[FALL-EPISODE] personId={person.id} 이동 감지 (Rg={rg:.1f}m) → is_fall 해제")

            # 에피소드 종료
            person.fall_pending = False

        await db.commit()

def start_scheduler():
    logger.info("## [스케줄러] start_scheduler() 함수가 호출되었습니다! ##")

    # 1분 주기로 하트비트 모니터링 루프 가동
    scheduler.add_job(monitor_device_heartbeats, 'interval', minutes=1)
    # 1분 주기로 낙상 부동 판정 루프 가동
    scheduler.add_job(monitor_fall_episodes, 'interval', minutes=1)
    scheduler.start()

def stop_scheduler():
    scheduler.shutdown()
