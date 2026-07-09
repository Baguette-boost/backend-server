"""Replay 피더 — 저장된 xlsx 를 '기기'처럼 재생해 백엔드 /telemetry 로 원본 데이터를 보낸다.

B(pull) 파이프라인 검증용. 기존 push replay(realtime_sensor_lstm.py replay)를 대체한다.
- 매 행의 GPS 를 POST /telemetry/gps 로 스트리밍
- 낙상 라벨 구간의 상승엣지에서, 직전 IMU 윈도우를 POST /telemetry/fall-suspect 로 전송
  (기기가 자체 판단으로 '낙상 의심'을 올리는 상황을 모사)

백엔드가 이 원본을 받아 AI 컨테이너 /predict 를 호출 → 낙상 판정 → 에피소드/알림.

사용 예:
  python3 feed_replay.py \
    --source ICCAS_total_data_with_fall.xlsx --sheet data \
    --backend-url http://baguetteboost-backend:8000 \
    --person-id 2 --device-token replay-feeder --window 20 --sleep 0.02
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone

import pandas as pd

IMU_FIELDS = ["roll", "pitch", "yaw", "ax", "ay", "az", "wx", "wy", "wz"]


def load_rows(source: str, sheet: str) -> pd.DataFrame:
    if source.lower().endswith(".csv"):
        frame = pd.read_csv(source)
    else:
        frame = pd.read_excel(source, sheet_name=sheet)
    frame = frame.rename(columns={"latitude": "lat", "longitude": "lng", "timestamp": "server_time"})
    frame["server_time"] = pd.to_datetime(frame["server_time"], errors="coerce")
    frame = frame.dropna(subset=["server_time", *IMU_FIELDS]).sort_values("server_time").reset_index(drop=True)
    return frame


def post(url: str, payload: dict, token: str, timeout: float = 5.0) -> tuple[int, str]:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Device {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--sheet", default="data")
    ap.add_argument("--backend-url", default="http://baguetteboost-backend:8000")
    ap.add_argument("--person-id", type=int, required=True)
    ap.add_argument("--device-token", default="replay-feeder")
    ap.add_argument("--window", type=int, default=20, help="fall-suspect 로 보낼 IMU 표본 수(>= 모델 seq)")
    ap.add_argument("--sleep", type=float, default=0.02)
    ap.add_argument("--gps-every", type=int, default=1, help="N행마다 GPS 전송(다운샘플)")
    # 실제 기기처럼 IMU 임팩트(가속/자이로 급변)에서 fall-suspect 를 올린다.
    # (realtime_sensor_lstm 의 impact_like_motion 휴리스틱과 동일한 임계치)
    ap.add_argument("--accel-impact", type=float, default=2.5)
    ap.add_argument("--gyro-impact", type=float, default=250.0)
    ap.add_argument("--cooldown", type=int, default=40, help="fall-suspect 재전송 억제 표본 수")
    # live 모드: 각 표본 타임스탬프를 현재(wall-clock UTC)로 재기록해 실기기처럼 보낸다.
    # (replay 는 xlsx 의 과거 시각을 그대로 써서 낙상 에피소드가 즉시 만료됨)
    ap.add_argument("--live", action="store_true", help="현재 시각으로 스트리밍(에피소드 유지)")
    args = ap.parse_args()

    frame = load_rows(args.source, args.sheet)
    gps_url = f"{args.backend_url}/telemetry/gps"
    fall_url = f"{args.backend_url}/telemetry/fall-suspect"
    has_gps = {"lat", "lng"}.issubset(frame.columns)

    buf: deque[dict] = deque(maxlen=args.window)
    cooldown = 0
    sent_gps = sent_fall = 0

    print(f"[feed] rows={len(frame)} person_id={args.person_id} window={args.window} "
          f"mode={'LIVE(now)' if args.live else 'replay(xlsx-time)'} "
          f"gps={'yes' if has_gps else 'no'} impact(accel>={args.accel_impact},gyro>={args.gyro_impact})", flush=True)

    for i, row in enumerate(frame.itertuples(index=False)):
        # live 면 현재 UTC, 아니면 xlsx 원본 시각
        ts = (datetime.now(timezone.utc).isoformat() if args.live
              else pd.Timestamp(row.server_time).isoformat())
        sample = {f: float(getattr(row, f)) for f in IMU_FIELDS}
        buf.append(sample)
        if cooldown > 0:
            cooldown -= 1

        # 1) GPS 스트리밍
        if has_gps and (i % args.gps_every == 0):
            lat, lng = getattr(row, "lat", None), getattr(row, "lng", None)
            if pd.notna(lat) and pd.notna(lng):
                code, _ = post(gps_url, {
                    "personId": args.person_id,
                    "gps": {"timestamp": ts, "latitude": float(lat), "longitude": float(lng)},
                }, args.device_token)
                sent_gps += 1
                if code != 200:
                    print(f"[gps] HTTP {code} @row {i}", flush=True)

        # 2) IMU 임팩트 감지(기기 자체 판단 모사) → 직전 윈도우를 fall-suspect 로 전송
        accel_norm = (sample["ax"] ** 2 + sample["ay"] ** 2 + sample["az"] ** 2) ** 0.5
        gyro_norm = (sample["wx"] ** 2 + sample["wy"] ** 2 + sample["wz"] ** 2) ** 0.5
        impact = accel_norm >= args.accel_impact or gyro_norm >= args.gyro_impact
        if impact and cooldown == 0 and len(buf) >= args.window:
            imu = {f: [p[f] for p in buf] for f in IMU_FIELDS}
            code, resp = post(fall_url, {
                "personId": args.person_id, "timestamp": ts, "imuData": imu,
            }, args.device_token)
            sent_fall += 1
            cooldown = args.cooldown
            print(f"[fall-suspect] impact@row {i} ts={ts} accel={accel_norm:.1f} gyro={gyro_norm:.0f} "
                  f"-> HTTP {code} {resp[:120]}", flush=True)

        if args.sleep > 0:
            time.sleep(args.sleep)

    print(f"[feed] done. gps_sent={sent_gps} fall_suspect_sent={sent_fall}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
