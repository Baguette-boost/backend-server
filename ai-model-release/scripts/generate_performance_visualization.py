"""Generate a standalone performance visualization dashboard for final ICCAS models."""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path
from typing import Any


METRICS = ["accuracy", "precision", "recall", "f1"]
COLORS = {
    "accuracy": "#2563eb",
    "precision": "#059669",
    "recall": "#d97706",
    "f1": "#dc2626",
}


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def num(value: float) -> str:
    return f"{value:.4f}"


def metric_row(name: str, values: dict[str, float], note: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "accuracy": float(values["accuracy"]),
        "precision": float(values["precision"]),
        "recall": float(values["recall"]),
        "f1": float(values["f1"]),
        "note": note,
    }


def bar_chart(rows: list[dict[str, Any]]) -> str:
    width = 1120
    height = 390
    margin_left = 170
    margin_right = 40
    margin_top = 34
    margin_bottom = 82
    chart_width = width - margin_left - margin_right
    chart_height = height - margin_top - margin_bottom
    group_width = chart_width / len(rows)
    bar_width = min(28, group_width / 6)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Model metric comparison chart">'
    ]
    for i in range(6):
        y = margin_top + chart_height - chart_height * i / 5
        value = i / 5
        parts.append(f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{margin_left - 14}" y="{y + 4:.1f}" text-anchor="end" class="axis">{int(value * 100)}%</text>')
    for index, row in enumerate(rows):
        base_x = margin_left + index * group_width + group_width / 2
        label = html.escape(row["name"])
        parts.append(f'<text x="{base_x:.1f}" y="{height - 44}" text-anchor="middle" class="x-label">{label}</text>')
        parts.append(f'<text x="{base_x:.1f}" y="{height - 22}" text-anchor="middle" class="x-sub">{html.escape(row.get("note", ""))}</text>')
        for metric_index, metric in enumerate(METRICS):
            value = row[metric]
            x = base_x - (len(METRICS) * bar_width) / 2 + metric_index * bar_width + 3
            h = chart_height * value
            y = margin_top + chart_height - h
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width - 6:.1f}" height="{h:.1f}" '
                f'rx="3" fill="{COLORS[metric]}"><title>{metric}: {pct(value)}</title></rect>'
            )
            parts.append(f'<text x="{x + (bar_width - 6) / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" class="bar-value">{value:.2f}</text>')
    legend_x = margin_left
    for metric in METRICS:
        parts.append(f'<rect x="{legend_x}" y="12" width="12" height="12" rx="2" fill="{COLORS[metric]}"/>')
        parts.append(f'<text x="{legend_x + 18}" y="22" class="legend">{metric}</text>')
        legend_x += 118
    parts.append("</svg>")
    return "\n".join(parts)


def horizontal_chart(labels: dict[str, int]) -> str:
    total = sum(labels.values())
    rows = sorted(labels.items(), key=lambda item: item[1], reverse=True)
    parts = []
    for label, count in rows:
        percent = count / total if total else 0.0
        parts.append(
            '<div class="dist-row">'
            f'<div class="dist-label">{html.escape(label)}</div>'
            '<div class="dist-track">'
            f'<div class="dist-fill" style="width:{percent * 100:.2f}%"></div>'
            '</div>'
            f'<div class="dist-count">{count:,}</div>'
            f'<div class="dist-pct">{percent * 100:.1f}%</div>'
            '</div>'
        )
    return "\n".join(parts)


def confusion_card(title: str, values: dict[str, float]) -> str:
    tp = int(values.get("tp", 0))
    fp = int(values.get("fp", 0))
    tn = int(values.get("tn", 0))
    fn = int(values.get("fn", 0))
    return f"""
    <section class="matrix-card">
      <h3>{html.escape(title)}</h3>
      <div class="matrix">
        <div class="cell good"><span>TP</span><strong>{tp:,}</strong></div>
        <div class="cell warn"><span>FP</span><strong>{fp:,}</strong></div>
        <div class="cell warn"><span>FN</span><strong>{fn:,}</strong></div>
        <div class="cell good"><span>TN</span><strong>{tn:,}</strong></div>
      </div>
    </section>
    """


