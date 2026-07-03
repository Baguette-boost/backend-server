"""Prepare ICCAS_dataV1.xlsx sheets as one labeled LSTM dataset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from realtime_sensor_lstm import FEATURE_COLUMNS, build_features, read_source


SHEET_LABELS = {
    "추가된 학교 데이터": "walk",
    "집 앞 walk": "walk",
    "학교 walk": "walk",
    "새로운 경로에서 배회": "wandering",
    "떨어졌다가 앞으로 걸어가고 떨어졌다 앞으로 걸어감": "fall",
    "새로운 경로 1.2km, 새로운 경로 600m": "wandering",
    "제자리에서 떨어짐1,2": "fall",
    "sit": "sit",
    "idle": "idle",
}


def load_labeled_sheets(source: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for sheet_name, target_label in SHEET_LABELS.items():
        frame = read_source(source, sheet_name=sheet_name)
        frame = frame.copy()
        frame["source_sheet"] = sheet_name
        frame["original_label"] = frame["label"]
        frame["label"] = target_label
        frames.append(frame)
    if not frames:
        raise SystemExit("No sheets were loaded.")
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["device", "server_time", "source_sheet"]).reset_index(drop=True)
    return combined


def split_by_label(frame: pd.DataFrame, output_dir: Path) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for label, group in frame.groupby("label", sort=True):
        path = output_dir / f"iccas_dataV1_{label}.csv"
        group.to_csv(path, index=False)
        counts[str(label)] = int(len(group))
    return counts


def write_summary(raw: pd.DataFrame, featured: pd.DataFrame, output: Path) -> None:
    by_sheet = (
        raw.groupby(["source_sheet", "label"], sort=False)
        .size()
        .reset_index(name="rows")
        .to_dict(orient="records")
    )
    by_label = raw["label"].value_counts().sort_index().to_dict()
    summary = {
        "source_rows": int(len(raw)),
        "preprocessed_rows": int(len(featured)),
        "time_start": str(raw["server_time"].min()),
        "time_end": str(raw["server_time"].max()),
        "labels": {str(key): int(value) for key, value in by_label.items()},
        "sheets": by_sheet,
        "feature_columns": FEATURE_COLUMNS,
        "normal_labels_for_lstm_training": ["walk", "sit", "idle"],
        "abnormal_or_event_labels": ["wandering", "fall"],
        "note": "Wandering labels are assigned from sheet names because some original sheet labels are stored as walk.",
    }
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("ICCAS_dataV1.xlsx"))
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--features-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument("--split-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.source.exists():
        raise SystemExit(f"Input file not found: {args.source}")

    raw = load_labeled_sheets(args.source)
    featured, origin_lat, origin_lng = build_features(raw)

    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.features_output.parent.mkdir(parents=True, exist_ok=True)
    raw.to_csv(args.raw_output, index=False)
    featured.to_csv(args.features_output, index=False)
    write_summary(raw, featured, args.summary_output)

    split_counts = split_by_label(raw, args.split_dir) if args.split_dir else {}
    result = {
        "raw_output": str(args.raw_output),
        "features_output": str(args.features_output),
        "summary_output": str(args.summary_output),
        "split_dir": str(args.split_dir) if args.split_dir else "",
        "source_rows": len(raw),
        "preprocessed_rows": len(featured),
        "origin_lat": origin_lat,
        "origin_lng": origin_lng,
        "labels": raw["label"].value_counts().sort_index().to_dict(),
        "split_counts": split_counts,
    }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
