import os
import asyncio
import logging
from datetime import datetime
from exponent_server_sdk import PushClient, PushMessage, PushServerError

from backend.core.websocket import manager

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.database import get_db
from backend.models.guardian import Guardian
from backend.models.person import TrackedPerson
from backend.schemas.ai import AIPredictRequest
from backend.services.ai_client import ai_client
from backend.database import get_independent_session

logger = logging.getLogger(__name__)

class NotificationService:
    @staticmethod
    async def broadcast_event(db: AsyncSession, guardian_id: int, event_type: str, person_id: str, payload_data: dict, payload: AIPredictRequest):
        """
        웹소켓 이벤트 브로드캐스팅용 함수
        WebSocket(대시보드용)과 Expo Push(모바일용)를 비동기적으로 동시 실행.
        event_type: 'location', 'telemetry', 'status', 'alert' 중 하나
        """
        message_payload = {}

        if payload:
            # AI 컨테이너에 데이터 전달 및 결과 대기
            ai_result = await ai_client.predict(payload)

            if not ai_result:
                return
            
            # AI 결과 확인
            is_fall = ai_result.fall_detection.is_triggered if ai_result.fall_detection is not None else False
            is_wander = ai_result.wandering_detection.is_triggered if ai_result.wandering_detection is not None else False

            if is_fall or is_wander:
                # 위험 감지 시, 해당 환자의 보호자 ID 조회
                async with get_independent_session() as db:
                    stmt_guard = select(TrackedPerson).where(TrackedPerson.id == person_id)
                    rst = (await db.execute(stmt_guard)).scalar_one_or_none()
                    guardian_id = rst.guardian_id
                    name = rst.name
                
                if guardian_id:
                    # 앱으로 보낼 페이로드 구성
                    return_type = "fall_detected" if is_fall else "zone_exit"
                    message_payload = {
                        "type": "alert",
                        "alert": {
                            "personId": person_id,
                            "type": return_type,
                            "message": f"{name}님의 낙상" if is_fall else f"{name}님의 배회",
                            "createdAt": datetime.utcnow().isoformat() + "Z", # 명세서 형식(ISO)
                            "read": False
                        }
                    }
        else:
            if event_type == "location":
                message_payload = {
                    "type": "location",
                    "personId": person_id,
                    "data": payload_data # latitude, longitude, address, zoneLabel, inSafeZone, isFallConfirmed, updatedAt
                }
            elif event_type == "telemetry":
                message_payload = {
                    "type": "telemetry",
                    "personId": person_id,
                    "data": payload_data  # battery, lastUpdated
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
                        "type": payload_data.get("type"),  # zone_exit, low_battery, fall_detected, offline
                        "message": payload_data.get("message"),
                        "createdAt": datetime.utcnow().isoformat() + "Z", # 명세서 형식(ISO)
                        "read": False
                    }
                }
            else:
                logger.warning(f"Unknown event type: {event_type}")
                return
        
        
        # 1. WebSocket 전송 태스크
        ws_task = manager.send_personal_message(message_payload, guardian_id)

        # 2. Expo Push 전송 태스크
        expo_task = NotificationService._send_expo_push(db, guardian_id, {"type": return_type}) if payload else NotificationService._send_expo_push(db, guardian_id, payload_data)

        # 두 작업을 병렬로 동시 실행 (I/O 병목 방지)
        results = await asyncio.gather(ws_task, expo_task, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Alert task failed: {result}")

    @staticmethod
    async def _send_expo_push(db: AsyncSession, guardian_id: int, extra_data: dict = None):
        """Expo Push 발송 로직 (비동기 스레드 풀에서 실행)"""
        # DB에서 guardian_id에 매핑된 Expo 푸시 토큰 조회
        stmt = select(Guardian.expo_token).where(
            Guardian.id == guardian_id
        )

        expo_token = (await db.execute(stmt)).scalars().first()

        if not expo_token:
            logger.info(f"Guardian {guardian_id} has no expo token. Skipping push notification")
            return

        alert_type = extra_data.get("type") if extra_data else None

        title = "알림"
        body = "새로운 상태 업데이트가 있습니다."

        if alert_type == 'zone_exit':
            title = "🚨 안전 구역 이탈"
            body = "안전 구역 이탈이 감지되었습니다"
        elif alert_type == 'low_battery':
            title = "🚨 배터리 부족"
            body = "기기 배터리가 부족합니다"
        elif alert_type == 'fall_detected':
            title = "🚨 위험 상황 발생"
            body = "낙상이 감지되었습니다"
        elif alert_type == 'offline':
            title = "🚨 오프라인"
            body = "기기가 오프라인 상태입니다"

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
            response = await asyncio.to_thread(PushClient().publish, message)
            
            # response.is_error 등 Expo 고유의 에러 응답 포맷 검증 가능
            logger.info(f"Successfully sent Expo push message. ID: {response.id}")
            
        except PushServerError as e:
            logger.error(f"Expo push validation/sending failed for guardian {guardian_id}: {e}")
            raise e
        except Exception as e:
            logger.error(f"Unexpected error during Expo push: {e}")
            raise e
