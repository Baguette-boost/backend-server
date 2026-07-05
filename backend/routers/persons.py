from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List
from datetime import datetime
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update
import asyncio

# 내부 모듈 임포트 (경로는 프로젝트 환경에 맞게 수정)
from backend.schemas.person import PersonCreate, PersonResponse, LocationAbstractResponse, LocationResponse, LocationHistoryResponse, ZoneData, DeviceVerifyRequest
from backend.core.security import get_current_user

from backend.database import get_db, get_independent_session
from backend.models.person import TrackedPerson
from backend.models.telemetry import GpsLog
from backend.models.guardian import Guardian

from typing import Annotated

person_router = APIRouter(prefix="/persons", tags=["Persons"])

# 헬퍼 함수: 권한 검증 (실제 DB 쿼리로 대체 필요)
async def check_guardian_ownership(person_id: int, guardian_id: int, db: AsyncSession):
    stmt = select(TrackedPerson).where(
        TrackedPerson.id == person_id
    )
    person = (await db.execute(stmt)).scalars().first()

    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="환자를 찾을 수 없습니다.")
    
    if person.guardian_id != guardian_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="해당 환자 정보에 접근할 권한이 없습니다."
        )
    return person


@person_router.post("", response_model=PersonResponse, status_code=status.HTTP_201_CREATED)
async def register_person(
    payload: PersonCreate,
    current_guardian: Guardian = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """환자 등록 및 디바이스 페어링"""
    # device_id 발급
    new_device_id = str(uuid.uuid4())
        
    # 2. DB 저장 로직 (Guardian ID와 함께 저장)
    new_person = TrackedPerson(
        guardian_id=current_guardian.id,
        name=payload.name,
        age=payload.age,
        device_id=new_device_id,
        device_token=payload.device_token,
        current_battery=payload.current_battery,
        is_active=True,
        base_lat=payload.base_lat,
        base_lng=payload.base_lng,
        safe_radius=payload.safe_radius
    )
    db.add(new_person)
    await db.commit()
    await db.refresh(new_person)

    return new_person

@person_router.get("", response_model=List[PersonResponse])
async def get_persons(current_guardian: Guardian = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """보호자가 관리하는 환자 목록 조회"""
    stmt = select(TrackedPerson).where(
        TrackedPerson.guardian_id == current_guardian.id
    )

    rst = (await db.execute(stmt)).scalars().all()
    
    if not rst:
        raise HTTPException(status_code=404, detail="환자 데이터를 찾을 수 없습니다.")
    
    return rst

@person_router.get("/{id}", response_model=PersonResponse)
async def get_person_detail(id: int, current_user: Annotated[Guardian, Depends(get_current_user)], db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TrackedPerson).where(TrackedPerson.id == id, TrackedPerson.guardian_id == current_user.id))
    person = result.scalar_one_or_none()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return person

@person_router.patch("/{id}", response_model=PersonResponse)
async def update_person(
    id: int, 
    payload: PersonCreate, 
    current_user: Annotated[Guardian, Depends(get_current_user)], 
    db: AsyncSession = Depends(get_db)
):
    stmt = update(TrackedPerson).where(TrackedPerson.id == id, TrackedPerson.guardian_id == current_user.id).values(**payload.model_dump(exclude_unset=True))
    await db.execute(stmt)
    await db.commit()
    
    res = await db.execute(select(TrackedPerson).where(TrackedPerson.id == id))
    return res.scalar_one()

@person_router.delete("/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_person(
    person_id: int,
    current_guardian: Guardian = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """추적하는 대상을 삭제"""
    # 1. 소유권 검증 (중요)
    await check_guardian_ownership(person_id, current_guardian.id, db)
    
    stmt = select(TrackedPerson).where(
        TrackedPerson.id == person_id
    )
    person = (await db.execute(stmt)).scalar_one_or_none()
    if not person:
        raise HTTPException(status_code=404, detail="환자를 찾을 수 없습니다")
    
    stmt = delete(TrackedPerson).where(
        TrackedPerson.id == person_id
    )
    await db.execute(stmt)
    await db.commit()
    return None

@person_router.post("/verify", status_code=status.HTTP_200_OK)
async def verify_device(payload: DeviceVerifyRequest, db: AsyncSession = Depends(get_db)):
    # 하드웨어 기기가 유효한 기기 토큰을 지녔는지 검증하고 환자와 연동
    result = await db.execute(select(TrackedPerson).where(TrackedPerson.device_token == payload.device_token))
    person = result.scalar_one_or_none()
    if not person:
        raise HTTPException(status_code=400, detail="Invalid Device Token")
        
    await db.execute(update(TrackedPerson).where(TrackedPerson.id == payload.person_id).values(device_token=payload.device_token))
    await db.commit()
    return {"status": "verified", "mapped_person_id": payload.person_id}

@person_router.get("/{person_id}/location", response_model=LocationResponse)
async def get_person_location(
    person_id: int,
    current_guardian: Guardian = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """최신 위경도, 배터리, AI 최종 낙상 확정 여부 반환"""
    # 1. 소유권 검증 (중요)
    await check_guardian_ownership(person_id, current_guardian.id, db)
    
    # 2. 캐시(Redis) 또는 DB에서 최신 위치 데이터 조회
    stmt = select(GpsLog).where(
        GpsLog.person_id == person_id
    ).order_by(GpsLog.created_at.desc())

    latest_log = (await db.execute(stmt)).scalars().first()

    if not latest_log:
        raise HTTPException(status_code=404, detail="위치 데이터를 찾을 수 없습니다.")
    
    latest_location = {
        "latitude": latest_log.latitude,
        "longitude": latest_log.longitude,
        "is_fall": latest_log.is_fall_detected, # AI 통신 결과값 반영
        "is_wandering": latest_log.is_wandering_detected
    }

    return latest_location

@person_router.get("/{person_id}/history", response_model=LocationHistoryResponse)
async def get_person_location_history(
    person_id: int,
    from_time: datetime = Query(..., alias="from", description="조회 시작 시간"),
    to_time: datetime = Query(..., alias="to", description="조회 종료 시간"),
    current_guardian: Guardian = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """지도 경로선 표현용 GPS 시계열 히스토리 데이터 조회"""
    # 1. 소유권 검증
    await check_guardian_ownership(person_id, current_guardian.id, db)
    
    # 2. DB에서 기간 내 위치 로그 조회
    stmt = select(GpsLog.latitude, GpsLog.longitude).where(
        GpsLog.person_id == person_id,
        GpsLog.created_at >= from_time,
        GpsLog.created_at <= to_time
    )

    # 위치 데이터 리스트
    history_data = (await db.execute(stmt)).all()

    if not history_data:
        return {"history": []}
    
    formatted_history = [
        LocationAbstractResponse(
            latitude=item[0],
            longitude=item[1]
        )
        for item in history_data
    ]

    return {"history": formatted_history}

@person_router.get("/{zoneId}/zones", response_model=ZoneData)
async def get_person_zones(zoneId: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TrackedPerson.base_lat, TrackedPerson.base_lng, TrackedPerson.safe_radius).where(TrackedPerson.id == zoneId))
    row = result.first()
    if row:
        return ZoneData(latitude=row.base_lat, longitude=row.base_lng, safe_radius=row.safe_radius)
    else:
        return None

@person_router.patch("/{zoneId}/zones", response_model=ZoneData, tags=["Global Zones Management"])
async def update_zone(zoneId: int, payload: ZoneData, db: AsyncSession = Depends(get_db)):
    # ZoneData 필드(latitude/longitude)를 DB 컬럼(base_lat/base_lng)으로 매핑
    values = payload.model_dump(exclude_unset=True)
    column_map = {"latitude": "base_lat", "longitude": "base_lng", "safe_radius": "safe_radius"}
    column_values = {column_map[k]: v for k, v in values.items() if k in column_map}

    await db.execute(update(TrackedPerson).where(TrackedPerson.id == zoneId).values(**column_values))
    await db.commit()
    result = await db.execute(select(TrackedPerson.base_lat, TrackedPerson.base_lng, TrackedPerson.safe_radius).where(TrackedPerson.id == zoneId))
    row = result.first()
    if row:
        return ZoneData(latitude=row.base_lat, longitude=row.base_lng, safe_radius=row.safe_radius)
    else:
        return None