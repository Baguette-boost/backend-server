from pydantic import BaseModel, Field, ConfigDict, model_serializer
from typing import List, Optional
from datetime import datetime

from backend.utils.time import OutgoingUtcDatetime

class PersonCreate(BaseModel):
    name: str = Field(..., description="환자 이름")
    age: int = Field(..., description="환자 연령")
    device_token: str = Field(..., description="페어링할 기기의 MAC 주소")

class PersonUpdate(BaseModel):
    name: Optional[str] = None
    age: Optional[int] = None

class PersonResponse(BaseModel):
    id: int
    name: str
    age: int
    device_id: str = Field(..., alias="deviceId", description="디바이스 id")
    device_token: str = Field(..., alias="deviceToken", description="디바이스 식별 토큰")
    created_at: OutgoingUtcDatetime = Field(..., alias="createdAt")

    # 해당 pydantic 모델의 동작 방식을 제어하는 규칙 설정
    model_config = ConfigDict(
        populate_by_name = True, # 필드 이름과 별칭 모두 허용
        from_attributes = True  # orm 객체 등을 pydantic 모델로 자동 매핑
    )

class DeviceVerifyRequest(BaseModel):
    device_token: str


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

# 지도 경로선 위의 지점별 이벤트(낙상/배회) 표시용
class LocationHistoryPoint(LocationAbstractResponse):
    is_fall_detected: bool = False
    is_wandering_detected: bool = False

class LocationHistoryResponse(BaseModel):
    history: List[LocationHistoryPoint]