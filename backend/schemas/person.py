from pydantic import BaseModel, Field, ConfigDict, model_serializer
from typing import List, Optional
from datetime import datetime

class PersonCreate(BaseModel):
    name: str = Field(..., description="환자 이름")
    age: int = Field(..., description="환자 연령")
    device_mac: str = Field(..., description="페어링할 기기의 MAC 주소")

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

class LocationAbstractResponse(BaseModel):
    latitude: float
    longitude: float
    updated_at: datetime = Field(..., alias="updatedAt")

    model_config = ConfigDict(
        populate_by_name = True,
        from_attributes = True
    )

class LocationResponse(BaseModel):
    address: str
    zone_label: str = Field(..., alias="zoneLabel")
    in_safe_zone: bool = Field(..., alias="inSafeZone")
    is_fall_confirmed: bool = Field(..., alias="isFallConfirmed")

    abstract: LocationAbstractResponse

    @model_serializer(mode="wrap")
    def flatten_serializer(self, handler):
        # 기본 직렬화 수행 (alias 적용됨)
        result = handler(self)
        
        # 'abstract' 내부 필드들을 상위로 꺼내고, 기존 'abstract' 키는 삭제
        abstract_data = result.pop("abstract", {})
        result.update(abstract_data)
        
        return result

    model_config = ConfigDict(
        populate_by_name = True,
        from_attributes = True
    )
    
class LocationHistoryResponse(BaseModel):
    history: List[LocationAbstractResponse]