from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class IMUData(BaseModel):
    ax: List[float]
    ay: List[float]
    az: List[float]
    wx: List[float]
    wy: List[float]
    wz: List[float]

class GPSPoint(BaseModel):
    timestamp: datetime
    latitude: float
    longitude: float

class AIPredictRequest(BaseModel):
    personId: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    imuData: Optional[IMUData] = None
    gpsData: Optional[List[GPSPoint]] = None

class DetectionResult(BaseModel):
    is_triggered: bool
    probability: float

class AIPredictResponse(BaseModel):
    personId: int
    fall_detection: Optional[DetectionResult] = None
    wandering_detection: Optional[DetectionResult] = None
