import os
import asyncio
import logging
from datetime import datetime
from exponent_server_sdk import PushClient, PushMessage, PushServerError

from backend.core.websocket import manager

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.database import get_db, get_independent_session
from backend.models.guardian import Guardian
from backend.models.person import TrackedPerson
from backend.models.alert import AlertLog
from backend.models.telemetry import GpsLog
from backend.schemas.ai import AIPredictRequest
from backend.services.ai_client import ai_client
from backend.utils.time import utcnow, isoformat_utc
import httpx

logger = logging.getLogger(__name__)

async def get_guardian_token_and_name(person_id: int) -> dict:
    """환자 ID를 통해 보호자의 ID 및 Expo Push 토큰을 비동기로 안전하게 조회"""
    async with get_independent_session() as db:
        stmt = (
            select(TrackedPerson.guardian_id, Guardian.expo_token)
            .join(Guardian, TrackedPerson.guardian_id == Guardian.id)
            .where(TrackedPerson.id == person_id)
        )
        res = await db.execute(stmt)
        row = res.first()
        if row:
            return {"guardian_id": row[0], "expo_token": row[1]}
    return None

async def send_emergency_push(expo_token: str, elder_name: str, alert_type: str):
    """ Expo Push API를 비동기(논블로킹)로 호출하여 실기기 알림을 전송하는 함수 """
    if not expo_token or not expo_token.startswith("ExponentPushToken["):
        logger.error(f"잘못된 Expo 토큰 형식입니다: {expo_token}")
        return

    expo_push_url = "https://exp.host/--/api/v2/push/send" # expo push service의 rest api 엔드포인트
    
    # 알림 타입에 따른 메시지 분기 (wandering / fall_detected / offline)
    if alert_type == "fall_detected":
        body_msg = "낙상이 감지되었습니다. 즉시 확인해주세요!"
    elif alert_type == "wandering":
        body_msg = "배회가 감지되었습니다. 확인해주세요."
    elif alert_type == "offline":
        body_msg = "기기가 오프라인 상태입니다. 전원을 확인해주세요."
    else:
        body_msg = "긴급 상황이 감지되었습니다."

    payload = {
        "to": expo_token,
        "sound": "default",
        "title": f"🚨 긴급 알림: {elder_name} 어르신",
        "body": body_msg,
        "priority": "high",
        "data": {"screen": "EmergencyMap", "alert_type": alert_type}
    }

    # 비동기 HTTP 클라이언트를 사용하여 메인 스레드 블로킹 방지
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(expo_push_url, json=payload, timeout=5.0)
            result = response.json()
            
            # Expo 서버 자체는 200 OK를 주더라도, 내부 데이터에 에러가 있을 수 있음
            if "data" in result and result["data"].get("status") == "error":
                logger.error(f"Expo 푸시 발송 에러 (토큰 만료 등): {result['data'].get('details')}")
            else:
                logger.info(f"[{elder_name}] 푸시 발송 성공!")
                
        except httpx.RequestError as e:
            logger.error(f"Expo API 네트워크 통신 에러: {str(e)}")

