"""Generate a self-contained LSTM prediction simulation HTML file."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def to_bool(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def to_float(value: str) -> float | None:
    try:
        if value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def load_points(path: Path) -> list[dict[str, object]]:
    points: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            lat = to_float(row.get("lat", ""))
            lng = to_float(row.get("lng", ""))
            if lat is None or lng is None:
                continue
            points.append(
                {
                    "t": row.get("timestamp", ""),
                    "label": row.get("label", ""),
                    "lat": lat,
                    "lng": lng,
                    "ready": to_bool(row.get("ready", "")),
                    "alarm": to_bool(row.get("alarm_active", "")),
                    "wandering": to_bool(row.get("wandering_detected", "")),
                    "fall": to_bool(row.get("fall_detected", "")),
                    "score": to_float(row.get("anomaly_score", "")),
                    "error": to_float(row.get("prediction_error", "")),
                    "type": row.get("detection_type", "normal") or "normal",
                    "risk": row.get("risk_level", "low") or "low",
                }
            )
    return points


def build_html(points: list[dict[str, object]], source_name: str) -> str:
    payload = json.dumps(points, ensure_ascii=False, separators=(",", ":"))
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ICCAS LSTM 실시간 예측 시뮬레이션</title>
  <style>
    :root {{
      --bg: #f7f7f4;
      --panel: #ffffff;
      --ink: #171717;
      --muted: #666b72;
      --line: #d8d8d2;
      --normal: #2f6f73;
      --walk: #2f6f73;
      --idle: #77736b;
      --sit: #8a6f2a;
      --wandering: #c77900;
      --fall: #c73d3d;
      --alarm: #9f3f8f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    main {{
      min-height: 100vh;
      display: grid;
      grid-template-columns: minmax(360px, 1fr) 380px;
      gap: 0;
    }}
    .stage {{
      padding: 18px;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 12px;
      min-width: 0;
    }}
    header {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
    }}
    h1 {{
      margin: 0;
      font-size: 24px;
      line-height: 1.15;
      letter-spacing: 0;
    }}
    .source {{
      margin-top: 4px;
      color: var(--muted);
      font-size: 13px;
    }}
    .badges {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .badge {{
      border: 1px solid var(--line);
      background: #fff;
      padding: 6px 9px;
      border-radius: 6px;
      font-size: 12px;
      color: #30343a;
      white-space: nowrap;
    }}
    .map-wrap {{
      position: relative;
      min-height: 540px;
      border: 1px solid var(--line);
      background:
        linear-gradient(90deg, rgba(0,0,0,.04) 1px, transparent 1px),
        linear-gradient(rgba(0,0,0,.04) 1px, transparent 1px),
        #eef1ec;
      background-size: 44px 44px;
      border-radius: 8px;
      overflow: hidden;
    }}
    canvas {{
      width: 100%;
      height: 100%;
      display: block;
      position: absolute;
      inset: 0;
    }}
    .status-card {{
      position: absolute;
      left: 14px;
      top: 14px;
      width: min(360px, calc(100% - 28px));
      background: rgba(255,255,255,.94);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      box-shadow: 0 12px 30px rgba(0,0,0,.08);
    }}
    .status-top {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 8px;
    }}
    .status-label {{
      font-weight: 700;
      font-size: 18px;
    }}
    .risk {{
      font-size: 12px;
      padding: 5px 8px;
      border-radius: 6px;
      background: #edf2ef;
      border: 1px solid var(--line);
    }}
    .status-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      font-size: 12px;
      color: var(--muted);
    }}
    .status-grid b {{
      display: block;
      margin-top: 2px;
      color: var(--ink);
      font-size: 14px;
      font-weight: 650;
      overflow-wrap: anywhere;
    }}
    .controls {{
      display: grid;
      grid-template-columns: auto auto 1fr auto;
      gap: 10px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 10px;
    }}
    button, select {{
      height: 36px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 6px;
      font-size: 14px;
      padding: 0 10px;
    }}
    button {{ cursor: pointer; font-weight: 650; }}
    input[type="range"] {{ width: 100%; }}
    aside {{
      border-left: 1px solid var(--line);
      background: #fff;
      padding: 18px;
      overflow: auto;
    }}
    .panel {{
      border-bottom: 1px solid var(--line);
      padding: 0 0 16px;
      margin-bottom: 16px;
    }}
    .panel h2 {{
      margin: 0 0 10px;
      font-size: 15px;
      letter-spacing: 0;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fbfbfa;
    }}
    .metric span {{
      color: var(--muted);
      font-size: 12px;
    }}
    .metric strong {{
      display: block;
      margin-top: 3px;
      font-size: 20px;
      line-height: 1.1;
    }}
    .legend {{
      display: grid;
      gap: 8px;
      font-size: 13px;
    }}
    .legend-row {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .dot {{
      width: 11px;
      height: 11px;
      border-radius: 999px;
      display: inline-block;
    }}
    .events {{
      display: grid;
      gap: 8px;
      max-height: 420px;
      overflow: auto;
    }}
    .event {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px;
      font-size: 12px;
      cursor: pointer;
      background: #fff;
    }}
    .event strong {{
      display: block;
      font-size: 13px;
      margin-bottom: 2px;
    }}
    .event:hover {{ border-color: #9b9890; }}
    @media (max-width: 980px) {{
      main {{ grid-template-columns: 1fr; }}
      aside {{ border-left: 0; border-top: 1px solid var(--line); }}
      .map-wrap {{ min-height: 520px; }}
    }}
    @media (max-width: 620px) {{
      .stage, aside {{ padding: 12px; }}
      header {{ display: block; }}
      .badges {{ justify-content: flex-start; margin-top: 10px; }}
      .controls {{ grid-template-columns: 1fr 1fr; }}
      .controls input {{ grid-column: 1 / -1; }}
      .status-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<main>
  <section class="stage">
    <header>
      <div>
        <h1>ICCAS LSTM 실시간 예측 시뮬레이션</h1>
        <div class="source">{source_name}</div>
      </div>
      <div class="badges">
        <span class="badge" id="totalBadge">0 points</span>
        <span class="badge" id="timeBadge">-</span>
      </div>
    </header>
    <div class="map-wrap" id="mapWrap">
      <canvas id="map"></canvas>
      <div class="status-card">
        <div class="status-top">
          <div class="status-label" id="stateText">준비 중</div>
          <div class="risk" id="riskText">risk -</div>
        </div>
        <div class="status-grid">
          <div>시간<b id="timestampText">-</b></div>
          <div>라벨<b id="labelText">-</b></div>
          <div>좌표<b id="coordText">-</b></div>
          <div>anomaly score<b id="scoreText">-</b></div>
        </div>
      </div>
    </div>
    <div class="controls">
      <button id="playBtn">재생</button>
      <button id="resetBtn">처음</button>
      <input id="slider" type="range" min="0" max="0" value="0">
      <select id="speed">
        <option value="1">1x</option>
        <option value="4" selected>4x</option>
        <option value="12">12x</option>
        <option value="40">40x</option>
      </select>
    </div>
  </section>
  <aside>
    <div class="panel">
      <h2>요약</h2>
      <div class="metrics">
        <div class="metric"><span>전체</span><strong id="mTotal">0</strong></div>
        <div class="metric"><span>ready</span><strong id="mReady">0</strong></div>
        <div class="metric"><span>alarm</span><strong id="mAlarm">0</strong></div>
        <div class="metric"><span>wandering</span><strong id="mWandering">0</strong></div>
        <div class="metric"><span>fall</span><strong id="mFall">0</strong></div>
        <div class="metric"><span>현재 index</span><strong id="mIndex">0</strong></div>
      </div>
    </div>
    <div class="panel">
      <h2>범례</h2>
      <div class="legend">
        <div class="legend-row"><span class="dot" style="background:var(--walk)"></span>정상/보행</div>
        <div class="legend-row"><span class="dot" style="background:var(--wandering)"></span>배회 감지</div>
        <div class="legend-row"><span class="dot" style="background:var(--fall)"></span>낙상 감지</div>
        <div class="legend-row"><span class="dot" style="background:var(--alarm)"></span>LSTM 이상 알람</div>
        <div class="legend-row"><span class="dot" style="background:var(--idle)"></span>sit / idle</div>
      </div>
    </div>
    <div class="panel">
      <h2>감지 이벤트</h2>
      <div class="events" id="events"></div>
    </div>
  </aside>
</main>
<script>
const points = {payload};
const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");
const wrap = document.getElementById("mapWrap");
const playBtn = document.getElementById("playBtn");
const resetBtn = document.getElementById("resetBtn");
const slider = document.getElementById("slider");
const speed = document.getElementById("speed");
const stateText = document.getElementById("stateText");
const riskText = document.getElementById("riskText");
const timestampText = document.getElementById("timestampText");
const labelText = document.getElementById("labelText");
const coordText = document.getElementById("coordText");
const scoreText = document.getElementById("scoreText");
const totalBadge = document.getElementById("totalBadge");
const timeBadge = document.getElementById("timeBadge");
const eventsEl = document.getElementById("events");
const metrics = {{
  total: document.getElementById("mTotal"),
  ready: document.getElementById("mReady"),
  alarm: document.getElementById("mAlarm"),
  wandering: document.getElementById("mWandering"),
  fall: document.getElementById("mFall"),
  index: document.getElementById("mIndex")
}};

let index = 0;
let playing = false;
let timer = null;
let bounds;

function computeBounds() {{
  const lats = points.map(p => p.lat);
  const lngs = points.map(p => p.lng);
  bounds = {{
    minLat: Math.min(...lats),
    maxLat: Math.max(...lats),
    minLng: Math.min(...lngs),
    maxLng: Math.max(...lngs)
  }};
  if (bounds.minLat === bounds.maxLat) bounds.maxLat += 0.0001;
  if (bounds.minLng === bounds.maxLng) bounds.maxLng += 0.0001;
}}

function colorFor(p) {{
  if (p.fall) return "#c73d3d";
  if (p.wandering) return "#c77900";
  if (p.alarm) return "#9f3f8f";
  if (p.label === "idle") return "#77736b";
  if (p.label === "sit") return "#8a6f2a";
  return "#2f6f73";
}}

function stateFor(p) {{
  if (p.fall) return "낙상 감지";
  if (p.wandering) return "배회 감지";
  if (p.alarm) return "LSTM 이상 알람";
  if (!p.ready) return "시퀀스 준비 중";
  if (p.label === "sit") return "앉음";
  if (p.label === "idle") return "대기";
  return "정상 이동";
}}

function project(p) {{
  const pad = 44;
  const w = canvas.width - pad * 2;
  const h = canvas.height - pad * 2;
  const x = pad + ((p.lng - bounds.minLng) / (bounds.maxLng - bounds.minLng)) * w;
  const y = pad + (1 - ((p.lat - bounds.minLat) / (bounds.maxLat - bounds.minLat))) * h;
  return [x, y];
}}

function resize() {{
  const rect = wrap.getBoundingClientRect();
  canvas.width = Math.max(600, Math.floor(rect.width * window.devicePixelRatio));
  canvas.height = Math.max(420, Math.floor(rect.height * window.devicePixelRatio));
  draw();
}}

function drawPath(until) {{
  ctx.lineWidth = 2.2 * window.devicePixelRatio;
  ctx.lineCap = "round";
  for (let i = 1; i <= until; i++) {{
    const a = points[i - 1];
    const b = points[i];
    const [x1, y1] = project(a);
    const [x2, y2] = project(b);
    ctx.strokeStyle = colorFor(b);
    ctx.globalAlpha = b.ready ? 0.72 : 0.28;
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
  }}
  ctx.globalAlpha = 1;
}}

function drawPoints(until) {{
  const step = Math.max(1, Math.floor(points.length / 900));
  for (let i = 0; i <= until; i += step) {{
    const p = points[i];
    const [x, y] = project(p);
    ctx.fillStyle = colorFor(p);
    ctx.globalAlpha = p.alarm || p.wandering || p.fall ? 0.95 : 0.35;
    ctx.beginPath();
    ctx.arc(x, y, (p.alarm || p.wandering || p.fall ? 3.2 : 1.8) * window.devicePixelRatio, 0, Math.PI * 2);
    ctx.fill();
  }}
  ctx.globalAlpha = 1;
}}

function drawCurrent(p) {{
  const [x, y] = project(p);
  ctx.fillStyle = colorFor(p);
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 4 * window.devicePixelRatio;
  ctx.beginPath();
  ctx.arc(x, y, 9 * window.devicePixelRatio, 0, Math.PI * 2);
  ctx.stroke();
  ctx.fill();
  if (p.alarm || p.wandering || p.fall) {{
    ctx.strokeStyle = colorFor(p);
    ctx.lineWidth = 2 * window.devicePixelRatio;
    ctx.globalAlpha = 0.32;
    ctx.beginPath();
    ctx.arc(x, y, 22 * window.devicePixelRatio, 0, Math.PI * 2);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }}
}}

function draw() {{
  if (!points.length) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawPath(index);
  drawPoints(index);
  drawCurrent(points[index]);
}}

function updateText() {{
  const p = points[index];
  stateText.textContent = stateFor(p);
  riskText.textContent = `risk ${{p.risk || "-"}}`;
  timestampText.textContent = p.t || "-";
  labelText.textContent = p.label || "-";
  coordText.textContent = `${{p.lat.toFixed(6)}}, ${{p.lng.toFixed(6)}}`;
  scoreText.textContent = p.score == null ? "-" : p.score.toFixed(4);
  timeBadge.textContent = p.t || "-";
  metrics.index.textContent = `${{index + 1}} / ${{points.length}}`;
  slider.value = String(index);
}}

function update() {{
  draw();
  updateText();
}}

function tick() {{
  const jump = Number(speed.value);
  index = Math.min(points.length - 1, index + jump);
  update();
  if (index >= points.length - 1) stop();
}}

function play() {{
  if (playing) return;
  playing = true;
  playBtn.textContent = "일시정지";
  timer = setInterval(tick, 80);
}}

function stop() {{
  playing = false;
  playBtn.textContent = "재생";
  if (timer) clearInterval(timer);
  timer = null;
}}

function setupEvents() {{
  const events = points
    .map((p, i) => [p, i])
    .filter(([p]) => p.fall || p.wandering || p.alarm)
    .slice(0, 160);
  eventsEl.innerHTML = "";
  if (!events.length) {{
    eventsEl.textContent = "감지 이벤트 없음";
    return;
  }}
  for (const [p, i] of events) {{
    const div = document.createElement("div");
    div.className = "event";
    div.innerHTML = `<strong>${{stateFor(p)}} · #${{i + 1}}</strong><span>${{p.t}}</span><br><span>label=${{p.label}}, score=${{p.score == null ? "-" : p.score.toFixed(4)}}</span>`;
    div.addEventListener("click", () => {{
      index = i;
      stop();
      update();
    }});
    eventsEl.appendChild(div);
  }}
}}

function setupMetrics() {{
  const ready = points.filter(p => p.ready).length;
  const alarm = points.filter(p => p.alarm).length;
  const wandering = points.filter(p => p.wandering).length;
  const fall = points.filter(p => p.fall).length;
  metrics.total.textContent = points.length.toLocaleString();
  metrics.ready.textContent = ready.toLocaleString();
  metrics.alarm.textContent = alarm.toLocaleString();
  metrics.wandering.textContent = wandering.toLocaleString();
  metrics.fall.textContent = fall.toLocaleString();
  totalBadge.textContent = `${{points.length.toLocaleString()}} points`;
}}

playBtn.addEventListener("click", () => playing ? stop() : play());
resetBtn.addEventListener("click", () => {{
  index = 0;
  stop();
  update();
}});
slider.addEventListener("input", () => {{
  index = Number(slider.value);
  stop();
  update();
}});
window.addEventListener("resize", resize);

computeBounds();
slider.max = String(points.length - 1);
setupMetrics();
setupEvents();
resize();
update();
</script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    points = load_points(args.predictions)
    if not points:
        raise SystemExit("No valid GPS points found.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_html(points, args.predictions.name), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "points": len(points)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
