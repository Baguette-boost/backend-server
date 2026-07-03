"""Train specialized binary GPS-wandering and IMU-fall LSTM models."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

from realtime_sensor_lstm import build_features, read_source
from train_parallel_sensor_lstm import GPS_FEATURES, IMU_GYRO_FEATURES, SCENARIOS


class SequenceDataset(Dataset):
    def __init__(self, x: np.ndarray, y: np.ndarray) -> None:
        self.x = torch.tensor(x, dtype=torch.float32)
        self.y = torch.tensor(y, dtype=torch.float32)

    def __len__(self) -> int:
        return len(self.y)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self.x[index], self.y[index]


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
        return cls(center, scale)

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


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def load_frames(source: Path) -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    raw_frames: list[pd.DataFrame] = []
    for sheet, label in SCENARIOS:
        frame = read_source(source, sheet_name=sheet)
        frame = frame.copy()
        frame["scenario"] = sheet
        frame["class_label"] = label
        frame["label"] = label
        raw_frames.append(frame)
    combined = pd.concat(raw_frames, ignore_index=True)
    origin_lat = float(combined["lat"].dropna().median())
    origin_lng = float(combined["lng"].dropna().median())
    for frame in raw_frames:
        featured, _, _ = build_features(frame, origin_lat=origin_lat, origin_lng=origin_lng)
        featured["scenario"] = frame["scenario"].iloc[0]
        featured["class_label"] = frame["class_label"].iloc[0]
        frames.append(featured)
    return frames


def make_sequences(
    frames: list[pd.DataFrame],
    feature_columns: list[str],
    positive_label: str,
    sequence_length: int,
    train_ratio: float,
    validation_ratio: float,
) -> dict[str, Any]:
    buckets: dict[str, list[Any]] = {key: [] for key in ["x_train", "y_train", "s_train", "x_val", "y_val", "s_val", "x_test", "y_test", "s_test"]}
    for frame in frames:
        values = frame[feature_columns].to_numpy(dtype=np.float32)
        y_value = 1 if str(frame["class_label"].iloc[0]) == positive_label else 0
        scenario = str(frame["scenario"].iloc[0])
        x_items: list[np.ndarray] = []
        y_items: list[int] = []
        s_items: list[str] = []
        for end in range(sequence_length, len(values) + 1):
            x_items.append(values[end - sequence_length : end])
            y_items.append(y_value)
            s_items.append(scenario)
        if len(y_items) < 3:
            continue
        x = np.stack(x_items).astype(np.float32)
        y = np.array(y_items, dtype=np.float32)
        train_end = max(1, int(len(y) * train_ratio))
        val_end = min(max(train_end + 1, int(len(y) * (train_ratio + validation_ratio))), len(y) - 1)
        splits = {
            "train": slice(0, train_end),
            "val": slice(train_end, val_end),
            "test": slice(val_end, len(y)),
        }
        for split_name, split_slice in splits.items():
            buckets[f"x_{split_name}"].append(x[split_slice])
            buckets[f"y_{split_name}"].append(y[split_slice])
            buckets[f"s_{split_name}"].extend(s_items[split_slice])

    def cat(name: str, dtype: Any) -> np.ndarray:
        return np.concatenate(buckets[name], axis=0).astype(dtype)

    return {
        "x_train": cat("x_train", np.float32),
        "y_train": cat("y_train", np.float32),
        "s_train": list(buckets["s_train"]),
        "x_val": cat("x_val", np.float32),
        "y_val": cat("y_val", np.float32),
        "s_val": list(buckets["s_val"]),
        "x_test": cat("x_test", np.float32),
        "y_test": cat("y_test", np.float32),
        "s_test": list(buckets["s_test"]),
    }


def scale_split(split: dict[str, Any]) -> tuple[dict[str, Any], RobustScaler]:
    scaler = RobustScaler.fit(split["x_train"].reshape(-1, split["x_train"].shape[-1]))
    out = dict(split)
    for key in ["x_train", "x_val", "x_test"]:
        x = split[key]
        out[key] = scaler.transform(x.reshape(-1, x.shape[-1])).reshape(x.shape)
    return out, scaler


def metrics(y_true: np.ndarray, scores: np.ndarray, threshold: float = 0.5) -> dict[str, Any]:
    pred = scores >= threshold
    truth = y_true.astype(bool)
    tp = int((truth & pred).sum())
    fp = int((~truth & pred).sum())
    tn = int((~truth & ~pred).sum())
    fn = int((truth & ~pred).sum())
    accuracy = (tp + tn) / max(1, tp + fp + tn + fn)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "threshold": threshold,
    }


def best_threshold(y_true: np.ndarray, scores: np.ndarray) -> tuple[float, dict[str, Any]]:
    best_t = 0.5
    best_m = metrics(y_true, scores, best_t)
    for threshold in np.linspace(0.05, 0.95, 91):
        current = metrics(y_true, scores, float(threshold))
        if (current["f1"], current["accuracy"]) > (best_m["f1"], best_m["accuracy"]):
            best_t = float(threshold)
            best_m = current
    return best_t, best_m


def predict(model: nn.Module, x: np.ndarray, batch_size: int, device: torch.device) -> np.ndarray:
    loader = DataLoader(SequenceDataset(x, np.zeros(len(x), dtype=np.float32)), batch_size=batch_size)
    chunks: list[np.ndarray] = []
    model.eval()
    with torch.no_grad():
        for sequences, _ in loader:
            logits = model(sequences.to(device))
            chunks.append(torch.sigmoid(logits).cpu().numpy())
    return np.concatenate(chunks)


def scenario_metrics(y_true: np.ndarray, scores: np.ndarray, scenarios: list[str], threshold: float) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for scenario in sorted(set(scenarios)):
        idx = np.array([item == scenario for item in scenarios], dtype=bool)
        out[scenario] = metrics(y_true[idx], scores[idx], threshold)
    return out


def train_task(
    task_name: str,
    positive_label: str,
    feature_columns: list[str],
    frames: list[pd.DataFrame],
    args: argparse.Namespace,
) -> dict[str, Any]:
    split = make_sequences(
        frames,
        feature_columns,
        positive_label,
        args.sequence_length,
        args.train_ratio,
        args.validation_ratio,
    )
    split, scaler = scale_split(split)
    device = torch.device(args.device)
    model = BinaryLSTM(len(feature_columns), args.hidden_size, args.num_layers, args.dropout).to(device)
    pos = float(split["y_train"].sum())
    neg = float(len(split["y_train"]) - pos)
    pos_weight = torch.tensor([neg / max(pos, 1.0)], dtype=torch.float32, device=device)
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    loader = DataLoader(SequenceDataset(split["x_train"], split["y_train"]), batch_size=args.batch_size, shuffle=True)
    best_state = None
    best_f1 = -1.0
    history: list[dict[str, float]] = []
    for epoch in range(1, args.epochs + 1):
        model.train()
        total = 0.0
        count = 0
        for sequences, labels in loader:
            sequences = sequences.to(device)
            labels = labels.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(sequences), labels)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total += float(loss.item()) * len(labels)
            count += len(labels)
        val_scores = predict(model, split["x_val"], args.batch_size, device)
        _, val_metrics = best_threshold(split["y_val"], val_scores)
        history.append({"epoch": epoch, "loss": total / max(1, count), "validation_f1": val_metrics["f1"]})
        print(f"{task_name} epoch={epoch:03d} loss={total / max(1, count):.6f} val_f1={val_metrics['f1']:.4f}")
        if val_metrics["f1"] > best_f1:
            best_f1 = val_metrics["f1"]
            best_state = {key: value.detach().cpu() for key, value in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)
    val_scores = predict(model, split["x_val"], args.batch_size, device)
    threshold, validation_metrics = best_threshold(split["y_val"], val_scores)
    test_scores = predict(model, split["x_test"], args.batch_size, device)
    test_metrics = metrics(split["y_test"], test_scores, threshold)
    by_scenario = scenario_metrics(split["y_test"], test_scores, split["s_test"], threshold)

    args.model_dir.mkdir(parents=True, exist_ok=True)
    model_path = args.model_dir / f"iccas_lstm_v1_{task_name}.pt"
    metadata_path = args.model_dir / f"iccas_lstm_v1_{task_name}.json"
    checkpoint = {
        "model_type": "iccas_specialized_binary_lstm",
        "task": task_name,
        "positive_label": positive_label,
        "feature_columns": feature_columns,
        "sequence_length": args.sequence_length,
        "threshold": threshold,
        "scaler_center": scaler.center,
        "scaler_scale": scaler.scale,
        "hidden_size": args.hidden_size,
        "num_layers": args.num_layers,
        "dropout": args.dropout,
        "model_state": model.state_dict(),
    }
    torch.save(checkpoint, model_path)
    metadata = {key: value for key, value in checkpoint.items() if key not in {"model_state", "scaler_center", "scaler_scale"}}
    metadata["scaler_center"] = scaler.center.tolist()
    metadata["scaler_scale"] = scaler.scale.tolist()
    metadata["split_sizes"] = {"train": len(split["y_train"]), "validation": len(split["y_val"]), "test": len(split["y_test"])}
    metadata["validation_metrics"] = validation_metrics
    metadata["test_metrics"] = test_metrics
    metadata["test_metrics_by_scenario"] = by_scenario
    metadata["history"] = history
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "task": task_name,
        "model_path": str(model_path),
        "metadata_path": str(metadata_path),
        "threshold": threshold,
        "validation_metrics": validation_metrics,
        "test_metrics": test_metrics,
        "test_metrics_by_scenario": by_scenario,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("ICCAS_dataV1.xlsx"))
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--report", type=Path, default=Path("data/iccas_sensor_lstm/specialized_sensor_lstm_metrics.json"))
    parser.add_argument("--sequence-length", type=int, default=16)
    parser.add_argument("--hidden-size", type=int, default=64)
    parser.add_argument("--num-layers", type=int, default=2)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--epochs", type=int, default=45)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--train-ratio", type=float, default=0.70)
    parser.add_argument("--validation-ratio", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    set_seed(args.seed)
    frames = load_frames(args.source)
    gps = train_task("gps_wandering", "wandering", GPS_FEATURES, frames, args)
    imu = train_task("imu_fall", "fall", IMU_GYRO_FEATURES, frames, args)
    report = {"source": str(args.source), "sequence_length": args.sequence_length, "models": {"gps_wandering": gps, "imu_fall": imu}}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