class NotificationService:
    @staticmethod
    async def broadcast_event(db: AsyncSession, guardian_id: int, event_type: str, person_id: str, payload_data: dict, payload: AIPredictRequest):
        """
        WebSocket(대시보드용)과 Expo Push(모바일용)를 비동기적으로 동시 실행.
        event_type: 'location', 'status', 'alert' 중 하나
        """
        message_payload = {}

        if payload:
            # [GPS 경로] 배회(wandering)만 처리한다. 낙상은 /fall-suspect(IMU) 경로가 담당.
            ai_result = await ai_client.predict(payload)
            if not ai_result:
                return

            is_wander = ai_result.wandering_detection.is_triggered if ai_result.wandering_detection is not None else False

            async with get_independent_session() as sess:
                person = (await sess.execute(
                    select(TrackedPerson).where(TrackedPerson.id == int(person_id))
                )).scalar_one_or_none()
                if not person:
                    return

                if is_wander:
                    # 이미 배회 상태면 알림 억제 (최초 감지 시 1회만 발송)
                    if person.is_wandering:
                        return
                    person.is_wandering = True
                    guardian_id = person.guardian_id
                    name = person.name
                    # 지도 지점별 표시용: 배회 감지 순간의 최신 gps_log 에 플래그
                    latest_gps = (await sess.execute(
                        select(GpsLog)
                        .where(GpsLog.person_id == int(person_id))
                        .order_by(GpsLog.created_at.desc())
                        .limit(1)
                    )).scalar_one_or_none()
                    if latest_gps is not None:
                        latest_gps.is_wandering_detected = True
                    # 이력 저장: _notify 는 실시간(WS+Push) 전용이므로 호출부에서 alert_logs 를 남긴다.
                    sess.add(AlertLog(
                        person_id=int(person_id),
                        alert_type="wandering",
                        message=f"{name}님의 배회가 감지되었습니다",
                        created_at=utcnow(),
                    ))
                    await sess.commit()
                else:
                    # 배회 아님 → 플래그 해제 (알림 없음)
                    if person.is_wandering:
                        person.is_wandering = False
                        await sess.commit()
                    return

            # 최초 배회 감지 → 알림 1회 (WebSocket + Expo Push)
            await NotificationService._notify(
                person_id, guardian_id, "wandering", f"{name}님의 배회가 감지되었습니다"
            )
            return
        else:
            if event_type == "location":
                message_payload = {
                    "type": "location",
                    "personId": person_id,
                    "data": payload_data # latitude, longitude, address, isFallConfirmed, updatedAt
                }
            elif event_type == "status":
                message_payload = {
                    "type": "status",
                    "personId": person_id,
                    "status": payload_data.get("status", "alert")
                }
            elif event_type == "alert":
                message_payload = {
                    "type": "alert",
                    "alert": {
                        "id": payload_data.get("id"),
                        "personId": person_id,
                        "type": payload_data.get("type"),  # wandering, fall_detected, offline
                        "message": payload_data.get("message"),
                        "createdAt": isoformat_utc(utcnow()), # UTC ISO 8601 (...Z)
                        "read": False
                    }
                }
            else:
                logger.warning(f"Unknown event type: {event_type}")
                return
        
        
        # 1. WebSocket 전송 태스크
        ws_task = manager.send_personal_message(message_payload, guardian_id)

        # 2. Expo Push 전송 태스크 (else 분기 전용 — payload 분기는 위에서 _notify 로 처리 후 return)
        expo_task = NotificationService._send_expo_push(db, guardian_id, payload_data)

        # 두 작업을 병렬로 동시 실행 (I/O 병목 방지)
        results = await asyncio.gather(ws_task, expo_task, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Notification task failed: {result}")

    @staticmethod
    async def _notify(person_id, guardian_id: int, alert_type: str, message: str):
        """단일 알림 발송: WebSocket + Expo Push (배회/낙상 공용)"""
        if not guardian_id:
            return
        ws_payload = {
            "type": "alert",
            "alert": {
                "personId": str(person_id),
                "type": alert_type,
                "message": message,
                "createdAt": isoformat_utc(utcnow()),  # UTC ISO 8601 (...Z)
                "read": False,
            },
        }
        ws_task = manager.send_personal_message(ws_payload, guardian_id)
        async with get_independent_session() as db:
            expo_task = NotificationService._send_expo_push(db, guardian_id, {"type": alert_type})
            results = await asyncio.gather(ws_task, expo_task, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"Notification task failed: {r}")

    @staticmethod
    async def _send_expo_push(db: AsyncSession, guardian_id: int, extra_data: dict = None):
        """기존 sdk 기반 Expo Push 발송 로직 (비동기 스레드 풀에서 실행)"""
        # DB에서 guardian_id에 매핑된 Expo 푸시 토큰 조회
        if not db or not guardian_id:
            return

        stmt = select(Guardian.expo_token).where(
            Guardian.id == guardian_id
        )

        expo_token = (await db.execute(stmt)).scalars().first()

        if not expo_token:
            logger.info(f"no expo token. Skipping push notification")
            return

        alert_type = extra_data.get("type") if extra_data else None

        title, body = "알림", "새로운 상태 업데이트가 있습니다."

        if alert_type == 'wandering':
            title, body = "🚨 배회 감지", "배회가 감지되었습니다"
        elif alert_type == 'fall_detected':
            title, body = "🚨 위험 상황 발생", "낙상이 감지되었습니다"
        elif alert_type == 'offline':
            title, body = "🚨 오프라인", "기기가 오프라인 상태입니다"

        try:
            # Expo 푸시 메시지 객체 생성
            message = PushMessage(
                to=expo_token,
                title=title,
                body=body,
                data=extra_data or {},
                sound="default",    # 알림 소리 발생
                priority="high"     # 즉시 전송 및 화면 깨우기(OS 정책에 따라 다름)
            )

            # SDK publish가 동기 함수이므로 to_thread를 통해 비동기 처리
            await asyncio.to_thread(PushClient().publish, message)
            
            # response.is_error 등 Expo 고유의 에러 응답 포맷 검증 가능
            logger.info(f"Successfully sent Expo push message")
            
        except Exception as e:
            logger.error(f"Unexpected error during Expo push: {e}")
            raise e