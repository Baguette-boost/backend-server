"""Train separate GPS and IMU/Gyro LSTM classifiers by ICCAS scenario sheet."""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

from realtime_sensor_lstm import build_features, read_source


SCENARIOS = [
    ("추가된 학교 데이터", "walk"),
    ("집 앞 walk", "walk"),
    ("학교 walk", "walk"),
    ("새로운 경로에서 배회", "wandering"),
    ("떨어졌다가 앞으로 걸어가고 떨어졌다 앞으로 걸어감", "fall"),
    ("새로운 경로 1.2km, 새로운 경로 600m", "wandering"),
    ("제자리에서 떨어짐1,2", "fall"),
    ("sit", "sit"),
    ("idle", "idle"),
]

CLASS_LABELS = ["walk", "wandering", "fall", "sit", "idle"]
GPS_FEATURES = ["x_m", "y_m", "dx_m", "dy_m", "speed_mps", "dt_s", "gps_valid"]
IMU_GYRO_FEATURES = [
    "roll",
    "pitch",
    "yaw",
    "ax",
    "ay",
    "az",
    "wx",
    "wy",
    "wz",
    "accel_norm",
    "gyro_norm",
    "dt_s",
]


class SequenceDataset(Dataset):
    def __init__(self, sequences: np.ndarray, labels: np.ndarray) -> None:
        self.sequences = torch.tensor(sequences, dtype=torch.float32)
        self.labels = torch.tensor(labels, dtype=torch.long)

    def __len__(self) -> int:
        return len(self.labels)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self.sequences[index], self.labels[index]


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


