import logging
import math
import httpx
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_independent_session
from backend.models.person import TrackedPerson
from backend.models.alert import AlertLog
from backend.models.telemetry import GpsLog, ImuLog
from backend.services.notification_service import NotificationService, get_guardian_token_and_name, send_emergency_push
from backend.utils.time import utcnow
from backend.utils.geo import clean_track, radius_of_gyration_m
from backend.config import settings
import asyncio

# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

TIMEOUT_MINUTES = 5 # 오프라인 판정 임계치 (5분)

# 낙상 후 부동(이동 없음) 판정 파라미터 (실외 낙상 대상, GPS 궤적 분산 기반)
FALL_EPISODE_MINUTES = 10   # 마지막 낙상 판정 후 이 시간 동안 추가 낙상이 없으면 종료 판정(기준: https://www.movementdisordersclinic.com/how-to-safely-get-up-after-a-fall-a-guide-for-seniors/)
FALL_MIN_VALID_POINTS = 10  # 회전반경 계산에 필요한 최소 유효 GPS 점 수 (미만이면 판단 불가) — Rg 안정성 위해 상향
FALL_RG_THRESHOLD_M = 20.0  # 회전반경이 이 값 이하이면 '아직 부동' → is_fall 유지

# IMU 정지판정 파라미터 (야외: 낙상은 IMU 가 직접 신호 + GPS 드롭 대비. 최근 IMU 로 활동/정지 구분)
FALL_IMU_LOOKBACK_MINUTES = 3   # 최근 몇 분의 imu_logs 로 현재 활동 상태를 볼지
FALL_IMU_MAX_LOGS = 30          # 조회할 최근 imu_logs 최대 개수(성능 상한)
FALL_IMU_MIN_SAMPLES = 50       # 판정에 필요한 최소 IMU 표본 수(미만이면 unknown)
FALL_ACTIVE_ACCEL_STD = 0.08    # accel_norm 표준편차(g) 이 이상이면 '활동' (임시값 — 실데이터 보정 필요)
FALL_ACTIVE_GYRO_MEAN = 20.0    # gyro_norm 평균(deg/s) 이 이상이면 '활동' (임시값 — 실데이터 보정 필요)

# 배회 RF 개인 모델 enroll 파라미터 (하루 1회 스케줄러가 미등록 환자를 학습)
WANDER_ENROLL_DAYS = 14         # 학습에 사용할 최근 GPS 기간(일)
WANDER_ENROLL_MIN_DAYS = 7      # 게이트: 유효 데이터가 있는 최소 일수
WANDER_ENROLL_MIN_FIXES = 1000  # 게이트: 20초 리샘플 후 최소 fix 수
WANDER_RESAMPLE_SECONDS = 20    # RF 전제(20초 간격)에 맞춰 리샘플

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

def _std(values: list) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / n)


async def _imu_motion_state(db, person_id: int, now) -> str:
    """최근 IMU 로 활동/정지 상태를 판정한다. return: 'active' | 'still' | 'unknown'.

    낙상은 IMU 가 직접 신호이고, 야외에서 GPS 가 드롭돼도 판단 가능한 회복 신호.
    accel_norm 변동(std)이 크거나 gyro_norm 평균이 크면 '활동'(회복),
    둘 다 작으면 '정지'(여전히 쓰러짐). 표본이 부족하면 'unknown'.
    """
    since = now - timedelta(minutes=FALL_IMU_LOOKBACK_MINUTES)
    stmt = (
        select(ImuLog.imu_data)
        .where(
            ImuLog.person_id == person_id,
            ImuLog.recorded_at >= since,
            ImuLog.recorded_at <= now,
        )
        .order_by(ImuLog.recorded_at.desc())
        .limit(FALL_IMU_MAX_LOGS)
    )
    logs = (await db.execute(stmt)).scalars().all()
    accel_norms: list = []
    gyro_norms: list = []
    for d in logs:
        if not d:
            continue
        ax, ay, az = d.get("ax", []), d.get("ay", []), d.get("az", [])
        wx, wy, wz = d.get("wx", []), d.get("wy", []), d.get("wz", [])
        for i in range(min(len(ax), len(ay), len(az), len(wx), len(wy), len(wz))):
            accel_norms.append(math.sqrt(ax[i] ** 2 + ay[i] ** 2 + az[i] ** 2))
            gyro_norms.append(math.sqrt(wx[i] ** 2 + wy[i] ** 2 + wz[i] ** 2))
    if len(accel_norms) < FALL_IMU_MIN_SAMPLES:
        return "unknown"
    if _std(accel_norms) >= FALL_ACTIVE_ACCEL_STD or (sum(gyro_norms) / len(gyro_norms)) >= FALL_ACTIVE_GYRO_MEAN:
        return "active"
    return "still"


