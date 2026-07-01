from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List
from datetime import datetime
import uuid

# 내부 모듈 임포트 (경로는 프로젝트 환경에 맞게 수정)
from app.schemas.person import PersonCreate, PersonResponse, LocationAbstractResponse, LocationResponse, LocationHistoryResponse
from app.core.security import get_current_user

router = APIRouter(prefix="/persons", tags=["Persons"])

# 헬퍼 함수: 권한 검증 (실제 DB 쿼리로 대체 필요)
async def check_guardian_ownership(person_id: int, guardian_id: int):
    # Mock DB Fetch: db.query(Person).filter(Person.id == person_id).first()
    mock_person_db = {1: {"guardian_id": 101}, 2: {"guardian_id": 102}} 
    
    person = mock_person_db.get(person_id)
    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="환자를 찾을 수 없습니다.")
    
    if person["guardian_id"] != guardian_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="해당 환자 정보에 접근할 권한이 없습니다."
        )
    return person


@router.post("", response_model=PersonResponse, status_code=status.HTTP_201_CREATED)
async def register_person(
    payload: PersonCreate,
    current_guardian: dict = Depends(get_current_user)
):
    """환자 등록 및 디바이스 페어링"""
    # 1. 고유 deviceToken 발급 (uuid 등 활용)
    new_device_token = str(uuid.uuid4())
    
    # 2. DB 저장 로직 (Guardian ID와 함께 저장)
    new_person = {
        "id": 1, # DB Auto Increment
        "name": payload.name,
        "age": payload.age,
        "device_token": new_device_token,
        "created_at": datetime.utcnow()
    }
    return new_person

@router.get("", response_model=List[PersonResponse])
async def get_persons(current_guardian: dict = Depends(get_current_user)):
    """보호자가 관리하는 환자 목록 조회"""
    # DB 조회: SELECT * FROM persons WHERE guardian_id = current_guardian['id']
    return []


@router.get("/{person_id}/location", response_model=LocationResponse)
async def get_person_location(
    person_id: int,
    current_guardian: dict = Depends(get_current_user)
):
    """최신 위경도, 배터리, AI 최종 낙상 확정 여부 반환"""
    # 1. 소유권 검증 (중요)
    await check_guardian_ownership(person_id, current_guardian["id"])
    
    # 2. 캐시(Redis) 또는 DB에서 최신 위치 데이터 조회
    latest_location = {
        "latitude": 37.5665,
        "longitude": 126.9780,
        "battery": 85,
        "is_fall_confirmed": False, # AI 통신 결과값 반영
        "updated_at": datetime.utcnow()
    }
    return latest_location

@router.get("/{person_id}/history", response_model=LocationHistoryResponse)
async def get_person_location_history(
    person_id: int,
    from_time: datetime = Query(..., alias="from", description="조회 시작 시간"),
    to_time: datetime = Query(..., alias="to", description="조회 종료 시간"),
    current_guardian: dict = Depends(get_current_user)
):
    """지도 경로선 표현용 GPS 시계열 히스토리 데이터 조회"""
    # 1. 소유권 검증
    await check_guardian_ownership(person_id, current_guardian["id"])
    
    # 2. DB에서 기간 내 위치 로그 조회
    history_data = [
        # ... 위치 데이터 리스트
    ]
    return {"history": history_data}