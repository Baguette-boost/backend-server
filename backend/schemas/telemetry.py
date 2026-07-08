from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from decimal import Decimal

# --- 1. GPS 관련 스키마 ---
class GPSPoint(BaseModel):
    timestamp: datetime
    latitude: Decimal
    longitude: Decimal

class GPSData(GPSPoint):
    is_fall_detected: bool = Field(default=False)
    is_wandering_detected: bool = Field(default=False)

class GPSRequest(BaseModel):
    personId: int
    gps: GPSData

# --- 2. 낙상 의심 (IMU) 관련 스키마 ---
class IMUData(BaseModel):
    ax: List[float] = Field(default_factory=list)
    ay: List[float] = Field(default_factory=list)
    az: List[float] = Field(default_factory=list)
    wx: List[float] = Field(default_factory=list)
    wy: List[float] = Field(default_factory=list)
    wz: List[float] = Field(default_factory=list)

class FallSuspectRequest(BaseModel):
    personId: int
    timestamp: datetime
    imuData: IMUData

# AI 컨테이너 전송용 스키마(AIPredictRequest)는 backend/schemas/ai.py로 일원화되었습니다.