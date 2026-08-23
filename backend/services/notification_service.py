import os
import asyncio
import logging
from datetime import datetime
from exponent_server_sdk import PushClient, PushMessage, PushServerError

from backend.core.websocket import manager, publish as ws_publish

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

logger = logging.getLogger(__name__)

# Android heads-up(창) 알림용 채널 id. 앱이 동일 id의 채널을 HIGH/MAX importance 로
# 등록(setNotificationChannelAsync)해야 배너/헤드업으로 표시된다. 미지정 시 저importance
# 기본 채널로 처리돼 앱 UI 배너가 뜨지 않는다. iOS 는 무시되는 필드라 안전하다.
EXPO_ANDROID_CHANNEL_ID = "default"

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
                        message=f"Wandering detected for {name}.",
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
                person_id, guardian_id, "wandering", f"Wandering detected for {name}."
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
        # 멀티워커에서 어느 워커가 이 코드를 실행하든 소켓이 붙은 워커로 팬아웃되도록
        # 로컬 전송(manager.send_personal_message) 대신 Redis Pub/Sub로 발행한다.
        ws_task = ws_publish(ws_payload, guardian_id)
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

        title, body = "Notification", "You have a new status update."

        if alert_type == 'wandering':
            title, body = "🚨 Wandering Detected", "Wandering has been detected."
        elif alert_type == 'fall_detected':
            title, body = "🚨 Emergency: Fall Detected", "A fall has been detected."
        elif alert_type == 'offline':
            title, body = "🚨 Device Offline", "The device is offline."

        try:
            # Expo 푸시 메시지 객체 생성
            message = PushMessage(
                to=expo_token,
                title=title,
                body=body,
                data=extra_data or {},
                sound="default",                        # 알림 소리 발생
                priority="high",                        # 즉시 전송 및 화면 깨우기(OS 정책에 따라 다름)
                channel_id=EXPO_ANDROID_CHANNEL_ID,     # Android heads-up 표시용(SDK가 channelId 로 직렬화)
            )

            # SDK publish가 동기 함수이므로 to_thread를 통해 비동기 처리
            await asyncio.to_thread(PushClient().publish, message)
            
            # response.is_error 등 Expo 고유의 에러 응답 포맷 검증 가능
            logger.info(f"Successfully sent Expo push message")
            
        except Exception as e:
            logger.error(f"Unexpected error during Expo push: {e}")
            raise e