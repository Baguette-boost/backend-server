"""Train an IMU/Gyro fall LSTM with the ICCAS + SisFall merged CSV."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset


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
    "accel_norm",
    "gyro_norm",
    "dt_s",
]


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
    def __init__(
        self,
        input_size: int,
        hidden_size: int,
        num_layers: int,
        dropout: float,
        bidirectional: bool,
        pooling: str,
    ) -> None:
        super().__init__()
        self.bidirectional = bidirectional
        self.pooling = pooling
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0.0,
            batch_first=True,
            bidirectional=bidirectional,
        )
        output_size = hidden_size * (2 if bidirectional else 1)
        self.attention = nn.Linear(output_size, 1)
        self.head = nn.Sequential(
            nn.LayerNorm(output_size),
            nn.Dropout(dropout),
            nn.Linear(output_size, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        output, _ = self.lstm(x)
        if self.pooling == "attention":
            weights = torch.softmax(self.attention(output), dim=1)
            pooled = (output * weights).sum(dim=1)
        elif self.pooling == "mean":
            pooled = output.mean(dim=1)
        else:
            pooled = output[:, -1, :]
        return self.head(pooled).squeeze(-1)


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def stable_bucket(value: str, seed: int) -> float:
    digest = hashlib.sha1(f"{seed}:{value}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def load_merged_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    frame = frame.copy()
    for column in ["roll", "pitch", "yaw", "ax", "ay", "az", "wx", "wy", "wz", "t_ms"]:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    if "label" not in frame.columns:
        raise SystemExit("Merged CSV must contain a label column.")
    for column, default in {
        "device": "unknown-device",
        "source_dataset": "unknown",
        "source_file": "unknown-file",
        "source_activity": "unknown-activity",
    }.items():
        if column not in frame.columns:
            frame[column] = default
        frame[column] = frame[column].fillna(default).astype(str)

    frame["label"] = frame["label"].fillna("normal").astype(str)
    frame["accel_norm"] = np.sqrt(frame["ax"] ** 2 + frame["ay"] ** 2 + frame["az"] ** 2)
    frame["gyro_norm"] = np.sqrt(frame["wx"] ** 2 + frame["wy"] ** 2 + frame["wz"] ** 2)
    frame["group_id"] = (
        frame["source_dataset"]
        + "::"
        + frame["source_file"]
        + "::"
        + frame["device"]
        + "::"
        + frame["source_activity"]
    )
    frame["dt_s"] = frame.groupby("group_id")["t_ms"].diff().fillna(0.0).clip(lower=0.0, upper=1_000.0) / 1000.0
    return frame


def split_group(group_id: str, seed: int, train_ratio: float, validation_ratio: float) -> str:
    bucket = stable_bucket(group_id, seed)
    if bucket < train_ratio:
        return "train"
    if bucket < train_ratio + validation_ratio:
        return "validation"
    return "test"


def split_position(position: float, train_ratio: float, validation_ratio: float) -> str:
    if position < train_ratio:
        return "train"
    if position < train_ratio + validation_ratio:
        return "validation"
    return "test"


def make_sequences(
    frame: pd.DataFrame,
    sequence_length: int,
    stride: int,
    train_ratio: float,
    validation_ratio: float,
    seed: int,
) -> dict[str, Any]:
    buckets: dict[str, list[Any]] = {
        "x_train": [],
        "y_train": [],
        "meta_train": [],
        "x_validation": [],
        "y_validation": [],
        "meta_validation": [],
        "x_test": [],
        "y_test": [],
        "meta_test": [],
    }
    for group_id, group in frame.groupby("group_id", sort=False):
        group = group.sort_values("t_ms", kind="mergesort")
        values = group[FEATURE_COLUMNS].to_numpy(dtype=np.float32)
        labels = (group["label"].astype(str).to_numpy() == "fall").astype(np.float32)
        if len(values) < sequence_length:
            continue
        source_dataset = str(group["source_dataset"].iloc[0])
        source_activity = str(group["source_activity"].iloc[0])
        group_split = split_group(str(group_id), seed, train_ratio, validation_ratio)
        for end in range(sequence_length, len(values) + 1, stride):
            start = end - sequence_length
            y_value = float(labels[start:end].max())
            if source_dataset == "ICCAS":
                split = split_position(end / len(values), train_ratio, validation_ratio)
            else:
                split = group_split
            buckets[f"x_{split}"].append(values[start:end])
            buckets[f"y_{split}"].append(y_value)
            buckets[f"meta_{split}"].append(
                {
                    "source_dataset": source_dataset,
                    "source_activity": source_activity,
                    "group_id": str(group_id),
                }
            )

    out: dict[str, Any] = {}
    for split in ["train", "validation", "test"]:
        x_items = buckets[f"x_{split}"]
        y_items = buckets[f"y_{split}"]
        if not x_items:
            raise SystemExit(f"No sequences were created for {split}.")
        out[f"x_{split}"] = np.stack(x_items).astype(np.float32)
        out[f"y_{split}"] = np.array(y_items, dtype=np.float32)
        out[f"meta_{split}"] = buckets[f"meta_{split}"]
    return out


def scale_split(split: dict[str, Any]) -> tuple[dict[str, Any], RobustScaler]:
    scaler = RobustScaler.fit(split["x_train"].reshape(-1, split["x_train"].shape[-1]))
    out = dict(split)
    for key in ["x_train", "x_validation", "x_test"]:
        x = split[key]
        out[key] = scaler.transform(x.reshape(-1, x.shape[-1])).reshape(x.shape)
    return out, scaler


def metrics(y_true: np.ndarray, scores: np.ndarray, threshold: float) -> dict[str, Any]:
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
        if (current["f1"], current["recall"], current["accuracy"]) > (best_m["f1"], best_m["recall"], best_m["accuracy"]):
            best_t = float(threshold)
            best_m = current
    return best_t, best_m


def predict(model: nn.Module, x: np.ndarray, batch_size: int, device: torch.device) -> np.ndarray:
    loader = DataLoader(SequenceDataset(x, np.zeros(len(x), dtype=np.float32)), batch_size=batch_size)
    chunks: list[np.ndarray] = []
    model.eval()
    with torch.no_grad():
        for sequences, _ in loader:
            chunks.append(torch.sigmoid(model(sequences.to(device))).cpu().numpy())
    return np.concatenate(chunks)


def grouped_metrics(y_true: np.ndarray, scores: np.ndarray, meta: list[dict[str, str]], threshold: float, key: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    values = sorted({item[key] for item in meta})
    for value in values:
        idx = np.array([item[key] == value for item in meta], dtype=bool)
        out[value] = metrics(y_true[idx], scores[idx], threshold)
    return out


def resolve_device(requested: str) -> torch.device:
    if requested == "auto":
        if torch.backends.mps.is_available():
            return torch.device("mps")
        if torch.cuda.is_available():
            return torch.device("cuda")
        return torch.device("cpu")
    if requested == "mps" and not torch.backends.mps.is_available():
        print("MPS was requested but is not available in this environment. Falling back to CPU.")
        return torch.device("cpu")
    if requested == "cuda" and not torch.cuda.is_available():
        print("CUDA was requested but is not available in this environment. Falling back to CPU.")
        return torch.device("cpu")
    return torch.device(requested)


def train(args: argparse.Namespace) -> dict[str, Any]:
    set_seed(args.seed)
    frame = load_merged_csv(args.source)
    split = make_sequences(frame, args.sequence_length, args.sequence_stride, args.train_ratio, args.validation_ratio, args.seed)
    split, scaler = scale_split(split)

    device = resolve_device(args.device)
    print(f"training_device={device}")
    model = BinaryLSTM(
        len(FEATURE_COLUMNS),
        args.hidden_size,
        args.num_layers,
        args.dropout,
        args.bidirectional,
        args.pooling,
    ).to(device)
    pos = float(split["y_train"].sum())
    neg = float(len(split["y_train"]) - pos)
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([neg / max(pos, 1.0)], dtype=torch.float32, device=device))
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
        val_scores = predict(model, split["x_validation"], args.batch_size, device)
        _, val_metrics = best_threshold(split["y_validation"], val_scores)
        history.append({"epoch": epoch, "loss": total / max(1, count), "validation_f1": val_metrics["f1"]})
        print(f"merged_imu_fall epoch={epoch:03d} loss={total / max(1, count):.6f} val_f1={val_metrics['f1']:.4f}")
        if val_metrics["f1"] > best_f1:
            best_f1 = val_metrics["f1"]
            best_state = {key: value.detach().cpu() for key, value in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)

    validation_scores = predict(model, split["x_validation"], args.batch_size, device)
    threshold, validation_metrics = best_threshold(split["y_validation"], validation_scores)
    test_scores = predict(model, split["x_test"], args.batch_size, device)
    test_metrics = metrics(split["y_test"], test_scores, threshold)

    args.model_dir.mkdir(parents=True, exist_ok=True)
    model_path = args.model_dir / "iccas_sisfall_lstm_imu_fall.pt"
    metadata_path = args.model_dir / "iccas_sisfall_lstm_imu_fall.json"
    checkpoint = {
        "model_type": "iccas_sisfall_binary_imu_fall_lstm",
        "task": "imu_fall",
        "positive_label": "fall",
        "feature_columns": FEATURE_COLUMNS,
        "sequence_length": args.sequence_length,
        "threshold": threshold,
        "scaler_center": scaler.center,
        "scaler_scale": scaler.scale,
        "hidden_size": args.hidden_size,
        "num_layers": args.num_layers,
        "dropout": args.dropout,
        "bidirectional": args.bidirectional,
        "pooling": args.pooling,
        "model_state": model.state_dict(),
    }
    torch.save(checkpoint, model_path)

    metadata = {key: value for key, value in checkpoint.items() if key not in {"model_state", "scaler_center", "scaler_scale"}}
    metadata["scaler_center"] = scaler.center.tolist()
    metadata["scaler_scale"] = scaler.scale.tolist()
    metadata["source"] = str(args.source)
    metadata["device"] = str(device)
    metadata["split_method"] = "SisFall source-file/group hash split; ICCAS chronological split inside each scenario"
    metadata["split_sizes"] = {
        "train": int(len(split["y_train"])),
        "validation": int(len(split["y_validation"])),
        "test": int(len(split["y_test"])),
    }
    metadata["label_counts"] = {
        "train_positive": int(split["y_train"].sum()),
        "train_negative": int(len(split["y_train"]) - split["y_train"].sum()),
        "validation_positive": int(split["y_validation"].sum()),
        "validation_negative": int(len(split["y_validation"]) - split["y_validation"].sum()),
        "test_positive": int(split["y_test"].sum()),
        "test_negative": int(len(split["y_test"]) - split["y_test"].sum()),
    }
    metadata["validation_metrics"] = validation_metrics
    metadata["test_metrics"] = test_metrics
    metadata["test_metrics_by_dataset"] = grouped_metrics(
        split["y_test"], test_scores, split["meta_test"], threshold, "source_dataset"
    )
    metadata["test_metrics_by_activity"] = grouped_metrics(
        split["y_test"], test_scores, split["meta_test"], threshold, "source_activity"
    )
    metadata["history"] = history
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "source": str(args.source),
        "model_path": str(model_path),
        "metadata_path": str(metadata_path),
        "report_path": str(args.report),
        "validation_metrics": validation_metrics,
        "test_metrics": test_metrics,
        "test_metrics_by_dataset": metadata["test_metrics_by_dataset"],
        "split_sizes": metadata["split_sizes"],
        "label_counts": metadata["label_counts"],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/iccas_sensor_lstm/iccas_sisfall_imu_merged.csv"))
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--report", type=Path, default=Path("data/iccas_sensor_lstm/sisfall_merged_imu_lstm_metrics.json"))
    parser.add_argument("--sequence-length", type=int, default=32)
    parser.add_argument("--sequence-stride", type=int, default=4)
    parser.add_argument("--hidden-size", type=int, default=96)
    parser.add_argument("--num-layers", type=int, default=2)
    parser.add_argument("--dropout", type=float, default=0.25)
    parser.add_argument("--epochs", type=int, default=35)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--train-ratio", type=float, default=0.70)
    parser.add_argument("--validation-ratio", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    parser.add_argument("--bidirectional", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--pooling", choices=["last", "mean", "attention"], default="attention")
    return parser.parse_args()


def main() -> None:
    report = train(parse_args())
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
