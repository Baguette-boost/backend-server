from pydantic import BaseModel, Field
from typing import List, Optional
from decimal import Decimal

from backend.utils.time import IncomingUtcDatetime

# --- 1. GPS 관련 스키마 ---
class GPSPoint(BaseModel):
    # 하드웨어가 UTC 로 보낸다고 가정하되, 오프셋이 붙어 와도 방어적으로 UTC(naive)로 정규화
    timestamp: IncomingUtcDatetime
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
    # 낙상 분류기 입력 피처: roll/pitch/yaw + 가속도(ax..az) + 자이로(wx..wz)
    roll: List[float] = Field(default_factory=list)
    pitch: List[float] = Field(default_factory=list)
    yaw: List[float] = Field(default_factory=list)
    ax: List[float] = Field(default_factory=list)
    ay: List[float] = Field(default_factory=list)
    az: List[float] = Field(default_factory=list)
    wx: List[float] = Field(default_factory=list)
    wy: List[float] = Field(default_factory=list)
    wz: List[float] = Field(default_factory=list)

class FallSuspectRequest(BaseModel):
    personId: int
    timestamp: IncomingUtcDatetime
    imuData: IMUData

# AI 컨테이너 전송용 스키마(AIPredictRequest)는 backend/schemas/ai.py로 일원화되었습니다.