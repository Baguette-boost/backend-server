from pydantic import BaseModel, Field, model_validator
from typing import List, Optional
from decimal import Decimal

# IMU 9채널 이름
_IMU_CHANNELS = ("roll", "pitch", "yaw", "ax", "ay", "az", "wx", "wy", "wz")

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

    @model_validator(mode="after")
    def _channels_same_length(self):
        # 9채널 리스트 길이가 모두 같아야 한다(불일치·누락 채널 → 422로 거른다)
        lengths = {ch: len(getattr(self, ch)) for ch in _IMU_CHANNELS}
        if len(set(lengths.values())) != 1:
            raise ValueError(f"IMU 9채널 리스트 길이가 모두 같아야 합니다: {lengths}")
        return self

class FallSuspectRequest(BaseModel):
    personId: int
    timestamp: IncomingUtcDatetime
    imuData: IMUData

# AI 컨테이너 전송용 스키마(AIPredictRequest)는 backend/schemas/ai.py로 일원화되었습니다.