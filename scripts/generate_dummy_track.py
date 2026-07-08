import asyncio
import random
from datetime import datetime, timedelta

from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models.telemetry import GpsLog
from backend.models.guardian import Guardian
from backend.models.person import TrackedPerson
import uuid

# 기준 좌표 (예: CBNU 위경도)
BASE_LAT = 36.6285
BASE_LNG = 127.4568

async def generate_trajectory(steps: int = 50, interval_minutes: int = 5):
    """지정된 person_id에 대해 자연스럽게 이동하는 위경도 시계열 데이터를 생성합니다."""
    
    current_lat = BASE_LAT
    current_lng = BASE_LNG
    # 현재 시간으로부터 (steps * interval) 분 전부터 시작
    current_time = datetime.utcnow() - timedelta(minutes=steps * interval_minutes)

    # 세션 시작-종료 직접 관리하기 위해, get_db() 대신 AsyncSessionLocal 사용
    async with AsyncSessionLocal() as session:    
        # 외래키 제약 고려해 보호자 먼저 삽입
        target_phone = "01012345678"
        stmt_guardian = select(Guardian).where(Guardian.phone == target_phone)
        result_guardian = await session.execute(stmt_guardian)
        guardian = result_guardian.scalar_one_or_none() # 보호자 데이터 존재 여부 확인

        if not guardian:
            guardian = Guardian(
                name="guardian0",
                phone=target_phone,
                password="PASSWD",
                expo_token="EXPO_TOKEN"
            )
            session.add(guardian)
            await session.flush()
            print(f"[SUCCESS] 새 보호자 데이터 생성 (ID: {guardian.id})")
        else:
            printf(f"[INFO] 기존 보호자 데이터 존재 (ID: {guardian.id})")
    

        # 외래키 제약 고려해 대상 환자 먼저 삽입
        target_device_id = "DEVICE_ID"
        stmt_person = select(TrackedPerson).where(TrackedPerson.device_id == target_device_id)
        result_person = await session.execute(stmt_person)
        person = result_person.scalar_one_or_none()

        if not person:
            person = TrackedPerson(
                guardian_id=guardian.id,
                name="person0",
                age=70,
                device_id=target_device_id,
                device_token="DEVICE_TOKEN",
                is_active=True,
                base_lat=BASE_LAT,
                base_lng=BASE_LNG,
                safe_radius=100
            )
            session.add(person)
            await session.flush()
            print(f"[SUCCESS] 새 환자 데이터 생성 (ID: {person.id})")
        else:
            print(f"[INFO] 기존 환자 데이터 존재 (ID: {person.id})")

        actual_person_id = person.id

        for i in range(steps):
            # 위경도 Random Walk (약 10~50m 씩 이동하도록 난수 생성)
            current_lat += random.uniform(-0.0005, 0.0005)
            current_lng += random.uniform(-0.0005, 0.0005)
            current_time += timedelta(minutes=interval_minutes)

            dummy_location = GpsLog(
                person_id=actual_person_id,
                latitude=current_lat,
                longitude=current_lng,
                is_fall_detected=False,
                is_wandering_detected=False,
                created_at=current_time
            )
            session.add(dummy_location)

        # 모든 데이터 한번에 커밋하여 트랜젝션 종료
        await session.commit()
        print(f"[SUCCESS] {steps}개의 연속된 궤적 데이터가 주입되었습니다.")
        print(f"종점 좌표: Lat {current_lat:.6f}, Lng {current_lng:.6f}")

if __name__ == "__main__":
    # 비동기 실행 루프
    asyncio.run(generate_trajectory(steps=60, interval_minutes=2))
