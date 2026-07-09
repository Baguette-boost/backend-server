from pydantic import BaseModel, Field, BeforeValidator, PlainSerializer
from typing import Annotated, List, Optional
from datetime import datetime

from backend.utils.time import to_naive_utc, isoformat_utc, utcnow

# AI 로 보낼 시간: 입력을 UTC(naive)로 정규화하고, JSON 직렬화 시 'Z' 표기로 내보낸다.
AiUtcDatetime = Annotated[
    datetime,
    BeforeValidator(to_naive_utc),
    PlainSerializer(isoformat_utc, return_type=str, when_used="json"),
]

class IMUData(BaseModel):
    # 낙상 분류기 입력 피처: roll/pitch/yaw + 가속도(ax..az) + 자이로(wx..wz).
    # (accel_norm/gyro_norm/dt_s 파생 피처는 AI 서버가 계산한다)
    roll: List[float]
    pitch: List[float]
    yaw: List[float]
    ax: List[float]
    ay: List[float]
    az: List[float]
    wx: List[float]
    wy: List[float]
    wz: List[float]

class GPSPoint(BaseModel):
    timestamp: AiUtcDatetime
    latitude: float
    longitude: float

class AIPredictRequest(BaseModel):
    personId: int
    timestamp: AiUtcDatetime = Field(default_factory=utcnow)
    imuData: Optional[IMUData] = None
    gpsData: Optional[List[GPSPoint]] = None

class DetectionResult(BaseModel):
    is_triggered: bool
    probability: float

class AIPredictResponse(BaseModel):
    personId: int
    fall_detection: Optional[DetectionResult] = None
    wandering_detection: Optional[DetectionResult] = None
