from pydantic import BaseModel, Field, ConfigDict, model_serializer
from typing import List, Optional
from datetime import datetime
from decimal import Decimal

class PersonCreate(BaseModel):
    name: str = Field(..., description="환자 이름")
    age: int = Field(..., description="환자 연령")
    device_token: str = Field(..., description="페어링할 기기의 MAC 주소")
    base_lat: Decimal
    base_lng: Decimal
    safe_radius: int

class PersonUpdate(BaseModel):
    name: Optional[str] = None
    age: Optional[int] = None
    base_lat: Optional[Decimal] = None
    base_lng: Optional[Decimal] = None
    safe_radius: Optional[int] = None

class PersonResponse(BaseModel):
    id: int
    name: str
    age: int
    device_id: str = Field(..., alias="deviceId", description="디바이스 id")
    device_token: str = Field(..., alias="deviceToken", description="디바이스 식별 토큰")
    created_at: datetime = Field(..., alias="createdAt")

    # 해당 pydantic 모델의 동작 방식을 제어하는 규칙 설정
    model_config = ConfigDict(
        populate_by_name = True, # 필드 이름과 별칭 모두 허용
        from_attributes = True  # orm 객체 등을 pydantic 모델로 자동 매핑
    )

class DeviceVerifyRequest(BaseModel):
    device_token: str

class ZoneData(BaseModel):
    latitude: Decimal
    longitude: Decimal
    safe_radius: int

class ZoneResponse(ZoneData):
    person_id: int
    
    model_config = ConfigDict(
        from_attributes = True
    )


class LocationAbstractResponse(BaseModel):
    latitude: float
    longitude: float
    # updated_at: datetime = Field(..., alias="updatedAt")

    # model_config = ConfigDict(
    #     populate_by_name = True,
    #     from_attributes = True
    # )

class LocationResponse(LocationAbstractResponse):
    is_fall: bool
    is_wandering: bool
    
class LocationHistoryResponse(BaseModel):
    history: List[LocationAbstractResponse]