async def monitor_fall_episodes():
    """
    낙상 후 회복/부동 판정 루프(야외 대상). 마지막 낙상 판정 후 FALL_EPISODE_MINUTES 동안 추가 낙상이 없으면 평가.
    회복은 IMU 활동(낙상은 IMU 가 직접 신호) 또는 GPS 이동(에피소드 동안 벗어남) 중 하나만 있어도 인정한다.
    GPS 는 야외에서 드롭될 수 있어 IMU 가 상호 보완한다.
      - 회복(IMU 활동 or GPS 이동) → is_fall 해제 + 에피소드 종료
      - 부동(이동 신호 없음)       → is_fall 유지 + 에피소드 열어둬 계속 감시(회복 시 자동 해제 → 고착 방지)
      - 판단 불가(IMU·GPS 모두 신호 없음) → 보류(다음 주기 재평가)
    (최초 낙상 알림은 감지 시점에 이미 발송됨 — 여기서는 '상태' 관리만 한다)
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
            # 1) IMU 정지판정 (낙상은 IMU 가 직접 신호 — 회복/부동 판단)
            imu_state = await _imu_motion_state(db, person.id, now)

            # 2) GPS 회전반경 (에피소드 동안 벗어났는지 — 야외 드롭 시 IMU 로 보완): 윈도우 [fall_started_at, now]
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
            points = clean_track([(r[0], r[1], r[2]) for r in rows])
            gps_ok = len(points) >= FALL_MIN_VALID_POINTS
            rg = radius_of_gyration_m(points) if gps_ok else None
            rg_str = f"{rg:.1f}m" if rg is not None else "n/a"

            # 회복: IMU 활동 OR GPS 이동 — 둘 중 하나만 있어도 인정
            #   (IMU 최근창은 '지금 정지'만 보므로, 걸어서 벗어난 뒤 앉은 경우를 GPS Rg 가 잡아준다)
            # 부동: 이동 신호가 하나도 없음(IMU 정지 또는 GPS 정지)
            moved = imu_state == "active" or (gps_ok and rg > FALL_RG_THRESHOLD_M)
            still = imu_state == "still" or (gps_ok and rg <= FALL_RG_THRESHOLD_M)

            if moved:
                person.is_fall = False
                person.fall_pending = False  # 회복 → 에피소드 종료
                logger.info(f"[FALL-EPISODE] personId={person.id} 회복 (imu={imu_state}, Rg={rg_str}) → is_fall 해제·에피소드 종료")
            elif still:
                person.is_fall = True        # 여전히 쓰러짐 → 유지
                # 에피소드는 닫지 않고 계속 감시 → 회복 시 자동 해제(고착 방지)
                logger.warning(f"[FALL-EPISODE] personId={person.id} 부동 지속 (imu={imu_state}, Rg={rg_str}) → is_fall 유지·감시 지속")
            else:
                # IMU 판단불가 & GPS 부족 → 판정 보류(다음 주기 재평가)
                logger.info(f"[FALL-EPISODE] personId={person.id} 판정 보류 (imu={imu_state}, gps점={len(points)})")

        await db.commit()

def _resample_20s(rows) -> list:
    """(lat, lng, created_at[naive UTC]) 시퀀스를 20초당 1점으로 리샘플한다.

    (0,0)·None 제거 후, 각 20초 bin 의 첫 점만 취한다. 반환: [(t_unix, lat, lng), ...]
    """
    out = []
    seen_bins = set()
    for lat, lng, ts in rows:
        if lat is None or lng is None or ts is None:
            continue
        lat_f, lng_f = float(lat), float(lng)
        if lat_f == 0.0 and lng_f == 0.0:
            continue
        epoch = ts.replace(tzinfo=timezone.utc).timestamp()  # created_at 은 naive UTC
        bin_id = int(epoch // WANDER_RESAMPLE_SECONDS)
        if bin_id in seen_bins:
            continue
        seen_bins.add(bin_id)
        out.append((bin_id * WANDER_RESAMPLE_SECONDS, lat_f, lng_f))
    return out


async def enroll_wandering_models():
    """[하루 1회] 미등록 환자 중 최근 WANDER_ENROLL_DAYS 일 GPS 가 게이트 충족 시 RF 개인 모델을 학습(enroll).

    게이트: 유효 일수 >= WANDER_ENROLL_MIN_DAYS AND 20초 리샘플 fix >= WANDER_ENROLL_MIN_FIXES.
    성공 시 wandering_enrolled=True. 재등록(생활패턴 변화)은 자동이 아니라 수동 트리거만 한다.
    """
    async with get_independent_session() as db:
        since = utcnow() - timedelta(days=WANDER_ENROLL_DAYS)
        persons = (await db.execute(
            select(TrackedPerson).where(TrackedPerson.wandering_enrolled == False)
        )).scalars().all()
        if not persons:
            return

        async with httpx.AsyncClient(base_url=settings.AI_WANDER_URL, timeout=60.0) as client:
            for person in persons:
                rows = (await db.execute(
                    select(GpsLog.latitude, GpsLog.longitude, GpsLog.created_at)
                    .where(GpsLog.person_id == person.id, GpsLog.created_at >= since)
                    .order_by(GpsLog.created_at)
                )).all()
                fixes_rs = _resample_20s(rows)
                distinct_days = len({int(t) // 86400 for t, _, _ in fixes_rs})

                if len(fixes_rs) < WANDER_ENROLL_MIN_FIXES or distinct_days < WANDER_ENROLL_MIN_DAYS:
                    logger.info(f"[WANDER-ENROLL] personId={person.id} 데이터 부족(fix={len(fixes_rs)}, days={distinct_days}) → 학습 보류")
                    continue

                payload = {"fixes": [{"lat": lat, "lng": lng, "t": t} for t, lat, lng in fixes_rs]}
                try:
                    resp = await client.post(f"/users/{person.id}/enroll", json=payload)
                    if resp.status_code == 200:
                        person.wandering_enrolled = True
                        info = resp.json()
                        logger.info(f"[WANDER-ENROLL] personId={person.id} 등록 완료 (fix={len(fixes_rs)}, days={distinct_days}, thr={info.get('threshold')})")
                    else:
                        logger.error(f"[WANDER-ENROLL] personId={person.id} enroll 실패 HTTP {resp.status_code}: {resp.text[:200]}")
                except Exception as e:
                    logger.error(f"[WANDER-ENROLL] personId={person.id} enroll 예외: {e}")

        await db.commit()


def start_scheduler():
    logger.info("## [스케줄러] start_scheduler() 함수가 호출되었습니다! ##")

    # 1분 주기로 하트비트 모니터링 루프 가동
    scheduler.add_job(monitor_device_heartbeats, 'interval', minutes=1)
    # 1분 주기로 낙상 부동 판정 루프 가동
    scheduler.add_job(monitor_fall_episodes, 'interval', minutes=1)
    # 하루 1회(새벽 3시) 미등록 환자 배회 RF 모델 enroll
    scheduler.add_job(enroll_wandering_models, 'cron', hour=3)
    scheduler.start()

def stop_scheduler():
    scheduler.shutdown()
