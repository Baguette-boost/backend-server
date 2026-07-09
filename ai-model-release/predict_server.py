"""AI 추론 HTTP 서버 (Pull 파이프라인, B).

백엔드(ai_client.predict)가 원본 IMU/GPS 를 담아 POST /predict 로 호출하면,
컨테이너 안에서 모델 추론을 수행해 낙상/배회 판정을 돌려준다.

- 낙상: iccas_specialized_binary_lstm 계열 IMU 분류기 (BinaryLSTM, sigmoid→threshold)
        피처엔지니어링은 학습과 동일하게 realtime_sensor_lstm.build_features 를 재사용한다.
- 배회: 모델 미확정 → 현재는 stub(비트리거). 파이프라인만 구축하고 모델은 추후 연결.

계약(스키마)은 backend/schemas/ai.py 의 AIPredictRequest / AIPredictResponse 와 일치한다.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
import torch
from torch import nn

from fastapi import FastAPI
from pydantic import BaseModel

# --- realtime_sensor_lstm 의 피처엔지니어링을 그대로 재사용 (학습·추론 피처 동일성 보장) ---
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from realtime_sensor_lstm import build_features, IMU_COLUMNS  # noqa: E402

logger = logging.getLogger("predict_server")
logging.basicConfig(level=logging.INFO)

# 낙상 모델 경로 (Stage 1: 로더가 지원하는 specialized binary 모델로 검증)
FALL_MODEL_PATH = os.environ.get("FALL_MODEL_PATH", "models/iccas_final_lstm_imu_fall.pt")
# 배회 모델은 미확정 → 경로가 주어지면 로드, 없으면 stub
WANDER_MODEL_PATH = os.environ.get("WANDER_MODEL_PATH", "")
# 기기가 per-sample 타임스탬프를 보내지 않으므로 dt_s 계산용 고정 샘플 간격(초).
# 학습 dt_s 와의 미세한 skew 는 감수(파이프라인 검증 목적). 필요 시 계약에 per-sample time 추가.
DEFAULT_SAMPLE_INTERVAL_S = float(os.environ.get("SAMPLE_INTERVAL_S", "0.05"))


# --- train_specialized_sensor_lstm.py 에서 그대로 가져온 정의(추론에 필요한 최소) ---
class RobustScaler:
    def __init__(self, center: np.ndarray, scale: np.ndarray) -> None:
        self.center = center.astype(np.float32)
        self.scale = scale.astype(np.float32)

    def transform(self, values: np.ndarray) -> np.ndarray:
        scaled = (values.astype(np.float32) - self.center) / self.scale
        return np.clip(scaled, -12.0, 12.0).astype(np.float32)


class BinaryLSTM(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, num_layers: int, dropout: float) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.head = nn.Sequential(nn.LayerNorm(hidden_size), nn.Linear(hidden_size, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        output, _ = self.lstm(x)
        return self.head(output[:, -1, :]).squeeze(-1)


class BinaryPredictor:
    """specialized binary LSTM(.pt) 하나를 로드해 시퀀스 확률을 내는 추론기."""

    def __init__(self, model_path: str) -> None:
        ckpt = torch.load(model_path, map_location="cpu", weights_only=False)
        if ckpt.get("model_type") != "iccas_specialized_binary_lstm":
            raise ValueError(f"지원하지 않는 model_type: {ckpt.get('model_type')} ({model_path})")
        self.feature_columns: List[str] = list(ckpt["feature_columns"])
        self.sequence_length = int(ckpt["sequence_length"])
        self.threshold = float(ckpt["threshold"])
        self.task = str(ckpt.get("task", "unknown"))
        self.scaler = RobustScaler(
            np.asarray(ckpt["scaler_center"], dtype=np.float32),
            np.asarray(ckpt["scaler_scale"], dtype=np.float32),
        )
        self.model = BinaryLSTM(
            input_size=len(self.feature_columns),
            hidden_size=int(ckpt["hidden_size"]),
            num_layers=int(ckpt["num_layers"]),
            dropout=float(ckpt["dropout"]),
        )
        self.model.load_state_dict(ckpt["model_state"])
        self.model.eval()
        logger.info(
            "loaded %s task=%s seq=%d thr=%.3f feat=%d",
            model_path, self.task, self.sequence_length, self.threshold, len(self.feature_columns),
        )

    def _score(self, featured: pd.DataFrame) -> Optional[float]:
        """featured DataFrame 의 마지막 sequence_length 윈도우로 sigmoid 확률을 낸다."""
        values = featured[self.feature_columns].to_numpy(dtype=np.float32)
        if len(values) < self.sequence_length:
            return None
        window = self.scaler.transform(values[-self.sequence_length:])
        tensor = torch.from_numpy(window[None, :, :]).to(dtype=torch.float32)
        with torch.no_grad():
            logit = self.model(tensor)
        return float(torch.sigmoid(logit).item())


def _imu_to_frame(imu: "ImuData", n: int) -> pd.DataFrame:
    """IMU 리스트를 build_features 가 먹는 DataFrame 으로 재구성.

    per-sample 시간이 없으므로 고정 간격으로 server_time 을 합성한다(dt_s 용).
    낙상 모델 피처엔 GPS 파생이 없으므로 lat/lng/gps_valid 는 더미(0)로 채운다.
    """
    base = pd.Timestamp("2000-01-01")
    server_time = base + pd.to_timedelta(np.arange(n) * DEFAULT_SAMPLE_INTERVAL_S, unit="s")
    data = {
        "device": ["0"] * n,
        "server_time": server_time,
        "roll": imu.roll,
        "pitch": imu.pitch,
        "yaw": imu.yaw,
        "ax": imu.ax,
        "ay": imu.ay,
        "az": imu.az,
        "wx": imu.wx,
        "wy": imu.wy,
        "wz": imu.wz,
        "lat": [0.0] * n,
        "lng": [0.0] * n,
        "gps_valid": [0.0] * n,
    }
    return pd.DataFrame(data)


# --- 요청/응답 스키마 (backend/schemas/ai.py 와 일치) ---
class ImuData(BaseModel):
    roll: List[float] = []
    pitch: List[float] = []
    yaw: List[float] = []
    ax: List[float] = []
    ay: List[float] = []
    az: List[float] = []
    wx: List[float] = []
    wy: List[float] = []
    wz: List[float] = []


class GpsPoint(BaseModel):
    timestamp: Optional[str] = None
    latitude: float
    longitude: float


class PredictRequest(BaseModel):
    personId: int
    timestamp: Optional[str] = None
    imuData: Optional[ImuData] = None
    gpsData: Optional[List[GpsPoint]] = None


class DetectionResult(BaseModel):
    is_triggered: bool
    probability: float


class PredictResponse(BaseModel):
    personId: int
    fall_detection: DetectionResult
    wandering_detection: DetectionResult


app = FastAPI(title="ICCAS AI Predict Server")
fall_predictor: Optional[BinaryPredictor] = None
wander_predictor: Optional[BinaryPredictor] = None


@app.on_event("startup")
def _load_models() -> None:
    global fall_predictor, wander_predictor
    fall_predictor = BinaryPredictor(FALL_MODEL_PATH)
    if WANDER_MODEL_PATH:
        wander_predictor = BinaryPredictor(WANDER_MODEL_PATH)
    else:
        logger.info("배회 모델 미설정 → stub(비트리거)로 동작")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "fall_model": bool(fall_predictor), "wander_model": bool(wander_predictor)}


def _detect_fall(req: PredictRequest) -> DetectionResult:
    if fall_predictor is None or req.imuData is None:
        return DetectionResult(is_triggered=False, probability=0.0)
    n = len(req.imuData.ax)
    # 회전반경 시퀀스 최소 길이 미만이면 판단 불가 → 비트리거
    if n < fall_predictor.sequence_length:
        logger.info("IMU 표본 부족 personId=%s n=%d (<%d)", req.personId, n, fall_predictor.sequence_length)
        return DetectionResult(is_triggered=False, probability=0.0)
    try:
        frame = _imu_to_frame(req.imuData, n)
        featured, _, _ = build_features(frame, origin_lat=0.0, origin_lng=0.0)
        prob = fall_predictor._score(featured)
    except Exception as exc:  # noqa: BLE001
        logger.exception("낙상 추론 실패 personId=%s: %s", req.personId, exc)
        return DetectionResult(is_triggered=False, probability=0.0)
    if prob is None:
        return DetectionResult(is_triggered=False, probability=0.0)
    return DetectionResult(is_triggered=prob >= fall_predictor.threshold, probability=round(prob, 4))


def _detect_wandering(req: PredictRequest) -> DetectionResult:
    # 배회 모델 미확정 → stub. (WANDER_MODEL_PATH 설정 시 낙상과 동일 경로로 확장 예정)
    return DetectionResult(is_triggered=False, probability=0.0)


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest) -> PredictResponse:
    return PredictResponse(
        personId=req.personId,
        fall_detection=_detect_fall(req),
        wandering_detection=_detect_wandering(req),
    )