def render_html(final: dict[str, Any], rows: list[dict[str, Any]], output_csv: Path) -> str:
    labels = final["labels"]
    binary = final["binary_models"]
    gps = binary["gps_wandering"]
    gps_server = gps["server_task_metrics"]["fall_sheets_excluded"]
    gps_route = gps["server_task_metrics"]["moving_route_only"]
    fall = binary["imu_fall_iccas_sisfall"]
    parallel = final["parallel_5class"]
    cards = [
        ("GPS 서버 F1", gps_server["f1"], "fall sheet 제외"),
        ("GPS 이동경로 F1", gps_route["f1"], "walk/wandering 기준"),
        ("IMU 낙상 F1", fall["f1"], "ICCAS+SisFall"),
        ("학습 Row", final["rows"], "ICCAS_final_data"),
    ]
    card_html = []
    for title, value, subtitle in cards:
        if isinstance(value, float):
            display = pct(value)
        else:
            display = f"{value:,}"
        card_html.append(
            f'<article class="kpi"><span>{html.escape(title)}</span><strong>{display}</strong><em>{html.escape(subtitle)}</em></article>'
        )

    table_rows = []
    for row in rows:
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(row['name'])}</td>"
            f"<td>{pct(row['accuracy'])}</td>"
            f"<td>{pct(row['precision'])}</td>"
            f"<td>{pct(row['recall'])}</td>"
            f"<td>{pct(row['f1'])}</td>"
            f"<td>{html.escape(row.get('note', ''))}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ICCAS Final Model Performance</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fb;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #667085;
      --line: #d9dee8;
      --soft: #eef2f7;
      --good: #dcfce7;
      --good-ink: #166534;
      --warn: #fff7ed;
      --warn-ink: #9a3412;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{ max-width: 1220px; margin: 0 auto; padding: 28px; }}
    header {{ display: flex; justify-content: space-between; gap: 20px; align-items: end; margin-bottom: 22px; }}
    h1 {{ margin: 0; font-size: 30px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 14px; font-size: 19px; }}
    h3 {{ margin: 0 0 12px; font-size: 16px; }}
    .sub {{ color: var(--muted); margin-top: 8px; }}
    .stamp {{ text-align: right; color: var(--muted); font-size: 13px; line-height: 1.5; }}
    .kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }}
    .kpi, .panel, .matrix-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
    }}
    .kpi {{ padding: 16px; }}
    .kpi span {{ display: block; color: var(--muted); font-size: 13px; }}
    .kpi strong {{ display: block; margin: 8px 0 4px; font-size: 28px; }}
    .kpi em {{ color: var(--muted); font-style: normal; font-size: 12px; }}
    .panel {{ padding: 18px; margin-bottom: 16px; }}
    .chart-wrap {{ overflow-x: auto; }}
    svg {{ width: 100%; min-width: 980px; height: auto; }}
    .grid {{ stroke: #e5e7eb; stroke-width: 1; }}
    .axis, .legend, .x-sub {{ fill: #667085; font-size: 12px; }}
    .x-label {{ fill: #172033; font-size: 12px; font-weight: 700; }}
    .bar-value {{ fill: #344054; font-size: 10px; font-weight: 700; }}
    .dist-row {{ display: grid; grid-template-columns: 140px 1fr 78px 58px; gap: 10px; align-items: center; margin: 10px 0; }}
    .dist-label {{ font-weight: 700; }}
    .dist-track {{ height: 12px; background: var(--soft); border-radius: 999px; overflow: hidden; }}
    .dist-fill {{ height: 100%; background: #2563eb; border-radius: inherit; }}
    .dist-count, .dist-pct {{ color: var(--muted); text-align: right; font-variant-numeric: tabular-nums; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ padding: 10px 8px; border-bottom: 1px solid var(--line); text-align: right; }}
    th:first-child, td:first-child, th:last-child, td:last-child {{ text-align: left; }}
    th {{ color: var(--muted); font-weight: 700; }}
    .matrices {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px; }}
    .matrix-card {{ padding: 16px; }}
    .matrix {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }}
    .cell {{ border-radius: 6px; padding: 12px; min-height: 76px; }}
    .cell span {{ display: block; font-size: 12px; }}
    .cell strong {{ display: block; font-size: 24px; margin-top: 8px; }}
    .good {{ background: var(--good); color: var(--good-ink); }}
    .warn {{ background: var(--warn); color: var(--warn-ink); }}
    .flow {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }}
    .flow div {{ background: var(--soft); padding: 14px; border-radius: 8px; min-height: 86px; }}
    .flow strong {{ display: block; margin-bottom: 6px; }}
    code {{ background: var(--soft); padding: 2px 5px; border-radius: 4px; }}
    footer {{ color: var(--muted); font-size: 12px; margin: 18px 0 4px; }}
    @media (max-width: 900px) {{
      main {{ padding: 18px; }}
      header {{ display: block; }}
      .stamp {{ text-align: left; margin-top: 12px; }}
      .kpis, .matrices, .flow {{ grid-template-columns: 1fr; }}
      .dist-row {{ grid-template-columns: 96px 1fr 70px; }}
      .dist-pct {{ display: none; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>ICCAS 최종 모델 성능 시각화</h1>
      <div class="sub">GPS 배회 감지와 IMU 낙상 감지를 서버 병렬 구조 기준으로 비교합니다.</div>
    </div>
    <div class="stamp">
      source: <code>{html.escape(final['source'])}</code><br>
      device: <code>{html.escape(final['device'])}</code><br>
      CSV: <code>{html.escape(str(output_csv))}</code>
    </div>
  </header>

  <section class="kpis">
    {"".join(card_html)}
  </section>

  <section class="panel">
    <h2>모델별 성능 비교</h2>
    <div class="chart-wrap">{bar_chart(rows)}</div>
  </section>

  <section class="panel">
    <h2>데이터 라벨 분포</h2>
    {horizontal_chart(labels)}
  </section>

  <section class="panel">
    <h2>성능 표</h2>
    <table>
      <thead>
        <tr><th>Model</th><th>Accuracy</th><th>Precision</th><th>Recall</th><th>F1</th><th>Note</th></tr>
      </thead>
      <tbody>
        {"".join(table_rows)}
      </tbody>
    </table>
  </section>

  <section class="matrices">
    {confusion_card("GPS Raw", {"tp": 1822, "fp": 300, "tn": 1548, "fn": 27})}
    {confusion_card("GPS Server Task", gps_server)}
    {confusion_card("IMU Fall", {"tp": 4014, "fp": 507, "tn": 5465, "fn": 769})}
  </section>

  <section class="panel">
    <h2>서버 적용 구조</h2>
    <div class="flow">
      <div><strong>GPS Server</strong><code>iccas_final_lstm_gps_wandering.pt</code><br>배회 감지 전용</div>
      <div><strong>IMU Server</strong><code>iccas_final_sisfall_lstm_imu_fall.pt</code><br>낙상 감지 전용</div>
      <div><strong>Result Merge</strong>낙상 이벤트가 활성화된 구간의 GPS 배회 알림은 낮은 우선순위로 병합</div>
    </div>
  </section>

  <footer>Generated from ai-model-release/metrics.json</footer>
</main>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", type=Path, default=Path("metrics.json"))
    parser.add_argument("--html-output", type=Path, required=True)
    parser.add_argument("--csv-output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))
    final = metrics["iccas_final_data"]
    binary = final["binary_models"]
    parallel = final["parallel_5class"]
    gps = binary["gps_wandering"]
    gps_server = gps["server_task_metrics"]["fall_sheets_excluded"]
    gps_route = gps["server_task_metrics"]["moving_route_only"]
    fall = binary["imu_fall_iccas_sisfall"]

    rows = [
        metric_row("GPS Raw", gps, "전체 sheet"),
        metric_row("GPS Server", gps_server, "fall 제외"),
        metric_row("GPS Route", gps_route, "이동 경로"),
        metric_row("IMU Fall", fall, "ICCAS+SisFall"),
        metric_row(
            "GPS 5-class",
            {
                "accuracy": parallel["gps"]["accuracy"],
                "precision": parallel["gps"]["weighted_f1"],
                "recall": parallel["gps"]["weighted_f1"],
                "f1": parallel["gps"]["weighted_f1"],
            },
            "weighted F1",
        ),
        metric_row(
            "IMU 5-class",
            {
                "accuracy": parallel["imu_gyro"]["accuracy"],
                "precision": parallel["imu_gyro"]["weighted_f1"],
                "recall": parallel["imu_gyro"]["weighted_f1"],
                "f1": parallel["imu_gyro"]["weighted_f1"],
            },
            "weighted F1",
        ),
    ]

    args.csv_output.parent.mkdir(parents=True, exist_ok=True)
    with args.csv_output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["name", "accuracy", "precision", "recall", "f1", "note"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    args.html_output.parent.mkdir(parents=True, exist_ok=True)
    args.html_output.write_text(render_html(final, rows, args.csv_output), encoding="utf-8")
    print(json.dumps({"html_output": str(args.html_output), "csv_output": str(args.csv_output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
