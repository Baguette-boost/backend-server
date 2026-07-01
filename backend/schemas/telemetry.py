from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

# --- 1. GPS 관련 스키마 ---
class GPSPoint(BaseModel):
    timestamp: datetime
    latitude: float
    longitude: float

class GPSRequest(BaseModel):
    personId: int
    gps: GPSPoint

# TODO: GPSCommon과 GPSRequest의 형태로 수정

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

# --- 3. AI 컨테이너 전송용 스키마 ---
class AIPredictRequest(BaseModel):
    personId: int
    timestamp: str
    imuData: IMUData
    gpsData: List[GPSPoint]