from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

# from backend.core.security import get_password_hash # 비밀번호 해싱 함수
from backend.models.guardian import Guardian
from backend.models.person import TrackedPerson

async def init_master_user(db: AsyncSession):
    master_phone = "010-0000-0000"
    stmt = select(Guardian).where(Guardian.phone == master_phone)
    master_user = (await db.execute(stmt)).scalar_one_or_none()
    
    if not master_user:
        # 마스터 보호자 계정이 없으면 생성 (더미 환자 데이터와 연결 가능)
        new_master = Guardian(
            phone=master_phone,
            password="master1234!", # get_password_hash("master1234!"),
            name="마스터보호자",
            expo_token="PUSH_TOKEN"
        )
        db.add(new_master)
        await db.commit()
        await db.refresh(master_user)
        print("✅ 마스터 유저(010-0000-0000)가 성공적으로 적재되었습니다.")
    
    stmt_person = select(TrackedPerson).where(TrackedPerson.guardian_id == master_user.id)
    dummy_person = (await db.execute(stmt_person)).scalars().first()

    if not dummy_person:
        dummy_person = TrackedPerson(
            guardian_id=master_user.id,
            name="김어르신(더미)",
            age=85,
            device_id="dummy_device_id",
            device_token="dummy_device_token",
            current_battery=100,
            is_active=True,
            base_lat=37.50123,
            base_lng=127.03645,
            safe_radius=500
        )
        db.add(dummy_person)
        await db.commit()
        print("✅ 더미 환자(김어르신) 적재 완료.")