class LSTMClassifier(nn.Module):
    def __init__(
        self,
        input_size: int,
        hidden_size: int,
        num_layers: int,
        dropout: float,
        num_classes: int,
    ) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.head = nn.Sequential(
            nn.LayerNorm(hidden_size),
            nn.Linear(hidden_size, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        output, _ = self.lstm(x)
        return self.head(output[:, -1, :])


@dataclass
class SplitData:
    x_train: np.ndarray
    y_train: np.ndarray
    s_train: list[str]
    x_val: np.ndarray
    y_val: np.ndarray
    s_val: list[str]
    x_test: np.ndarray
    y_test: np.ndarray
    s_test: list[str]


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def load_scenario_frames(source: Path) -> list[pd.DataFrame]:
    raw_frames: list[pd.DataFrame] = []
    for sheet_name, class_label in SCENARIOS:
        frame = read_source(source, sheet_name=sheet_name)
        frame = frame.copy()
        frame["source_sheet"] = sheet_name
        frame["scenario"] = sheet_name
        frame["class_label"] = class_label
        frame["original_label"] = frame["label"]
        frame["label"] = class_label
        raw_frames.append(frame)
    return raw_frames


def add_features_per_scenario(frames: list[pd.DataFrame]) -> list[pd.DataFrame]:
    combined = pd.concat(frames, ignore_index=True)
    origin_lat = float(combined["lat"].dropna().median())
    origin_lng = float(combined["lng"].dropna().median())
    featured_frames: list[pd.DataFrame] = []
    for frame in frames:
        featured, _, _ = build_features(frame, origin_lat=origin_lat, origin_lng=origin_lng)
        featured["source_sheet"] = frame["source_sheet"].iloc[0]
        featured["scenario"] = frame["scenario"].iloc[0]
        featured["class_label"] = frame["class_label"].iloc[0]
        featured["original_label"] = frame["original_label"].iloc[0]
        featured_frames.append(featured)
    return featured_frames


def sequences_for_frame(
    frame: pd.DataFrame,
    feature_columns: list[str],
    sequence_length: int,
    label_to_id: dict[str, int],
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    values = frame[feature_columns].to_numpy(dtype=np.float32)
    label = label_to_id[str(frame["class_label"].iloc[0])]
    scenario = str(frame["scenario"].iloc[0])
    sequences: list[np.ndarray] = []
    labels: list[int] = []
    scenarios: list[str] = []
    for end in range(sequence_length, len(values) + 1):
        sequences.append(values[end - sequence_length : end])
        labels.append(label)
        scenarios.append(scenario)
    if not sequences:
        return (
            np.empty((0, sequence_length, len(feature_columns)), dtype=np.float32),
            np.empty((0,), dtype=np.int64),
            [],
        )
    return np.stack(sequences).astype(np.float32), np.array(labels, dtype=np.int64), scenarios


def split_indices(count: int, train_ratio: float, val_ratio: float) -> tuple[slice, slice, slice]:
    train_end = max(1, int(count * train_ratio))
    val_end = max(train_end + 1, int(count * (train_ratio + val_ratio)))
    val_end = min(val_end, count - 1)
    return slice(0, train_end), slice(train_end, val_end), slice(val_end, count)


def build_split(
    frames: list[pd.DataFrame],
    feature_columns: list[str],
    sequence_length: int,
    train_ratio: float,
    val_ratio: float,
) -> SplitData:
    label_to_id = {label: index for index, label in enumerate(CLASS_LABELS)}
    buckets: dict[str, list[Any]] = {
        "x_train": [],
        "y_train": [],
        "s_train": [],
        "x_val": [],
        "y_val": [],
        "s_val": [],
        "x_test": [],
        "y_test": [],
        "s_test": [],
    }
    for frame in frames:
        x, y, scenarios = sequences_for_frame(frame, feature_columns, sequence_length, label_to_id)
        if len(y) < 3:
            continue
        train_slice, val_slice, test_slice = split_indices(len(y), train_ratio, val_ratio)
        buckets["x_train"].append(x[train_slice])
        buckets["y_train"].append(y[train_slice])
        buckets["s_train"].extend(scenarios[train_slice])
        buckets["x_val"].append(x[val_slice])
        buckets["y_val"].append(y[val_slice])
        buckets["s_val"].extend(scenarios[val_slice])
        buckets["x_test"].append(x[test_slice])
        buckets["y_test"].append(y[test_slice])
        buckets["s_test"].extend(scenarios[test_slice])

    def cat(name: str, shape: tuple[int, ...], dtype: Any) -> np.ndarray:
        if not buckets[name]:
            return np.empty(shape, dtype=dtype)
        return np.concatenate(buckets[name], axis=0)

    return SplitData(
        x_train=cat("x_train", (0, sequence_length, len(feature_columns)), np.float32),
        y_train=cat("y_train", (0,), np.int64),
        s_train=list(buckets["s_train"]),
        x_val=cat("x_val", (0, sequence_length, len(feature_columns)), np.float32),
        y_val=cat("y_val", (0,), np.int64),
        s_val=list(buckets["s_val"]),
        x_test=cat("x_test", (0, sequence_length, len(feature_columns)), np.float32),
        y_test=cat("y_test", (0,), np.int64),
        s_test=list(buckets["s_test"]),
    )


def fit_transform(split: SplitData) -> tuple[SplitData, RobustScaler]:
    scaler = RobustScaler.fit(split.x_train.reshape(-1, split.x_train.shape[-1]))

    def transform(x: np.ndarray) -> np.ndarray:
        flat = x.reshape(-1, x.shape[-1])
        return scaler.transform(flat).reshape(x.shape)

    return (
        SplitData(
            x_train=transform(split.x_train),
            y_train=split.y_train,
            s_train=split.s_train,
            x_val=transform(split.x_val),
            y_val=split.y_val,
            s_val=split.s_val,
            x_test=transform(split.x_test),
            y_test=split.y_test,
            s_test=split.s_test,
        ),
        scaler,
    )


def class_weights(labels: np.ndarray) -> torch.Tensor:
    counts = np.bincount(labels, minlength=len(CLASS_LABELS)).astype(np.float32)
    counts = np.where(counts > 0, counts, 1.0)
    weights = counts.sum() / (len(CLASS_LABELS) * counts)
    return torch.tensor(weights, dtype=torch.float32)


def predict(model: nn.Module, x: np.ndarray, batch_size: int, device: torch.device) -> tuple[np.ndarray, np.ndarray]:
    loader = DataLoader(SequenceDataset(x, np.zeros(len(x), dtype=np.int64)), batch_size=batch_size)
    logits_all: list[np.ndarray] = []
    model.eval()
    with torch.no_grad():
        for sequences, _ in loader:
            logits = model(sequences.to(device))
            logits_all.append(logits.cpu().numpy())
    logits_np = np.concatenate(logits_all, axis=0)
    probs = softmax(logits_np)
    return probs.argmax(axis=1), probs


def softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=1, keepdims=True)


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    matrix = np.zeros((len(CLASS_LABELS), len(CLASS_LABELS)), dtype=np.int64)
    for truth, pred in zip(y_true, y_pred):
        matrix[int(truth), int(pred)] += 1
    total = int(matrix.sum())
    correct = int(np.trace(matrix))
    per_class: dict[str, Any] = {}
    precisions: list[float] = []
    recalls: list[float] = []
    f1s: list[float] = []
    supports: list[int] = []
    for idx, label in enumerate(CLASS_LABELS):
        tp = int(matrix[idx, idx])
        fp = int(matrix[:, idx].sum() - tp)
        fn = int(matrix[idx, :].sum() - tp)
        support = int(matrix[idx, :].sum())
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
        }
        precisions.append(precision)
        recalls.append(recall)
        f1s.append(f1)
        supports.append(support)
    support_np = np.array(supports, dtype=np.float64)
    weight = support_np / max(1.0, support_np.sum())
    return {
        "accuracy": correct / total if total else 0.0,
        "macro_precision": float(np.mean(precisions)),
        "macro_recall": float(np.mean(recalls)),
        "macro_f1": float(np.mean(f1s)),
        "weighted_f1": float(np.sum(np.array(f1s) * weight)),
        "total": total,
        "correct": correct,
        "confusion_matrix": matrix.tolist(),
        "labels": CLASS_LABELS,
        "per_class": per_class,
    }


def scenario_metrics(y_true: np.ndarray, y_pred: np.ndarray, scenarios: list[str]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for scenario in sorted(set(scenarios)):
        idx = np.array([item == scenario for item in scenarios], dtype=bool)
        output[scenario] = compute_metrics(y_true[idx], y_pred[idx])
    return output


def train_one(
    name: str,
    split: SplitData,
    feature_columns: list[str],
    args: argparse.Namespace,
    output_dir: Path,
) -> dict[str, Any]:
    split, scaler = fit_transform(split)
    device = torch.device(args.device)
    model = LSTMClassifier(
        input_size=len(feature_columns),
        hidden_size=args.hidden_size,
        num_layers=args.num_layers,
        dropout=args.dropout,
        num_classes=len(CLASS_LABELS),
    ).to(device)
    train_loader = DataLoader(
        SequenceDataset(split.x_train, split.y_train),
        batch_size=args.batch_size,
        shuffle=True,
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    criterion = nn.CrossEntropyLoss(weight=class_weights(split.y_train).to(device))
    best_state = None
    best_val = math.inf
    history: list[dict[str, float]] = []

    for epoch in range(1, args.epochs + 1):
        model.train()
        running = 0.0
        count = 0
        for sequences, labels in train_loader:
            sequences = sequences.to(device)
            labels = labels.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(sequences), labels)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            running += float(loss.item()) * len(labels)
            count += len(labels)
        train_loss = running / max(1, count)
        val_pred, _ = predict(model, split.x_val, args.batch_size, device)
        val_metrics = compute_metrics(split.y_val, val_pred)
        val_loss = 1.0 - val_metrics["accuracy"]
        history.append(
            {
                "epoch": float(epoch),
                "train_loss": float(train_loss),
                "validation_accuracy": float(val_metrics["accuracy"]),
                "validation_macro_f1": float(val_metrics["macro_f1"]),
            }
        )
        print(
            f"{name} epoch={epoch:03d} train_loss={train_loss:.6f} "
            f"val_acc={val_metrics['accuracy']:.4f} val_f1={val_metrics['macro_f1']:.4f}"
        )
        if val_loss < best_val:
            best_val = val_loss
            best_state = {key: value.detach().cpu() for key, value in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)
    test_pred, test_probs = predict(model, split.x_test, args.batch_size, device)
    test_metrics = compute_metrics(split.y_test, test_pred)
    scenario_test_metrics = scenario_metrics(split.y_test, test_pred, split.s_test)

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / f"iccas_lstm_v1_{name}.pt"
    metadata_path = output_dir / f"iccas_lstm_v1_{name}.json"
    checkpoint = {
        "model_type": "iccas_parallel_sensor_lstm_classifier",
        "sensor_stream": name,
        "feature_columns": feature_columns,
        "class_labels": CLASS_LABELS,
        "sequence_length": args.sequence_length,
        "hidden_size": args.hidden_size,
        "num_layers": args.num_layers,
        "dropout": args.dropout,
        "scaler_center": scaler.center,
        "scaler_scale": scaler.scale,
        "model_state": model.state_dict(),
    }
    torch.save(checkpoint, model_path)
    metadata = {
        key: value
        for key, value in checkpoint.items()
        if key not in {"model_state", "scaler_center", "scaler_scale"}
    }
    metadata["scaler_center"] = scaler.center.tolist()
    metadata["scaler_scale"] = scaler.scale.tolist()
    metadata["history"] = history
    metadata["split_sizes"] = {
        "train": int(len(split.y_train)),
        "validation": int(len(split.y_val)),
        "test": int(len(split.y_test)),
    }
    metadata["test_metrics"] = test_metrics
    metadata["test_metrics_by_scenario"] = scenario_test_metrics
    metadata["sample_predictions"] = [
        {
            "scenario": split.s_test[i],
            "true": CLASS_LABELS[int(split.y_test[i])],
            "predicted": CLASS_LABELS[int(test_pred[i])],
            "confidence": float(test_probs[i].max()),
        }
        for i in range(min(20, len(test_pred)))
    ]
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "name": name,
        "model_path": str(model_path),
        "metadata_path": str(metadata_path),
        "metrics": test_metrics,
        "scenario_metrics": scenario_test_metrics,
    }


def write_scenario_files(frames: list[pd.DataFrame], output_dir: Path) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for frame in frames:
        scenario = str(frame["scenario"].iloc[0])
        safe = (
            scenario.replace("/", "_")
            .replace(",", "_")
            .replace(" ", "_")
        )
        path = output_dir / f"{safe}.csv"
        frame.to_csv(path, index=False)
        counts[scenario] = int(len(frame))
    return counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("ICCAS_dataV1.xlsx"))
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--report", type=Path, default=Path("data/iccas_sensor_lstm/parallel_sensor_lstm_metrics.json"))
    parser.add_argument("--scenario-dir", type=Path, default=Path("data/iccas_sensor_lstm/scenarios"))
    parser.add_argument("--sequence-length", type=int, default=16)
    parser.add_argument("--hidden-size", type=int, default=64)
    parser.add_argument("--num-layers", type=int, default=2)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--epochs", type=int, default=35)
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
    raw_frames = load_scenario_frames(args.source)
    featured_frames = add_features_per_scenario(raw_frames)
    scenario_counts = write_scenario_files(featured_frames, args.scenario_dir)

    gps_split = build_split(
        featured_frames,
        GPS_FEATURES,
        args.sequence_length,
        args.train_ratio,
        args.validation_ratio,
    )
    imu_split = build_split(
        featured_frames,
        IMU_GYRO_FEATURES,
        args.sequence_length,
        args.train_ratio,
        args.validation_ratio,
    )

    gps_result = train_one("gps", gps_split, GPS_FEATURES, args, args.model_dir)
    imu_result = train_one("imu_gyro", imu_split, IMU_GYRO_FEATURES, args, args.model_dir)
    report = {
        "source": str(args.source),
        "scenario_counts": scenario_counts,
        "class_labels": CLASS_LABELS,
        "sequence_length": args.sequence_length,
        "split_note": "Sequences are created inside each source sheet and split chronologically per scenario.",
        "models": {
            "gps": gps_result,
            "imu_gyro": imu_result,
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
