"""Split ICCAS GPS + IMU rows into rule-based situation candidates.

This script does not create ground-truth labels. It creates candidate
situations from sensor features and optional LSTM replay predictions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from realtime_sensor_lstm import build_features, read_source


def bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    return frame[column].astype(str).str.lower().isin({"true", "1", "yes"})


def load_predictions(path: Path | None, row_count: int) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame(index=range(row_count))
    predictions = pd.read_csv(path)
    if len(predictions) != row_count:
        raise SystemExit(
            f"Prediction row count mismatch: source={row_count}, predictions={len(predictions)}"
        )
    return predictions.reset_index(drop=True)


def classify_points(featured: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    result = featured.reset_index(drop=True).copy()
    result["accel_delta_from_1g"] = (result["accel_norm"] - 1.0).abs()

    if not predictions.empty:
        for column in [
            "ready",
            "predicted_abnormal",
            "wandering_detected",
            "fall_detected",
            "alarm_active",
            "anomaly_score",
            "prediction_error",
            "detection_type",
            "risk_level",
        ]:
            if column in predictions.columns:
                result[column] = predictions[column]

    fall_detected = bool_series(result, "fall_detected")
    wandering_detected = bool_series(result, "wandering_detected")
    alarm_active = bool_series(result, "alarm_active")
    predicted_abnormal = bool_series(result, "predicted_abnormal")

    impact_like_motion = (result["accel_norm"] >= 2.5) | (result["gyro_norm"] >= 250.0)
    time_gap = result["dt_s"] >= 5.0
    stationary = (
        (result["speed_mps"] < 0.20)
        & (result["gyro_norm"] < 18.0)
        & (result["accel_delta_from_1g"] < 0.08)
    )
    movement = (
        (result["speed_mps"] >= 0.20)
        | (result["gyro_norm"] >= 18.0)
        | (result["accel_delta_from_1g"] >= 0.08)
    )

    choices = [
        "fall_candidate",
        "wandering_candidate",
        "ai_anomaly_candidate",
        "recording_gap",
        "stationary_or_waiting",
        "walking_or_movement",
    ]
    conditions = [
        fall_detected | impact_like_motion,
        wandering_detected,
        alarm_active | predicted_abnormal,
        time_gap,
        stationary,
        movement,
    ]
    result["situation"] = np.select(conditions, choices, default="normal_walk")
    result["situation_reason"] = np.select(
        conditions,
        [
            "fall_detected or accel_norm>=2.5 or gyro_norm>=250",
            "wandering_detected by LSTM route rule",
            "alarm_active or predicted_abnormal from LSTM replay",
            "dt_s>=5.0 seconds",
            "low speed, low gyro, accel close to 1g",
            "GPS speed, gyro, or acceleration changed",
        ],
        default="baseline normal movement",
    )
    return result


def build_segments(points: pd.DataFrame) -> pd.DataFrame:
    segment_break = (
        (points["device"] != points["device"].shift(1))
        | (points["situation"] != points["situation"].shift(1))
        | (points["dt_s"] >= 5.0)
    )
    points["segment_id"] = segment_break.cumsum().astype(int)

    aggregations: dict[str, Any] = {
        "device": "first",
        "situation": "first",
        "situation_reason": "first",
        "server_time": ["min", "max", "count"],
        "dt_s": "sum",
        "lat": ["first", "last"],
        "lng": ["first", "last"],
        "speed_mps": "max",
        "accel_norm": "max",
        "gyro_norm": "max",
    }
    for column in ["anomaly_score", "prediction_error"]:
        if column in points.columns:
            aggregations[column] = "max"
    for column in ["alarm_active", "fall_detected", "wandering_detected"]:
        if column in points.columns:
            points[column] = bool_series(points, column)
            aggregations[column] = "sum"

    segments = points.groupby("segment_id", as_index=True).agg(aggregations)
    segments.columns = [
        "_".join(part for part in column if part)
        if isinstance(column, tuple)
        else str(column)
        for column in segments.columns
    ]
    segments = segments.rename(
        columns={
            "device_first": "device",
            "situation_first": "situation",
            "situation_reason_first": "situation_reason",
            "server_time_min": "start_time",
            "server_time_max": "end_time",
            "server_time_count": "points",
            "dt_s_sum": "duration_s",
            "lat_first": "start_lat",
            "lat_last": "end_lat",
            "lng_first": "start_lng",
            "lng_last": "end_lng",
            "speed_mps_max": "max_speed_mps",
            "accel_norm_max": "max_accel_norm",
            "gyro_norm_max": "max_gyro_norm",
            "anomaly_score_max": "max_anomaly_score",
            "prediction_error_max": "max_prediction_error",
            "alarm_active_sum": "alarm_points",
            "fall_detected_sum": "fall_points",
            "wandering_detected_sum": "wandering_points",
        }
    )
    return segments.reset_index()


def write_summary(points: pd.DataFrame, segments: pd.DataFrame, output: Path) -> None:
    situation_counts = points["situation"].value_counts().to_dict()
    segment_counts = segments["situation"].value_counts().to_dict()
    summary = {
        "points": int(len(points)),
        "segments": int(len(segments)),
        "time_start": str(points["server_time"].min()),
        "time_end": str(points["server_time"].max()),
        "situation_point_counts": {key: int(value) for key, value in situation_counts.items()},
        "situation_segment_counts": {key: int(value) for key, value in segment_counts.items()},
        "note": "These are rule-based situation candidates, not human-verified ground-truth labels.",
    }
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sheet", default="data")
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--points-output", type=Path, required=True)
    parser.add_argument("--segments-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = read_source(args.source, sheet_name=args.sheet)
    featured, _, _ = build_features(frame)
    predictions = load_predictions(args.predictions, len(featured))
    points = classify_points(featured, predictions)
    segments = build_segments(points)

    args.points_output.parent.mkdir(parents=True, exist_ok=True)
    points.to_csv(args.points_output, index=False)
    segments.to_csv(args.segments_output, index=False)
    write_summary(points, segments, args.summary_output)

    print(
        json.dumps(
            {
                "points_output": str(args.points_output),
                "segments_output": str(args.segments_output),
                "summary_output": str(args.summary_output),
                "points": len(points),
                "segments": len(segments),
                "situations": points["situation"].value_counts().to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
