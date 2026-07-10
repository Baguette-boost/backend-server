"""AI 추론 HTTP 서버 (Pull 파이프라인, B).

백엔드(ai_client.predict)가 원본 IMU/GPS 를 담아 POST /predict 로 호출하면,
컨테이너 안에서 모델 추론을 수행해 낙상/배회 판정을 돌려준다.

- 낙상: TCN(v3_binary_imu_fall_tcn) IMU 분류기. 모델 클래스·전처리는 팀원의
        final-tcn-server/app.py 에서 포팅. (구 specialized_binary_lstm 도 계속 로드 가능)
- 배회: specialized_binary_lstm(GPS) 분류기. build_features 재사용.

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

# --- realtime_sensor_lstm 의 피처엔지니어링을 그대로 재사용 (배회 GPS 피처용) ---
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from realtime_sensor_lstm import build_features, IMU_COLUMNS  # noqa: E402

logger = logging.getLogger("predict_server")
logging.basicConfig(level=logging.INFO)

# 낙상 모델(TCN) / 배회 모델(GPS LSTM) 경로. 컨테이너별로 자기 역할만 설정.
FALL_MODEL_PATH = os.environ.get("FALL_MODEL_PATH", "models/v3_tcn_imu_fall.pt")
WANDER_MODEL_PATH = os.environ.get("WANDER_MODEL_PATH", "")
# 배회(LSTM) 프레임의 dt_s 합성용 고정 샘플 간격(초).
DEFAULT_SAMPLE_INTERVAL_S = float(os.environ.get("SAMPLE_INTERVAL_S", "0.05"))


class RobustScaler:
    def __init__(self, center: np.ndarray, scale: np.ndarray) -> None:
        self.center = center.astype(np.float32)
        self.scale = scale.astype(np.float32)

    def transform(self, values: np.ndarray) -> np.ndarray:
        scaled = (values.astype(np.float32) - self.center) / self.scale
        return np.clip(scaled, -12.0, 12.0).astype(np.float32)


# ─────────────────────────────────────────────────────────────
# 배회: specialized binary LSTM
# ─────────────────────────────────────────────────────────────
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
    """specialized binary LSTM(.pt) 하나를 로드해 시퀀스 확률을 내는 추론기 (배회용)."""

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
        logger.info("loaded LSTM %s task=%s seq=%d thr=%.3f", model_path, self.task, self.sequence_length, self.threshold)

    def _score(self, featured: pd.DataFrame) -> Optional[float]:
        values = featured[self.feature_columns].to_numpy(dtype=np.float32)
        if len(values) < self.sequence_length:
            return None
        window = self.scaler.transform(values[-self.sequence_length:])
        tensor = torch.from_numpy(window[None, :, :]).to(dtype=torch.float32)
        with torch.no_grad():
            logit = self.model(tensor)
        return float(torch.sigmoid(logit).item())


# ─────────────────────────────────────────────────────────────
# 낙상: TCN (팀원 final-tcn-server/app.py 에서 포팅)
# ─────────────────────────────────────────────────────────────
class Chomp1d(nn.Module):
    def __init__(self, chomp_size: int) -> None:
        super().__init__()
        self.chomp_size = chomp_size

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.chomp_size == 0:
            return x
        return x[:, :, : -self.chomp_size].contiguous()


class TemporalBlock(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, kernel_size: int, dilation: int, dropout: float) -> None:
        super().__init__()
        padding = (kernel_size - 1) * dilation
        self.network = nn.Sequential(
            nn.Conv1d(in_channels, out_channels, kernel_size, padding=padding, dilation=dilation),
            Chomp1d(padding),
            nn.BatchNorm1d(out_channels),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Conv1d(out_channels, out_channels, kernel_size, padding=padding, dilation=dilation),
            Chomp1d(padding),
            nn.BatchNorm1d(out_channels),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        self.downsample = nn.Conv1d(in_channels, out_channels, 1) if in_channels != out_channels else nn.Identity()
        self.activation = nn.ReLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.activation(self.network(x) + self.downsample(x))


class TCNFallModel(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, num_layers: int, kernel_size: int, dropout: float) -> None:
        super().__init__()
        blocks = []
        in_channels = input_size
        for index in range(num_layers):
            blocks.append(TemporalBlock(in_channels, hidden_size, kernel_size, dilation=2**index, dropout=dropout))
            in_channels = hidden_size
        self.encoder = nn.Sequential(*blocks)
        self.head = nn.Sequential(nn.LayerNorm(hidden_size), nn.Dropout(dropout), nn.Linear(hidden_size, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        encoded = self.encoder(x.transpose(1, 2)).transpose(1, 2)
        pooled = encoded.mean(dim=1)
        return self.head(pooled).squeeze(-1)


class TcnFallPredictor:
    """TCN IMU 낙상 추론기. IMU 리스트 → 12피처(accel_norm/gyro_norm/dt_s 포함) → sigmoid."""

    def __init__(self, model_path: str) -> None:
        ckpt = torch.load(model_path, map_location="cpu", weights_only=False)
        self.feature_columns: List[str] = list(ckpt["feature_columns"])
        self.sequence_length = int(ckpt["sequence_length"])
        self.sample_ms = int(ckpt.get("sample_ms", 40))
        self.threshold = float(ckpt["threshold"])
        self.scaler = RobustScaler(
            np.asarray(ckpt["scaler_center"], dtype=np.float32),
            np.asarray(ckpt["scaler_scale"], dtype=np.float32),
        )
        self.model = TCNFallModel(
            len(self.feature_columns),
            int(ckpt["hidden_size"]),
            int(ckpt["num_layers"]),
            int(ckpt["kernel_size"]),
            float(ckpt["dropout"]),
        )
        self.model.load_state_dict(ckpt["model_state"])
        self.model.eval()
        logger.info("loaded TCN %s seq=%d thr=%.3f feat=%d", model_path, self.sequence_length, self.threshold, len(self.feature_columns))

    def _features(self, imu: "ImuData", n: int) -> np.ndarray:
        # app.py sample_to_features 와 동일: per-sample 시간이 없으므로 dt_s 는 첫점 0, 이후 sample_ms/1000.
        dt = self.sample_ms / 1000.0
        rows: list[list[float]] = []
        for i in range(n):
            ax, ay, az = float(imu.ax[i]), float(imu.ay[i]), float(imu.az[i])
            wx, wy, wz = float(imu.wx[i]), float(imu.wy[i]), float(imu.wz[i])
            row = {
                "roll": float(imu.roll[i]), "pitch": float(imu.pitch[i]), "yaw": float(imu.yaw[i]),
                "ax": ax, "ay": ay, "az": az, "wx": wx, "wy": wy, "wz": wz,
                "accel_norm": float(np.sqrt(ax * ax + ay * ay + az * az)),
                "gyro_norm": float(np.sqrt(wx * wx + wy * wy + wz * wz)),
                "dt_s": 0.0 if i == 0 else dt,
            }
            rows.append([row[c] for c in self.feature_columns])
        return np.asarray(rows, dtype=np.float32)

    def predict_fall(self, imu: "ImuData") -> Optional[float]:
        n = len(imu.ax)
        if n < self.sequence_length:
            return None
        feats = self._features(imu, n)[-self.sequence_length:]
        window = self.scaler.transform(feats)
        with torch.no_grad():
            logit = self.model(torch.from_numpy(window[None, :, :]).to(dtype=torch.float32))
        return float(torch.sigmoid(logit).item())


class LstmImuFallPredictor:
    """구 specialized_binary_lstm IMU 낙상 모델용 어댑터(하위호환). predict_fall 인터페이스 통일."""

    def __init__(self, model_path: str) -> None:
        self.inner = BinaryPredictor(model_path)
        self.sequence_length = self.inner.sequence_length
        self.threshold = self.inner.threshold

    def predict_fall(self, imu: "ImuData") -> Optional[float]:
        n = len(imu.ax)
        if n < self.sequence_length:
            return None
        frame = _imu_to_frame(imu, n)
        featured, _, _ = build_features(frame, origin_lat=0.0, origin_lng=0.0)
        return self.inner._score(featured)


def build_fall_predictor(model_path: str):
    """체크포인트 model_type 으로 낙상 추론기 선택 (tcn → TCN, 그 외 → LSTM 어댑터)."""
    model_type = str(torch.load(model_path, map_location="cpu", weights_only=False).get("model_type", ""))
    if "tcn" in model_type.lower():
        return TcnFallPredictor(model_path)
    return LstmImuFallPredictor(model_path)


def _imu_to_frame(imu: "ImuData", n: int) -> pd.DataFrame:
    """(LSTM 낙상 하위호환용) IMU 리스트를 build_features 가 먹는 DataFrame 으로 재구성."""
    base = pd.Timestamp("2000-01-01")
    server_time = base + pd.to_timedelta(np.arange(n) * DEFAULT_SAMPLE_INTERVAL_S, unit="s")
    data = {
        "device": ["0"] * n, "server_time": server_time,
        "roll": imu.roll, "pitch": imu.pitch, "yaw": imu.yaw,
        "ax": imu.ax, "ay": imu.ay, "az": imu.az, "wx": imu.wx, "wy": imu.wy, "wz": imu.wz,
        "lat": [0.0] * n, "lng": [0.0] * n, "gps_valid": [0.0] * n,
    }
    return pd.DataFrame(data)


def _gps_to_frame(points: list) -> pd.DataFrame:
    """GPS 궤적을 build_features 가 먹는 DataFrame 으로 재구성(배회 피처용).

    x_m/y_m 는 origin 기준 절대위치인데 체크포인트에 origin 이 없어 build_features(origin=None)로
    배치 GPS 중앙값을 원점으로 근사한다(이동 피처 dx/dy/speed/dt_s 는 원점 무관하게 정확).
    """
    n = len(points)
    times = [pd.to_datetime(p.timestamp, errors="coerce") if p.timestamp else pd.NaT for p in points]
    server_time = pd.Series(times)
    if server_time.isna().any():
        base = pd.Timestamp("2000-01-01")
        server_time = pd.Series(base + pd.to_timedelta(np.arange(n), unit="s"))
    data = {
        "device": ["0"] * n, "server_time": server_time,
        "lat": [float(p.latitude) for p in points], "lng": [float(p.longitude) for p in points],
        "gps_valid": [1.0] * n,
    }
    for c in ("roll", "pitch", "yaw", "ax", "ay", "az", "wx", "wy", "wz"):
        data[c] = [0.0] * n
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
fall_predictor = None
wander_predictor: Optional[BinaryPredictor] = None


@app.on_event("startup")
def _load_models() -> None:
    global fall_predictor, wander_predictor
    # 컨테이너별로 자기 역할 모델만 로드한다(2컨테이너 분리). 빈 경로면 그 역할은 비트리거.
    if FALL_MODEL_PATH:
        fall_predictor = build_fall_predictor(FALL_MODEL_PATH)
    else:
        logger.info("낙상 모델 미설정 → 낙상 비트리거")
    if WANDER_MODEL_PATH:
        wander_predictor = BinaryPredictor(WANDER_MODEL_PATH)
    else:
        logger.info("배회 모델 미설정 → 배회 비트리거")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "fall_model": type(fall_predictor).__name__ if fall_predictor else None,
        "wander_model": bool(wander_predictor),
    }


def _detect_fall(req: PredictRequest) -> DetectionResult:
    if fall_predictor is None or req.imuData is None:
        return DetectionResult(is_triggered=False, probability=0.0)
    n = len(req.imuData.ax)
    if n < fall_predictor.sequence_length:
        logger.info("IMU 표본 부족 personId=%s n=%d (<%d)", req.personId, n, fall_predictor.sequence_length)
        return DetectionResult(is_triggered=False, probability=0.0)
    try:
        prob = fall_predictor.predict_fall(req.imuData)
    except Exception as exc:  # noqa: BLE001
        logger.exception("낙상 추론 실패 personId=%s: %s", req.personId, exc)
        return DetectionResult(is_triggered=False, probability=0.0)
    if prob is None:
        return DetectionResult(is_triggered=False, probability=0.0)
    return DetectionResult(is_triggered=prob >= fall_predictor.threshold, probability=round(prob, 4))


def _detect_wandering(req: PredictRequest) -> DetectionResult:
    if wander_predictor is None or not req.gpsData:
        return DetectionResult(is_triggered=False, probability=0.0)
    pts = [p for p in req.gpsData if p.latitude is not None and p.longitude is not None]
    if len(pts) < wander_predictor.sequence_length:
        return DetectionResult(is_triggered=False, probability=0.0)
    try:
        frame = _gps_to_frame(pts)
        featured, _, _ = build_features(frame, origin_lat=None, origin_lng=None)
        prob = wander_predictor._score(featured)
    except Exception as exc:  # noqa: BLE001
        logger.exception("배회 추론 실패 personId=%s: %s", req.personId, exc)
        return DetectionResult(is_triggered=False, probability=0.0)
    if prob is None:
        return DetectionResult(is_triggered=False, probability=0.0)
    return DetectionResult(is_triggered=prob >= wander_predictor.threshold, probability=round(prob, 4))


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest) -> PredictResponse:
    return PredictResponse(
        personId=req.personId,
        fall_detection=_detect_fall(req),
        wandering_detection=_detect_wandering(req),
    )
