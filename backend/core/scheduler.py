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
from backend.services.notification_service import NotificationService
from backend.utils.time import utcnow
from backend.utils.geo import clean_track, radius_of_gyration_m
from backend.config import settings
from backend.core.buffer import get_patient_gps_history
from backend.schemas.ai import AIPredictRequest
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

# 배회 판정 디바운스 주기(Issue #6): /detect가 매 호출마다 버퍼(최근 15분) 전체를
# 처음부터 재평가하는 순수 함수라, GPS 핑(20초)마다 대신 이 주기로 호출을 줄여도
# 놓치는 이벤트 없이 backend CPU 부하만 줄어든다.
WANDER_CHECK_INTERVAL_MINUTES = 1

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

            # WebSocket(대시보드) + Expo Push(모바일)를 wandering/fall_detected 와 동일하게
            # _notify() 단일 경로로 발송한다(예전엔 broadcast_event 의 Expo Push와
            # send_emergency_push 가 중복 호출되어 offline 알림이 두 번 발송되고 있었다).
            asyncio.create_task(
                NotificationService._notify(person.id, person.guardian_id, "offline", alert_msg)
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
                logger.info(f"[FALL-EPISODE pid={person.id}]회복 (imu={imu_state}, Rg={rg_str}) → is_fall 해제·에피소드 종료")
            elif still:
                person.is_fall = True        # 여전히 쓰러짐 → 유지
                # 에피소드는 닫지 않고 계속 감시 → 회복 시 자동 해제(고착 방지)
                logger.warning(f"[FALL-EPISODE pid={person.id}]부동 지속 (imu={imu_state}, Rg={rg_str}) → is_fall 유지·감시 지속")
            else:
                # IMU 판단불가 & GPS 부족 → 판정 보류(다음 주기 재평가)
                logger.info(f"[FALL-EPISODE pid={person.id}]판정 보류 (imu={imu_state}, gps점={len(points)})")

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


async def enroll_person_wandering(db, person, client: httpx.AsyncClient) -> dict:
    """한 환자의 최근 WANDER_ENROLL_DAYS 일 GPS 로 RF 개인 모델을 학습(enroll)한다.

    게이트: 유효 일수 >= WANDER_ENROLL_MIN_DAYS AND 20초 리샘플 fix >= WANDER_ENROLL_MIN_FIXES.
    성공 시 person.wandering_enrolled=True (커밋은 호출부 책임). 스케줄러 잡·수동 재등록 공용.
    return: {enrolled: bool, fixes: int, days: int, reason?: str, threshold?: float}
    """
    since = utcnow() - timedelta(days=WANDER_ENROLL_DAYS)
    rows = (await db.execute(
        select(GpsLog.latitude, GpsLog.longitude, GpsLog.created_at)
        .where(GpsLog.person_id == person.id, GpsLog.created_at >= since)
        .order_by(GpsLog.created_at)
    )).all()
    fixes_rs = _resample_20s(rows)
    distinct_days = len({int(t) // 86400 for t, _, _ in fixes_rs})

    if len(fixes_rs) < WANDER_ENROLL_MIN_FIXES or distinct_days < WANDER_ENROLL_MIN_DAYS:
        return {"enrolled": False, "reason": "insufficient_data", "fixes": len(fixes_rs), "days": distinct_days}

    payload = {"fixes": [{"lat": lat, "lng": lng, "t": t} for t, lat, lng in fixes_rs]}
    try:
        resp = await client.post(f"/users/{person.id}/enroll", json=payload)
    except Exception as e:
        logger.error(f"[WANDER-ENROLL pid={person.id}]enroll 예외: {e}")
        return {"enrolled": False, "reason": "request_error", "fixes": len(fixes_rs), "days": distinct_days}

    if resp.status_code == 200:
        person.wandering_enrolled = True
        info = resp.json()
        return {"enrolled": True, "fixes": len(fixes_rs), "days": distinct_days, "threshold": info.get("threshold")}
    logger.error(f"[WANDER-ENROLL pid={person.id}]enroll 실패 HTTP {resp.status_code}: {resp.text[:200]}")
    return {"enrolled": False, "reason": f"server_{resp.status_code}", "fixes": len(fixes_rs), "days": distinct_days}


async def enroll_wandering_models():
    """[하루 1회] 미등록(wandering_enrolled=False) 환자를 스캔해 게이트 충족 시 자동 enroll."""
    async with get_independent_session() as db:
        persons = (await db.execute(
            select(TrackedPerson).where(TrackedPerson.wandering_enrolled == False)
        )).scalars().all()
        if not persons:
            return
        async with httpx.AsyncClient(base_url=settings.AI_WANDER_URL, timeout=60.0) as client:
            for person in persons:
                r = await enroll_person_wandering(db, person, client)
                if r["enrolled"]:
                    logger.info(f"[WANDER-ENROLL pid={person.id}]등록 완료 (fix={r['fixes']}, days={r['days']}, thr={r.get('threshold')})")
                elif r["reason"] == "insufficient_data":
                    logger.info(f"[WANDER-ENROLL pid={person.id}]데이터 부족(fix={r['fixes']}, days={r['days']}) → 학습 보류")
        await db.commit()


async def check_wandering():
    """[1분 주기] 활성 + RF 등록 완료 환자를 배치로 배회 판정한다(Issue #6).

    RF 모델이 미등록(wandering_enrolled=False)이면 /detect가 항상 404(비트리거)라
    호출 자체를 스킵한다. GPS 버퍼 조회·AI 호출·알림은 기존 receive_gps 경로가
    쓰던 것과 동일한 NotificationService.broadcast_event()를 그대로 재사용한다
    (판정/알림 로직 중복 구현 방지).
    """
    async with get_independent_session() as db:
        person_ids = (await db.execute(
            select(TrackedPerson.id).where(
                TrackedPerson.is_active == True,
                TrackedPerson.wandering_enrolled == True,
            )
        )).scalars().all()

    for person_id in person_ids:
        gps_list = await get_patient_gps_history(person_id)
        if not gps_list:
            continue
        payload = AIPredictRequest(personId=person_id, imuData=None, gpsData=gps_list)
        await NotificationService.broadcast_event(
            db=None, guardian_id=None, event_type=None,
            person_id=person_id, payload_data=None, payload=payload,
        )


def start_scheduler():
    logger.info("## [스케줄러] start_scheduler() 함수가 호출되었습니다! ##")

    # 1분 주기로 하트비트 모니터링 루프 가동
    scheduler.add_job(monitor_device_heartbeats, 'interval', minutes=1)
    # 1분 주기로 낙상 부동 판정 루프 가동
    scheduler.add_job(monitor_fall_episodes, 'interval', minutes=1)
    # 1분 주기로 배회 배치 판정 (Issue #6 — GPS 핑 경로에서 분리)
    scheduler.add_job(check_wandering, 'interval', minutes=WANDER_CHECK_INTERVAL_MINUTES)
    # 하루 1회(새벽 3시) 미등록 환자 배회 RF 모델 enroll
    scheduler.add_job(enroll_wandering_models, 'cron', hour=3)
    scheduler.start()

def stop_scheduler():
    scheduler.shutdown()
