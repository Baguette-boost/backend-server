"""알림 저장 헬퍼 모듈.

기존 `save_wandering_alert`(배회 감지 스케줄러 전용)는 배회 처리가
GPS 수신 경로(receive_gps → NotificationService.broadcast_event)로 일원화되면서 제거되었다.
현재 사용 중인 함수는 없으며, 향후 알림 관련 공용 헬퍼가 필요할 때 이 모듈에 추가한다.
"""
