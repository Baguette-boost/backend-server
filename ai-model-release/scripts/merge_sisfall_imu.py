"""Convert SisFall raw TXT files and merge them with ICCAS IMU fall data."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


SISFALL_PATTERN = re.compile(r"^(?P<activity>[FD]\d{2})_(?P<subject>S[AE]\d{2})_R(?P<trial>\d{2})\.txt$")
OUTPUT_COLUMNS = [
    "server_time",
    "device",
    "label",
    "t_ms",
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
    "source_dataset",
    "source_file",
    "source_subject",
    "source_activity",
    "source_trial",
]


def sisfall_label(activity: str) -> str:
    return "fall" if activity.startswith("F") else "normal"


def iter_sisfall_files(root: Path) -> list[Path]:
    files = []
    for path in root.glob("*/*.txt"):
        if path.name.startswith("._"):
            continue
        if SISFALL_PATTERN.match(path.name):
            files.append(path)
    return sorted(files)


def balanced_files(files: list[Path], max_files_per_label: int) -> list[Path]:
    buckets = {"fall": [], "normal": []}
    for path in files:
        match = SISFALL_PATTERN.match(path.name)
        if not match:
            continue
        buckets[sisfall_label(match.group("activity"))].append(path)
    if max_files_per_label <= 0:
        return buckets["fall"] + buckets["normal"]
    return buckets["fall"][:max_files_per_label] + buckets["normal"][:max_files_per_label]


def parse_sisfall_file(
    path: Path,
    file_index: int,
    sample_rate_hz: float,
    stride: int,
    max_rows_per_file: int,
) -> list[dict[str, object]]:
    match = SISFALL_PATTERN.match(path.name)
    if not match:
        return []
    activity = match.group("activity")
    subject = match.group("subject")
    trial = match.group("trial")
    label = sisfall_label(activity)
    rows: list[dict[str, object]] = []
    base_time = datetime(2026, 1, 1) + timedelta(minutes=file_index)

    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        kept = 0
        for raw_index, line in enumerate(handle):
            if raw_index % stride != 0:
                continue
            if max_rows_per_file > 0 and kept >= max_rows_per_file:
                break
            cleaned = line.strip().rstrip(";")
            if not cleaned:
                continue
            parts = [part.strip() for part in cleaned.split(",")]
            if len(parts) < 6:
                continue
            try:
                values = [float(part) for part in parts[:6]]
            except ValueError:
                continue

            # SisFall original format:
            # col 1-3: ADXL345 accelerometer, approx 3.90625 mg/LSB
            # col 4-6: ITG3200 gyroscope, approx 14.375 LSB/(deg/s)
            ax, ay, az = [value * 0.00390625 for value in values[:3]]
            wx, wy, wz = [value / 14.375 for value in values[3:6]]
            t_ms = int((raw_index / sample_rate_hz) * 1000)
            rows.append(
                {
                    "server_time": (base_time + timedelta(milliseconds=t_ms)).isoformat(sep=" "),
                    "device": f"sisfall-{subject}",
                    "label": label,
                    "t_ms": t_ms,
                    "roll": 0.0,
                    "pitch": 0.0,
                    "yaw": 0.0,
                    "ax": ax,
                    "ay": ay,
                    "az": az,
                    "wx": wx,
                    "wy": wy,
                    "wz": wz,
                    "lat": None,
                    "lng": None,
                    "source_dataset": "SisFall",
                    "source_file": str(path),
                    "source_subject": subject,
                    "source_activity": activity,
                    "source_trial": trial,
                }
            )
            kept += 1
    return rows


def convert_sisfall(args: argparse.Namespace) -> pd.DataFrame:
    files = balanced_files(iter_sisfall_files(args.sisfall_dir), args.max_files_per_label)
    frames: list[pd.DataFrame] = []
    for index, path in enumerate(files):
        rows = parse_sisfall_file(
            path,
            file_index=index,
            sample_rate_hz=args.sample_rate_hz,
            stride=args.stride,
            max_rows_per_file=args.max_rows_per_file,
        )
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        raise SystemExit("No SisFall rows were converted.")
    return pd.concat(frames, ignore_index=True)


def load_iccas_imu(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame = frame.copy()
    if "class_label" in frame.columns and "label" not in frame.columns:
        frame["label"] = frame["class_label"]
    frame["source_dataset"] = "ICCAS"
    frame["source_file"] = str(path)
    frame["source_subject"] = "local"
    frame["source_activity"] = frame["label"]
    frame["source_trial"] = "local"
    for column in OUTPUT_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[OUTPUT_COLUMNS]


def write_summary(sisfall: pd.DataFrame, merged: pd.DataFrame, output: Path, args: argparse.Namespace) -> None:
    summary = {
        "sisfall_dir": str(args.sisfall_dir),
        "iccas_source": str(args.iccas_source),
        "sample_rate_hz": args.sample_rate_hz,
        "stride": args.stride,
        "max_files_per_label": args.max_files_per_label,
        "max_rows_per_file": args.max_rows_per_file,
        "sisfall_rows": int(len(sisfall)),
        "merged_rows": int(len(merged)),
        "sisfall_label_counts": {str(k): int(v) for k, v in sisfall["label"].value_counts().to_dict().items()},
        "merged_label_counts": {str(k): int(v) for k, v in merged["label"].value_counts().to_dict().items()},
        "note": "SisFall is merged only for IMU/Gyro fall training. It does not contain GPS, so it should not be used for GPS wandering training.",
    }
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sisfall-dir", type=Path, default=Path("SisFall_dataset"))
    parser.add_argument("--iccas-source", type=Path, default=Path("data/iccas_sensor_lstm/iccas_dataV1_labeled.csv"))
    parser.add_argument("--sisfall-output", type=Path, required=True)
    parser.add_argument("--merged-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument("--sample-rate-hz", type=float, default=200.0)
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--max-files-per-label", type=int, default=200)
    parser.add_argument("--max-rows-per-file", type=int, default=3000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sisfall = convert_sisfall(args)
    iccas = load_iccas_imu(args.iccas_source)
    merged = pd.concat([iccas, sisfall[OUTPUT_COLUMNS]], ignore_index=True)

    args.sisfall_output.parent.mkdir(parents=True, exist_ok=True)
    sisfall.to_csv(args.sisfall_output, index=False)
    merged.to_csv(args.merged_output, index=False)
    write_summary(sisfall, merged, args.summary_output, args)

    print(
        json.dumps(
            {
                "sisfall_output": str(args.sisfall_output),
                "merged_output": str(args.merged_output),
                "summary_output": str(args.summary_output),
                "sisfall_rows": len(sisfall),
                "merged_rows": len(merged),
                "sisfall_labels": sisfall["label"].value_counts().to_dict(),
                "merged_labels": merged["label"].value_counts().to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
