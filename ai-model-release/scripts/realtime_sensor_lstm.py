#!/usr/bin/env python3
"""Real-time LSTM stream model for ICCAS GPS + gyro sensor data.

The script is designed for the current assignment prototype and the later
service shape:

1. train  : learn a normal sensor/GPS sequence model from ICCAS_total_data.xlsx
2. replay : replay a file as a real-time stream and optionally POST each result
3. live   : read one JSON sensor point per line from stdin and POST results

Expected input columns:
server_time, device, label, t_ms, roll, pitch, yaw, ax, ay, az, wx, wy, wz, lat, lng
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset


EARTH_RADIUS_M = 6_371_008.8
RAW_SENSOR_COLUMNS = [
    "roll",
    "pitch",
    "yaw",
    "ax",
    "ay",
    "az",
    "wx",
    "wy",
    "wz",
    "lat",
    "lng",
]
IMU_COLUMNS = RAW_SENSOR_COLUMNS[:9]
FEATURE_COLUMNS = [
    "roll",
    "pitch",
    "yaw",
    "ax",
    "ay",
    "az",
    "wx",
    "wy",
    "wz",
    "x_m",
    "y_m",
    "dx_m",
    "dy_m",
    "speed_mps",
    "accel_norm",
    "gyro_norm",
    "dt_s",
    "gps_valid",
]


@dataclass
class StreamResult:
    timestamp: str
    device: str
    label: str | None
    lat: float | None
    lng: float | None
    gps_valid: bool
    ready: bool
    predicted_abnormal: bool
    wandering_detected: bool
    fall_detected: bool
    alarm_active: bool
    anomaly_score: float | None
    prediction_error: float | None
    position_error_m: float | None
    route_distance_m: float | None
    activity_hint: str | None
    detection_type: str
    risk_level: str
    event: str


class RobustScaler:
    def __init__(self, center: np.ndarray, scale: np.ndarray) -> None:
        self.center = center.astype(np.float32)
        self.scale = scale.astype(np.float32)

    @classmethod
    def fit(cls, values: np.ndarray) -> "RobustScaler":
        center = np.nanmedian(values, axis=0)
        q25 = np.nanpercentile(values, 25, axis=0)
        q75 = np.nanpercentile(values, 75, axis=0)
        scale = q75 - q25
        std = np.nanstd(values, axis=0)
        scale = np.where(scale > 1e-6, scale, std)
        scale = np.where(scale > 1e-6, scale, 1.0)
        return cls(center.astype(np.float32), scale.astype(np.float32))

    def transform(self, values: np.ndarray) -> np.ndarray:
        scaled = (values.astype(np.float32) - self.center) / self.scale
        return np.clip(scaled, -12.0, 12.0).astype(np.float32)

    def inverse_transform(self, values: np.ndarray) -> np.ndarray:
        return values.astype(np.float32) * self.scale + self.center


class SequenceDataset(Dataset):
    def __init__(self, values: np.ndarray, sequence_length: int) -> None:
        self.values = values.astype(np.float32)
        self.sequence_length = sequence_length
        self.count = max(0, len(values) - sequence_length)

    def __len__(self) -> int:
        return self.count

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        start = index
        stop = start + self.sequence_length
        return (
            torch.from_numpy(self.values[start:stop]),
            torch.from_numpy(self.values[stop]),
        )


class SensorLSTM(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, num_layers: int, dropout: float) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.head = nn.Sequential(
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, input_size),
        )

    def forward(self, sequence: torch.Tensor) -> torch.Tensor:
        output, _ = self.lstm(sequence)
        return self.head(output[:, -1, :])


def choose_device(name: str) -> torch.device:
    if name != "auto":
        return torch.device(name)
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def xlsx_rows(path: Path, sheet_name: str = "data") -> list[dict[str, Any]]:
    """Read a simple .xlsx sheet without openpyxl.

    This is intentionally small but enough for ICCAS_data.xlsx, whose sheet
    cells are plain shared strings and numeric values.
    """
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
    office_rel_ns = {
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }

    with ZipFile(path) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", ns):
                texts = [node.text or "" for node in item.findall(".//m:t", ns)]
                shared.append("".join(texts))

        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_targets = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in rels.findall("r:Relationship", rel_ns)
        }
        target = None
        for sheet in workbook.findall("m:sheets/m:sheet", ns):
            if sheet.attrib.get("name") == sheet_name:
                rel_id = sheet.attrib.get(
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                )
                target = rel_targets.get(rel_id or "")
                break
        if target is None:
            first = workbook.find("m:sheets/m:sheet", ns)
            if first is None:
                raise SystemExit(f"No sheets found in {path}")
            rel_id = first.attrib.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            target = rel_targets.get(rel_id or "")

        sheet_path = "xl/" + target.lstrip("/")
        root = ET.fromstring(zf.read(sheet_path))
        rows: list[list[Any]] = []
        for row in root.findall("m:sheetData/m:row", ns):
            values: list[Any] = []
            expected_col = 1
            for cell in row.findall("m:c", ns):
                ref = cell.attrib.get("r", "")
                col_letters = "".join(ch for ch in ref if ch.isalpha())
                col_index = column_number(col_letters)
                while expected_col < col_index:
                    values.append(None)
                    expected_col += 1
                value_node = cell.find("m:v", ns)
                if cell.attrib.get("t") == "inlineStr":
                    texts = [node.text or "" for node in cell.findall(".//m:t", ns)]
                    values.append("".join(texts))
                elif value_node is None:
                    values.append(None)
                elif cell.attrib.get("t") == "s":
                    values.append(shared[int(value_node.text or 0)])
                else:
                    raw = value_node.text or ""
                    try:
                        number = float(raw)
                        values.append(int(number) if number.is_integer() else number)
                    except ValueError:
                        values.append(raw)
                expected_col += 1
            rows.append(values)

    if not rows:
        return []
    headers = [str(value).strip() for value in rows[0]]
    return [
        {headers[index]: row[index] if index < len(row) else None for index in range(len(headers))}
        for row in rows[1:]
        if any(value is not None for value in row)
    ]


def column_number(letters: str) -> int:
    value = 0
    for char in letters:
        value = value * 26 + ord(char.upper()) - ord("A") + 1
    return value


def read_source(path: Path, sheet_name: str = "data") -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"Input file not found: {path}")
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
    elif path.suffix.lower() == ".xlsx":
        frame = pd.DataFrame(xlsx_rows(path, sheet_name=sheet_name))
    else:
        raise SystemExit("Supported input formats are .xlsx and .csv")

    aliases = {"longitude": "lng", "latitude": "lat", "timestamp": "server_time"}
    frame = frame.rename(columns={key: value for key, value in aliases.items() if key in frame.columns})
    required = {"server_time", "device", *IMU_COLUMNS}
    missing = required - set(frame.columns)
    if missing:
        raise SystemExit(f"Missing required columns: {sorted(missing)}")

    frame = frame.copy()
    frame["server_time"] = pd.to_datetime(frame["server_time"], errors="coerce")
    if "label" not in frame.columns:
        frame["label"] = None
    for column in ["t_ms", *RAW_SENSOR_COLUMNS]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in ("lat", "lng"):
        if column not in frame.columns:
            frame[column] = np.nan
    frame["device"] = frame["device"].fillna("unknown").astype(str)
    frame = frame.dropna(subset=["server_time", *IMU_COLUMNS])
    gps_valid = frame["lat"].between(-90, 90) & frame["lng"].between(-180, 180)
    frame["gps_valid"] = gps_valid.astype(float)
    frame.loc[~gps_valid, ["lat", "lng"]] = np.nan
    frame = frame.sort_values(["device", "server_time"]).reset_index(drop=True)
    return frame


def latlon_to_xy(lat: np.ndarray, lng: np.ndarray, origin_lat: float, origin_lng: float) -> tuple[np.ndarray, np.ndarray]:
    x = EARTH_RADIUS_M * np.radians(lng - origin_lng) * math.cos(math.radians(origin_lat))
    y = EARTH_RADIUS_M * np.radians(lat - origin_lat)
    return x, y


def build_features(frame: pd.DataFrame, origin_lat: float | None = None, origin_lng: float | None = None) -> tuple[pd.DataFrame, float, float]:
    if origin_lat is None:
        origin_lat = float(frame["lat"].dropna().median()) if frame["lat"].notna().any() else 0.0
    if origin_lng is None:
        origin_lng = float(frame["lng"].dropna().median()) if frame["lng"].notna().any() else 0.0
    parts: list[pd.DataFrame] = []
    for _, group in frame.groupby("device", sort=False):
        group = group.sort_values("server_time").copy()
        gps_valid = group["gps_valid"].fillna(0).to_numpy(dtype=np.float64)
        lat = group["lat"].ffill().bfill().fillna(origin_lat).to_numpy(dtype=np.float64)
        lng = group["lng"].ffill().bfill().fillna(origin_lng).to_numpy(dtype=np.float64)
        x, y = latlon_to_xy(
            lat,
            lng,
            origin_lat,
            origin_lng,
        )
        dt = group["server_time"].diff().dt.total_seconds().to_numpy(dtype=np.float64)
        if "t_ms" in group.columns and group["t_ms"].notna().sum() > 1:
            dt_from_sensor = group["t_ms"].diff().to_numpy(dtype=np.float64) / 1000.0
            dt = np.where(np.isfinite(dt_from_sensor) & (dt_from_sensor > 0), dt_from_sensor, dt)
        dt = np.clip(np.nan_to_num(dt, nan=1.0, posinf=1.0, neginf=1.0), 0.02, 30.0)
        dx = np.diff(x, prepend=x[0])
        dy = np.diff(y, prepend=y[0])
        speed = np.hypot(dx, dy) / dt
        accel_norm = np.sqrt(
            group["ax"].to_numpy(dtype=np.float64) ** 2
            + group["ay"].to_numpy(dtype=np.float64) ** 2
            + group["az"].to_numpy(dtype=np.float64) ** 2
        )
        gyro_norm = np.sqrt(
            group["wx"].to_numpy(dtype=np.float64) ** 2
            + group["wy"].to_numpy(dtype=np.float64) ** 2
            + group["wz"].to_numpy(dtype=np.float64) ** 2
        )
        group["x_m"] = x
        group["y_m"] = y
        group["dx_m"] = dx
        group["dy_m"] = dy
        group["speed_mps"] = np.clip(speed, 0.0, 25.0)
        group["accel_norm"] = accel_norm
        group["gyro_norm"] = gyro_norm
        group["dt_s"] = dt
        group["gps_valid"] = gps_valid
        parts.append(group)
    if not parts:
        raise SystemExit("No valid sensor rows were found after cleaning.")
    featured = pd.concat(parts, ignore_index=True)
    featured = featured.replace([np.inf, -np.inf], np.nan).dropna(subset=FEATURE_COLUMNS)
    return featured, origin_lat, origin_lng


def split_arrays(values: np.ndarray, train_ratio: float, validation_ratio: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    train_end = max(1, int(len(values) * train_ratio))
    validation_end = max(train_end + 1, int(len(values) * (train_ratio + validation_ratio)))
    validation_end = min(validation_end, len(values) - 1)
    return values[:train_end], values[train_end:validation_end], values[validation_end:]


def batch_errors(model: nn.Module, loader: DataLoader, device: torch.device) -> np.ndarray:
    model.eval()
    errors: list[np.ndarray] = []
    with torch.no_grad():
        for sequences, targets in loader:
            sequences = sequences.to(device=device, dtype=torch.float32)
            targets = targets.to(device=device, dtype=torch.float32)
            predicted = model(sequences)
            errors.append(torch.mean((predicted - targets) ** 2, dim=1).cpu().numpy())
    return np.concatenate(errors) if errors else np.empty(0, dtype=np.float32)


def train_command(args: argparse.Namespace) -> None:
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    device = choose_device(args.device)
    frame = read_source(args.source, sheet_name=args.sheet)
    if args.normal_labels:
        labels = {label.strip() for label in args.normal_labels.split(",") if label.strip()}
        frame = frame[frame["label"].astype(str).isin(labels)].copy()
    featured, origin_lat, origin_lng = build_features(frame)
    if len(featured) < args.sequence_length + 10:
        raise SystemExit("Not enough rows for LSTM training. Lower --sequence-length or add data.")

    raw_values = featured[FEATURE_COLUMNS].to_numpy(dtype=np.float32)
    train_raw, validation_raw, test_raw = split_arrays(raw_values, args.train_ratio, args.validation_ratio)
    scaler = RobustScaler.fit(train_raw)
    train_values = scaler.transform(train_raw)
    validation_values = scaler.transform(validation_raw)
    test_values = scaler.transform(test_raw)

    train_dataset = SequenceDataset(train_values, args.sequence_length)
    validation_dataset = SequenceDataset(validation_values, args.sequence_length)
    test_dataset = SequenceDataset(test_values, args.sequence_length)
    if len(train_dataset) == 0 or len(validation_dataset) == 0:
        raise SystemExit("No LSTM windows were created. Lower --sequence-length.")

    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True)
    validation_loader = DataLoader(validation_dataset, batch_size=args.batch_size, shuffle=False)
    test_loader = DataLoader(test_dataset, batch_size=args.batch_size, shuffle=False)

    model = SensorLSTM(
        input_size=len(FEATURE_COLUMNS),
        hidden_size=args.hidden_size,
        num_layers=args.num_layers,
        dropout=args.dropout,
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    criterion = nn.MSELoss()
    best_state = None
    best_validation = float("inf")
    history: list[dict[str, float | int]] = []

    print(
        json.dumps(
            {
                "rows": len(featured),
                "device": str(device),
                "features": FEATURE_COLUMNS,
                "train_windows": len(train_dataset),
                "validation_windows": len(validation_dataset),
                "test_windows": len(test_dataset),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    for epoch in range(1, args.epochs + 1):
        model.train()
        total = 0.0
        count = 0
        for sequences, targets in train_loader:
            sequences = sequences.to(device=device, dtype=torch.float32)
            targets = targets.to(device=device, dtype=torch.float32)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(sequences), targets)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total += float(loss.detach().cpu()) * len(sequences)
            count += len(sequences)
        validation_error = float(batch_errors(model, validation_loader, device).mean())
        train_loss = total / max(1, count)
        history.append({"epoch": epoch, "train_loss": train_loss, "validation_loss": validation_error})
        print(f"epoch={epoch:03d} train_loss={train_loss:.6f} validation_loss={validation_error:.6f}")
        if validation_error < best_validation:
            best_validation = validation_error
            best_state = {key: value.detach().cpu() for key, value in model.state_dict().items()}

    if best_state is None:
        raise RuntimeError("Training failed.")
    model.load_state_dict(best_state)
    validation_errors = batch_errors(model, validation_loader, device)
    test_errors = batch_errors(model, test_loader, device) if len(test_dataset) else np.empty(0)
    threshold = float(np.quantile(validation_errors, args.threshold_quantile))

    checkpoint = {
        "format_version": 1,
        "model_type": "iccas_realtime_sensor_lstm",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "feature_columns": FEATURE_COLUMNS,
        "raw_sensor_columns": RAW_SENSOR_COLUMNS,
        "sequence_length": args.sequence_length,
        "hidden_size": args.hidden_size,
        "num_layers": args.num_layers,
        "dropout": args.dropout,
        "origin_lat": origin_lat,
        "origin_lng": origin_lng,
        "scaler_center": scaler.center,
        "scaler_scale": scaler.scale,
        "threshold": threshold,
        "threshold_quantile": args.threshold_quantile,
        "route_x_m": featured["x_m"].to_numpy(dtype=np.float32),
        "route_y_m": featured["y_m"].to_numpy(dtype=np.float32),
        "route_threshold_m": args.route_threshold_m,
        "model_state": best_state,
        "history": history,
        "data_summary": {
            "source": str(args.source),
            "rows": len(featured),
            "labels": sorted(str(value) for value in featured["label"].dropna().unique()),
            "normal_labels": args.normal_labels,
            "validation_error_mean": float(validation_errors.mean()),
            "validation_error_std": float(validation_errors.std()),
            "test_error_mean": float(test_errors.mean()) if len(test_errors) else None,
        },
    }
    args.model.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, args.model)
    summary = {key: value for key, value in checkpoint.items() if key != "model_state"}
    summary["scaler_center"] = scaler.center.tolist()
    summary["scaler_scale"] = scaler.scale.tolist()
    summary["route_x_m"] = checkpoint["route_x_m"].tolist()
    summary["route_y_m"] = checkpoint["route_y_m"].tolist()
    summary["evaluation"] = evaluate_with_synthetic_events(
        model_path=args.model,
        source=args.source,
        sheet_name=args.sheet,
        device_name=args.device,
        consecutive_points=args.consecutive_points,
        recovery_points=args.recovery_points,
        normal_labels=args.normal_labels,
    )
    with args.model.with_suffix(".json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {
                "saved_model": str(args.model),
                "saved_metadata": str(args.model.with_suffix(".json")),
                "threshold": threshold,
                "accuracy": summary["evaluation"]["overall_accuracy"],
                "evaluation": summary["evaluation"],
                "backend_payload_note": "replay/live sends timestamp, device, anomaly_score, alarm_active, position_error_m",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def load_checkpoint(path: Path) -> dict[str, Any]:
    try:
        checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        checkpoint = torch.load(path, map_location="cpu")
    if checkpoint.get("model_type") != "iccas_realtime_sensor_lstm":
        raise SystemExit(f"Unsupported model file: {path}")
    return checkpoint


class OnlineSensorLSTM:
    def __init__(
        self,
        model_path: Path,
        device_name: str = "auto",
        consecutive_points: int = 3,
        recovery_points: int = 3,
        online_learning_rate: float = 0.0,
    ) -> None:
        self.checkpoint = load_checkpoint(model_path)
        self.device = choose_device(device_name)
        self.sequence_length = int(self.checkpoint["sequence_length"])
        self.feature_columns = list(self.checkpoint["feature_columns"])
        self.raw_sensor_columns = list(self.checkpoint["raw_sensor_columns"])
        self.origin_lat = float(self.checkpoint["origin_lat"])
        self.origin_lng = float(self.checkpoint["origin_lng"])
        self.threshold = float(self.checkpoint["threshold"])
        self.route_x_m = np.asarray(self.checkpoint.get("route_x_m", []), dtype=np.float32)
        self.route_y_m = np.asarray(self.checkpoint.get("route_y_m", []), dtype=np.float32)
        self.route_threshold_m = float(self.checkpoint.get("route_threshold_m", 30.0))
        self.scaler = RobustScaler(
            np.asarray(self.checkpoint["scaler_center"], dtype=np.float32),
            np.asarray(self.checkpoint["scaler_scale"], dtype=np.float32),
        )
        self.model = SensorLSTM(
            input_size=len(self.feature_columns),
            hidden_size=int(self.checkpoint["hidden_size"]),
            num_layers=int(self.checkpoint["num_layers"]),
            dropout=float(self.checkpoint["dropout"]),
        )
        self.model.load_state_dict(self.checkpoint["model_state"])
        self.model.to(self.device)
        self.model.eval()
        self.optimizer = (
            torch.optim.AdamW(self.model.parameters(), lr=online_learning_rate)
            if online_learning_rate > 0
            else None
        )
        self.buffer: deque[np.ndarray] = deque(maxlen=self.sequence_length)
        self.previous: dict[str, Any] | None = None
        self.consecutive_points = consecutive_points
        self.recovery_points = recovery_points
        self.outside_count = 0
        self.inside_count = 0
        self.alarm_active = False
        self.activity_counts: deque[str] = deque(maxlen=7)

    def reset(self) -> None:
        self.buffer.clear()
        self.previous = None
        self.outside_count = 0
        self.inside_count = 0
        self.alarm_active = False
        self.activity_counts.clear()

    def make_feature(self, point: dict[str, Any]) -> np.ndarray:
        timestamp = pd.to_datetime(point.get("server_time") or point.get("timestamp"), errors="coerce")
        if pd.isna(timestamp):
            timestamp = pd.Timestamp.now()
        raw_lat = point.get("lat", point.get("latitude"))
        raw_lng = point.get("lng", point.get("longitude"))
        gps_valid = raw_lat is not None and raw_lng is not None
        try:
            lat = float(raw_lat) if gps_valid else self.origin_lat
            lng = float(raw_lng) if gps_valid else self.origin_lng
            gps_valid = gps_valid and -90 <= lat <= 90 and -180 <= lng <= 180
        except (TypeError, ValueError):
            lat = self.origin_lat
            lng = self.origin_lng
            gps_valid = False
        x, y = latlon_to_xy(np.asarray([lat]), np.asarray([lng]), self.origin_lat, self.origin_lng)
        x_m = float(x[0])
        y_m = float(y[0])

        point_t_ms = point.get("t_ms")
        try:
            point_t_ms = float(point_t_ms) if point_t_ms is not None else None
        except (TypeError, ValueError):
            point_t_ms = None

        if self.previous is None:
            dt = 1.0
            dx = 0.0
            dy = 0.0
        else:
            if point_t_ms is not None and self.previous.get("t_ms") is not None:
                dt = (point_t_ms - float(self.previous["t_ms"])) / 1000.0
            else:
                dt = (timestamp - self.previous["timestamp"]).total_seconds()
            if not math.isfinite(dt) or dt <= 0 or dt > 30:
                dt = 1.0
                dx = 0.0
                dy = 0.0
                self.buffer.clear()
            else:
                dx = x_m - float(self.previous["x_m"])
                dy = y_m - float(self.previous["y_m"])
        speed = min(math.hypot(dx, dy) / max(dt, 0.02), 25.0)
        ax, ay, az = (float(point[column]) for column in ("ax", "ay", "az"))
        wx, wy, wz = (float(point[column]) for column in ("wx", "wy", "wz"))
        feature_map = {
            "roll": float(point["roll"]),
            "pitch": float(point["pitch"]),
            "yaw": float(point["yaw"]),
            "ax": ax,
            "ay": ay,
            "az": az,
            "wx": wx,
            "wy": wy,
            "wz": wz,
            "x_m": x_m,
            "y_m": y_m,
            "dx_m": dx,
            "dy_m": dy,
            "speed_mps": speed,
            "accel_norm": math.sqrt(ax * ax + ay * ay + az * az),
            "gyro_norm": math.sqrt(wx * wx + wy * wy + wz * wz),
            "dt_s": dt,
            "gps_valid": float(gps_valid),
        }
        self.previous = {
            "timestamp": pd.Timestamp(timestamp),
            "x_m": x_m,
            "y_m": y_m,
            "t_ms": point_t_ms,
        }
        return np.asarray([feature_map[column] for column in self.feature_columns], dtype=np.float32)

    def update(self, point: dict[str, Any]) -> StreamResult:
        raw_feature = self.make_feature(point)
        scaled_feature = self.scaler.transform(raw_feature[None, :])[0]
        ready = len(self.buffer) == self.sequence_length
        prediction_error = None
        anomaly_score = None
        position_error_m = None
        route_distance_m = None
        predicted_abnormal = False
        wandering_detected = False
        fall_detected = False
        detection_type = "normal"
        risk_level = "low"
        event = ""
        ax, ay, az = (float(point[column]) for column in ("ax", "ay", "az"))
        wx, wy, wz = (float(point[column]) for column in ("wx", "wy", "wz"))
        accel_norm = math.sqrt(ax * ax + ay * ay + az * az)
        gyro_norm = math.sqrt(wx * wx + wy * wy + wz * wz)
        # ICCAS IMU data is in g and deg/s-like gyro units. These thresholds
        # catch abrupt impact/rotation while the LSTM score supplies sequence context.
        impact_like_motion = accel_norm >= 2.5 or gyro_norm >= 250.0

        if ready:
            sequence = np.stack(self.buffer, axis=0)[None, :, :]
            tensor = torch.from_numpy(sequence).to(self.device, dtype=torch.float32)
            target = torch.from_numpy(scaled_feature[None, :]).to(self.device, dtype=torch.float32)
            with torch.no_grad():
                predicted = self.model(tensor)
            diff = predicted.detach().cpu().numpy()[0] - scaled_feature
            prediction_error = float(np.mean(diff**2))
            anomaly_score = prediction_error / max(self.threshold, 1e-12)
            predicted_abnormal = prediction_error > self.threshold
            predicted_raw = self.scaler.inverse_transform(predicted.detach().cpu().numpy())[0]
            x_index = self.feature_columns.index("x_m")
            y_index = self.feature_columns.index("y_m")
            gps_index = self.feature_columns.index("gps_valid")
            if raw_feature[gps_index] > 0.5:
                position_error_m = float(
                    math.hypot(
                        raw_feature[x_index] - predicted_raw[x_index],
                        raw_feature[y_index] - predicted_raw[y_index],
                    )
                )
                if len(self.route_x_m) and len(self.route_y_m):
                    route_distance_m = float(
                        np.sqrt(
                            (self.route_x_m - raw_feature[x_index]) ** 2
                            + (self.route_y_m - raw_feature[y_index]) ** 2
                        ).min()
                    )

            wandering_detected = bool(route_distance_m is not None and route_distance_m >= self.route_threshold_m)
            fall_detected = impact_like_motion and (predicted_abnormal or anomaly_score is None or anomaly_score >= 0.75)
            abnormal_signal = predicted_abnormal or wandering_detected or fall_detected

            if abnormal_signal:
                self.outside_count += 1
                self.inside_count = 0
                if not self.alarm_active and self.outside_count >= self.consecutive_points:
                    self.alarm_active = True
                    event = "ALARM_STARTED"
            else:
                self.inside_count += 1
                self.outside_count = 0
                if self.alarm_active and self.inside_count >= self.recovery_points:
                    self.alarm_active = False
                    event = "ALARM_CLEARED"

            if self.optimizer is not None and not predicted_abnormal:
                self.model.train()
                self.optimizer.zero_grad(set_to_none=True)
                loss = torch.mean((self.model(tensor) - target) ** 2)
                loss.backward()
                nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                self.optimizer.step()
                self.model.eval()

            if fall_detected:
                detection_type = "fall"
                risk_level = "critical"
            elif wandering_detected:
                detection_type = "wandering"
                risk_level = "high" if self.alarm_active else "medium"
            elif predicted_abnormal:
                detection_type = "sensor_anomaly"
                risk_level = "medium"

        label = point.get("label")
        raw_lat = point.get("lat", point.get("latitude"))
        raw_lng = point.get("lng", point.get("longitude"))
        lat = None
        lng = None
        gps_valid = False
        try:
            if raw_lat is not None and raw_lng is not None:
                lat = float(raw_lat)
                lng = float(raw_lng)
                gps_valid = -90 <= lat <= 90 and -180 <= lng <= 180
                if not gps_valid:
                    lat = None
                    lng = None
        except (TypeError, ValueError):
            lat = None
            lng = None
            gps_valid = False
        if label:
            self.activity_counts.append(str(label))
        activity_hint = max(set(self.activity_counts), key=self.activity_counts.count) if self.activity_counts else None
        self.buffer.append(scaled_feature)
        return StreamResult(
            timestamp=str(point.get("server_time") or point.get("timestamp") or datetime.now().isoformat()),
            device=str(point.get("device", "unknown")),
            label=str(label) if label is not None else None,
            lat=lat,
            lng=lng,
            gps_valid=gps_valid,
            ready=ready,
            predicted_abnormal=predicted_abnormal,
            wandering_detected=wandering_detected,
            fall_detected=fall_detected,
            alarm_active=self.alarm_active,
            anomaly_score=round(anomaly_score, 4) if anomaly_score is not None else None,
            prediction_error=round(prediction_error, 6) if prediction_error is not None else None,
            position_error_m=round(position_error_m, 3) if position_error_m is not None else None,
            route_distance_m=round(route_distance_m, 3) if route_distance_m is not None else None,
            activity_hint=activity_hint,
            detection_type=detection_type,
            risk_level=risk_level,
            event=event,
        )


def synthetic_event_frames(frame: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    clean = frame.sort_values(["device", "server_time"]).reset_index(drop=True).copy()
    if len(clean) < 40:
        return [("normal", clean)]
    start = max(10, len(clean) // 2)
    stop = min(len(clean), start + 80)

    normal = clean.copy()
    normal["expected_event"] = "normal"

    wandering = clean.copy()
    meters_lat = 150.0 / 111_320.0
    meters_lng = 150.0 / (111_320.0 * max(0.2, math.cos(math.radians(float(clean["lat"].dropna().median())))))
    wandering.loc[start:stop, "lat"] = pd.to_numeric(wandering.loc[start:stop, "lat"], errors="coerce") + meters_lat
    wandering.loc[start:stop, "lng"] = pd.to_numeric(wandering.loc[start:stop, "lng"], errors="coerce") + meters_lng
    wandering["expected_event"] = "normal"
    wandering.loc[start:stop, "expected_event"] = "wandering"

    fall = clean.copy()
    fall["expected_event"] = "normal"
    impact_rows = list(range(start, min(len(fall), start + 6)))
    fall.loc[impact_rows, ["ax", "ay", "az"]] = [3.2, -2.6, 4.1]
    fall.loc[impact_rows, ["wx", "wy", "wz"]] = [320.0, -280.0, 220.0]
    fall.loc[impact_rows, "expected_event"] = "fall"
    return [("normal", normal), ("wandering", wandering), ("fall", fall)]


def expected_positive(expected: str) -> bool:
    return expected in {"wandering", "fall"}


def predicted_positive(result: dict[str, Any]) -> bool:
    return bool(result["wandering_detected"] or result["fall_detected"])


def evaluate_with_synthetic_events(
    model_path: Path,
    source: Path,
    sheet_name: str,
    device_name: str,
    consecutive_points: int = 3,
    recovery_points: int = 3,
    normal_labels: str = "",
) -> dict[str, Any]:
    frame = read_source(source, sheet_name=sheet_name)
    if normal_labels:
        labels = {label.strip() for label in normal_labels.split(",") if label.strip()}
        frame = frame[frame["label"].astype(str).isin(labels)].copy()
    else:
        abnormal_labels = {"fall", "wandering", "abnormal"}
        normal_frame = frame[~frame["label"].astype(str).str.lower().isin(abnormal_labels)].copy()
        if len(normal_frame) >= 40:
            frame = normal_frame
    metrics: dict[str, Any] = {
        "source": str(source),
        "note": "Synthetic validation uses normal baseline rows and deterministic GPS-offset/IMU-impact events. When --normal-labels is set, only those labels are used as the normal baseline.",
        "sets": {},
    }
    total = 0
    correct = 0
    tp = fp = tn = fn = 0
    for name, eval_frame in synthetic_event_frames(frame):
        detector = OnlineSensorLSTM(
            model_path,
            device_name=device_name,
            consecutive_points=consecutive_points,
            recovery_points=recovery_points,
        )
        set_total = set_correct = 0
        set_tp = set_fp = set_tn = set_fn = 0
        previous_device = None
        for row in eval_frame.itertuples(index=False):
            if previous_device is not None and row.device != previous_device:
                detector.reset()
            previous_device = row.device
            result = asdict(detector.update(row_to_point(row)))
            if not result["ready"]:
                continue
            expected = getattr(row, "expected_event", "normal")
            exp = expected_positive(expected)
            pred = predicted_positive(result)
            set_total += 1
            set_correct += int(exp == pred)
            set_tp += int(exp and pred)
            set_fp += int((not exp) and pred)
            set_tn += int((not exp) and (not pred))
            set_fn += int(exp and (not pred))
        total += set_total
        correct += set_correct
        tp += set_tp
        fp += set_fp
        tn += set_tn
        fn += set_fn
        metrics["sets"][name] = {
            "points": set_total,
            "accuracy": round(set_correct / set_total, 4) if set_total else None,
            "tp": set_tp,
            "fp": set_fp,
            "tn": set_tn,
            "fn": set_fn,
        }
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    metrics.update(
        {
            "overall_accuracy": round(correct / total, 4) if total else None,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(2 * precision * recall / max(1e-12, precision + recall), 4),
            "tp": tp,
            "fp": fp,
            "tn": tn,
            "fn": fn,
        }
    )
    return metrics


def post_backend(url: str | None, payload: dict[str, Any], timeout: float) -> None:
    if not url:
        return
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status >= 400:
                raise urllib.error.HTTPError(
                    url,
                    response.status,
                    response.reason,
                    response.headers,
                    None,
                )
    except Exception as error:
        print(json.dumps({"backend_error": str(error), "url": url}, ensure_ascii=False), file=sys.stderr)


def row_to_point(row: Any) -> dict[str, Any]:
    return {
        "server_time": str(row.server_time),
        "device": str(row.device),
        "label": None if pd.isna(row.label) else str(row.label),
        "t_ms": None if pd.isna(row.t_ms) else float(row.t_ms),
        "roll": float(row.roll),
        "pitch": float(row.pitch),
        "yaw": float(row.yaw),
        "ax": float(row.ax),
        "ay": float(row.ay),
        "az": float(row.az),
        "wx": float(row.wx),
        "wy": float(row.wy),
        "wz": float(row.wz),
        "lat": None if pd.isna(row.lat) else float(row.lat),
        "lng": None if pd.isna(row.lng) else float(row.lng),
    }


def replay_command(args: argparse.Namespace) -> None:
    frame = read_source(args.source, sheet_name=args.sheet)
    detector = OnlineSensorLSTM(
        args.model,
        device_name=args.device,
        consecutive_points=args.consecutive_points,
        recovery_points=args.recovery_points,
        online_learning_rate=args.online_learning_rate,
    )
    records: list[dict[str, Any]] = []
    previous_device = None
    for row in frame.itertuples(index=False):
        if previous_device is not None and row.device != previous_device:
            detector.reset()
        previous_device = row.device
        point = row_to_point(row)
        result = asdict(detector.update(point))
        records.append(result)
        post_backend(args.backend_url, result, args.backend_timeout)
        if args.print_events and (result["event"] or result["ready"]):
            print(json.dumps(result, ensure_ascii=False))
        if args.sleep > 0:
            time.sleep(args.sleep)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records).to_csv(args.output, index=False)
    summary = {
        "output": str(args.output),
        "points": len(records),
        "ready_points": int(sum(1 for item in records if item["ready"])),
        "alarm_points": int(sum(1 for item in records if item["alarm_active"])),
        "wandering_points": int(sum(1 for item in records if item["wandering_detected"])),
        "fall_points": int(sum(1 for item in records if item["fall_detected"])),
        "backend_url": args.backend_url,
    }
    with args.output.with_suffix(".json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def live_command(args: argparse.Namespace) -> None:
    detector = OnlineSensorLSTM(
        args.model,
        device_name=args.device,
        consecutive_points=args.consecutive_points,
        recovery_points=args.recovery_points,
        online_learning_rate=args.online_learning_rate,
    )
    print(json.dumps({"ready": True, "mode": "live", "sequence_length": detector.sequence_length}, ensure_ascii=False), flush=True)
    for line in sys.stdin:
        try:
            point = json.loads(line)
            if point.get("reset"):
                detector.reset()
                print(json.dumps({"reset": True}), flush=True)
                continue
            result = asdict(detector.update(point))
            post_backend(args.backend_url, result, args.backend_timeout)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as error:
            print(json.dumps({"error": str(error)}, ensure_ascii=False), flush=True)


def inspect_command(args: argparse.Namespace) -> None:
    frame = read_source(args.source, sheet_name=args.sheet)
    featured, _, _ = build_features(frame)
    summary = {
        "source": str(args.source),
        "rows": len(frame),
        "devices": sorted(frame["device"].unique().tolist()),
        "columns": list(frame.columns),
        "labels": sorted(str(value) for value in frame["label"].dropna().unique()),
        "time_start": frame["server_time"].min().isoformat(),
        "time_end": frame["server_time"].max().isoformat(),
        "lstm_features": FEATURE_COLUMNS,
        "sample": featured[["server_time", "device", "label", *FEATURE_COLUMNS]].head(5).to_dict(orient="records"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--source", type=Path, default=Path("ICCAS_data.xlsx"))
    inspect.add_argument("--sheet", default="data")

    train = sub.add_parser("train")
    train.add_argument("--source", type=Path, default=Path("ICCAS_total_data.xlsx"))
    train.add_argument("--sheet", default="data")
    train.add_argument("--model", type=Path, default=Path("models/iccas_sensor_lstm.pt"))
    train.add_argument("--normal-labels", default="", help="Optional comma list, e.g. idle,walk")
    train.add_argument("--sequence-length", type=int, default=8)
    train.add_argument("--hidden-size", type=int, default=64)
    train.add_argument("--num-layers", type=int, default=2)
    train.add_argument("--dropout", type=float, default=0.2)
    train.add_argument("--epochs", type=int, default=80)
    train.add_argument("--batch-size", type=int, default=32)
    train.add_argument("--learning-rate", type=float, default=1e-3)
    train.add_argument("--weight-decay", type=float, default=1e-4)
    train.add_argument("--threshold-quantile", type=float, default=0.98)
    train.add_argument("--route-threshold-m", type=float, default=30.0)
    train.add_argument("--consecutive-points", type=int, default=3)
    train.add_argument("--recovery-points", type=int, default=3)
    train.add_argument("--train-ratio", type=float, default=0.70)
    train.add_argument("--validation-ratio", type=float, default=0.15)
    train.add_argument("--seed", type=int, default=42)
    train.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")

    replay = sub.add_parser("replay")
    replay.add_argument("--source", type=Path, default=Path("ICCAS_total_data.xlsx"))
    replay.add_argument("--sheet", default="data")
    replay.add_argument("--model", type=Path, default=Path("models/iccas_sensor_lstm.pt"))
    replay.add_argument("--output", type=Path, default=Path("data/iccas_sensor_lstm/realtime_results.csv"))
    replay.add_argument("--backend-url", default="")
    replay.add_argument("--backend-timeout", type=float, default=2.0)
    replay.add_argument("--sleep", type=float, default=0.0, help="Seconds between rows for real-time demo.")
    replay.add_argument("--consecutive-points", type=int, default=3)
    replay.add_argument("--recovery-points", type=int, default=3)
    replay.add_argument("--online-learning-rate", type=float, default=0.0, help="Optional online fine-tuning on predicted-normal points.")
    replay.add_argument("--print-events", action="store_true")
    replay.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")

    live = sub.add_parser("live")
    live.add_argument("--model", type=Path, default=Path("models/iccas_sensor_lstm.pt"))
    live.add_argument("--backend-url", default="")
    live.add_argument("--backend-timeout", type=float, default=2.0)
    live.add_argument("--consecutive-points", type=int, default=3)
    live.add_argument("--recovery-points", type=int, default=3)
    live.add_argument("--online-learning-rate", type=float, default=0.0)
    live.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "inspect":
        inspect_command(args)
    elif args.command == "train":
        train_command(args)
    elif args.command == "replay":
        replay_command(args)
    elif args.command == "live":
        live_command(args)


if __name__ == "__main__":
    main()
