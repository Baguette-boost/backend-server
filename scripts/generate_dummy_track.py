import asyncio
import random
from datetime import datetime, timedelta

from backend.database import AsyncSessionLocal
from backend.models.telemetry import GpsLog 
import uuid

# 기준 좌표 (예: CBNU 위경도)
BASE_LAT = 36.6285
BASE_LNG = 127.4568

async def generate_trajectory(person_id: str, steps: int = 50, interval_minutes: int = 5):
    """지정된 person_id에 대해 자연스럽게 이동하는 위경도 시계열 데이터를 생성합니다."""
    
    current_lat = BASE_LAT
    current_lng = BASE_LNG
    # 현재 시간으로부터 (steps * interval) 분 전부터 시작
    current_time = datetime.utcnow() - timedelta(minutes=steps * interval_minutes)

    # 세션 시작-종료 직접 관리하기 위해, get_db() 대신 AsyncSessionLocal 사용
    async with AsyncSessionLocal() as session:
        for i in range(steps):
            # 위경도 Random Walk (약 10~50m 씩 이동하도록 난수 생성)
            current_lat += random.uniform(-0.0005, 0.0005)
            current_lng += random.uniform(-0.0005, 0.0005)
            current_time += timedelta(minutes=interval_minutes)

            dummy_location = GpsLog(
                id=uuid.uuid4(),
                person_id=person_id,
                latitude=current_lat,
                longitude=current_lng,
                timestamp=current_time
            )
            session.add(dummy_location)

        await session.commit()
        print(f"[SUCCESS] {steps}개의 연속된 궤적 데이터가 {person_id}에게 주입되었습니다.")
        print(f"종점 좌표: Lat {current_lat:.6f}, Lng {current_lng:.6f}")

if __name__ == "__main__":
    TARGET_PERSON_ID = "test-person-001"
    # 비동기 실행 루프
    asyncio.run(generate_trajectory(TARGET_PERSON_ID, steps=60, interval_minutes=2))
