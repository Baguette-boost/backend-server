"""Generate PNG performance report images from metrics.json using macOS sips."""

from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
from pathlib import Path


COLORS = {
    "accuracy": "#2563eb",
    "precision": "#059669",
    "recall": "#d97706",
    "f1": "#dc2626",
}


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def text(x: float, y: float, value: object, size: int = 24, color: str = "#172033", weight: int = 500, anchor: str = "start") -> str:
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
        f'font-weight="{weight}" text-anchor="{anchor}" '
        f'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif">{esc(value)}</text>'
    )


def rect(x: float, y: float, w: float, h: float, fill: str, stroke: str = "none", rx: float = 8) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}"/>'


def metric_rows(final: dict) -> list[dict]:
    binary = final["binary_models"]
    gps = binary["gps_wandering"]
    gps_server = gps["server_task_metrics"]["fall_sheets_excluded"]
    gps_route = gps["server_task_metrics"]["moving_route_only"]
    fall = binary["imu_fall_iccas_sisfall"]
    parallel = final["parallel_5class"]
    return [
        {"name": "GPS Raw", "note": "전체 sheet", **{k: gps[k] for k in ["accuracy", "precision", "recall", "f1"]}},
        {"name": "GPS Server", "note": "fall 제외", **{k: gps_server[k] for k in ["accuracy", "precision", "recall", "f1"]}},
        {"name": "GPS Route", "note": "이동 경로", **{k: gps_route[k] for k in ["accuracy", "precision", "recall", "f1"]}},
        {"name": "IMU Fall", "note": "ICCAS+SisFall", **{k: fall[k] for k in ["accuracy", "precision", "recall", "f1"]}},
        {
            "name": "GPS 5-class",
            "note": "weighted F1",
            "accuracy": parallel["gps"]["accuracy"],
            "precision": parallel["gps"]["weighted_f1"],
            "recall": parallel["gps"]["weighted_f1"],
            "f1": parallel["gps"]["weighted_f1"],
        },
        {
            "name": "IMU 5-class",
            "note": "weighted F1",
            "accuracy": parallel["imu_gyro"]["accuracy"],
            "precision": parallel["imu_gyro"]["weighted_f1"],
            "recall": parallel["imu_gyro"]["weighted_f1"],
            "f1": parallel["imu_gyro"]["weighted_f1"],
        },
    ]


def render_svg(final: dict) -> str:
    width = 1600
    height = 2100
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        rect(0, 0, width, height, "#f7f8fb", rx=0),
        text(70, 92, "ICCAS 최종 모델 성능지표", 42, "#111827", 800),
        text(70, 132, "GPS 배회 감지 + IMU 낙상 감지 서버 병렬 구조 기준", 22, "#667085", 500),
        text(1530, 92, "device: MPS", 20, "#667085", 700, "end"),
        text(1530, 124, f"rows: {final['rows']:,}", 20, "#667085", 700, "end"),
    ]

    gps_server = final["binary_models"]["gps_wandering"]["server_task_metrics"]["fall_sheets_excluded"]
    gps_route = final["binary_models"]["gps_wandering"]["server_task_metrics"]["moving_route_only"]
    fall = final["binary_models"]["imu_fall_iccas_sisfall"]
    kpis = [
        ("GPS 서버 F1", pct(gps_server["f1"]), "fall sheet 제외"),
        ("GPS 이동경로 F1", pct(gps_route["f1"]), "walk/wandering"),
        ("IMU 낙상 F1", pct(fall["f1"]), "ICCAS+SisFall"),
        ("학습 데이터", f"{final['rows']:,}", "ICCAS_final_data"),
    ]
    for i, (title, value, note) in enumerate(kpis):
        x = 70 + i * 370
        parts += [
            rect(x, 170, 340, 150, "#ffffff", "#d9dee8"),
            text(x + 24, 214, title, 22, "#667085", 650),
            text(x + 24, 270, value, 46, "#172033", 800),
            text(x + 24, 302, note, 18, "#667085", 500),
        ]

    rows = metric_rows(final)
    chart_x, chart_y, chart_w, chart_h = 90, 420, 1420, 560
    parts += [rect(70, 370, 1460, 680, "#ffffff", "#d9dee8"), text(100, 425, "모델별 성능 비교", 28, "#172033", 800)]
    plot_x, plot_y, plot_w, plot_h = chart_x + 90, chart_y + 60, chart_w - 130, chart_h - 120
    for i in range(6):
        y = plot_y + plot_h - (plot_h * i / 5)
        parts.append(f'<line x1="{plot_x}" y1="{y}" x2="{plot_x + plot_w}" y2="{y}" stroke="#e5e7eb" stroke-width="1"/>')
        parts.append(text(plot_x - 18, y + 7, f"{i * 20}%", 16, "#667085", 500, "end"))
    legend_x = plot_x
    for label, color in COLORS.items():
        parts += [rect(legend_x, 458, 18, 18, color, rx=4), text(legend_x + 28, 474, label, 17, "#344054", 650)]
        legend_x += 150
    group_w = plot_w / len(rows)
    bar_w = 22
    metrics = ["accuracy", "precision", "recall", "f1"]
    for i, row in enumerate(rows):
        cx = plot_x + group_w * i + group_w / 2
        parts.append(text(cx, plot_y + plot_h + 42, row["name"], 17, "#172033", 700, "middle"))
        parts.append(text(cx, plot_y + plot_h + 70, row["note"], 14, "#667085", 500, "middle"))
        for j, metric in enumerate(metrics):
            value = row[metric]
            h = plot_h * value
            x = cx - 52 + j * 28
            y = plot_y + plot_h - h
            parts += [
                rect(x, y, bar_w, h, COLORS[metric], rx=4),
                text(x + bar_w / 2, y - 8, f"{value:.2f}", 12, "#344054", 700, "middle"),
            ]

    parts += [rect(70, 1090, 690, 410, "#ffffff", "#d9dee8"), text(100, 1145, "라벨 분포", 28, "#172033", 800)]
    labels = sorted(final["labels"].items(), key=lambda item: item[1], reverse=True)
    total = sum(final["labels"].values())
    for i, (label, count) in enumerate(labels):
        y = 1195 + i * 58
        percent = count / total
        parts += [
            text(110, y + 18, label, 20, "#172033", 700),
            rect(260, y, 350, 22, "#eef2f7", rx=11),
            rect(260, y, 350 * percent, 22, "#2563eb", rx=11),
            text(635, y + 18, f"{count:,}", 18, "#667085", 650, "end"),
            text(720, y + 18, pct(percent), 18, "#667085", 650, "end"),
        ]

    parts += [rect(800, 1090, 730, 410, "#ffffff", "#d9dee8"), text(830, 1145, "핵심 Confusion Matrix", 28, "#172033", 800)]
    matrices = [
        ("GPS Server", gps_server),
        ("IMU Fall", {"tp": 4014, "fp": 507, "tn": 5465, "fn": 769}),
    ]
    for m_index, (title, values) in enumerate(matrices):
        x = 840 + m_index * 340
        y = 1190
        parts.append(text(x, y - 20, title, 22, "#172033", 800))
        cells = [("TP", values["tp"], "#dcfce7", "#166534"), ("FP", values["fp"], "#fff7ed", "#9a3412"), ("FN", values["fn"], "#fff7ed", "#9a3412"), ("TN", values["tn"], "#dcfce7", "#166534")]
        for i, (name, value, fill, color) in enumerate(cells):
            cx = x + (i % 2) * 145
            cy = y + (i // 2) * 105
            parts += [rect(cx, cy, 125, 85, fill, rx=8), text(cx + 18, cy + 30, name, 18, color, 700), text(cx + 18, cy + 68, f"{int(value):,}", 30, color, 800)]

    parts += [rect(70, 1540, 1460, 360, "#ffffff", "#d9dee8"), text(100, 1595, "서버 적용 구조", 28, "#172033", 800)]
    flow = [
        ("GPS Server", "iccas_final_lstm_gps_wandering.pt", "배회 감지 전용"),
        ("IMU Server", "iccas_final_sisfall_lstm_imu_fall.pt", "낙상 감지 전용"),
        ("Result Merge", "fall 우선순위 적용", "낙상 구간의 GPS 배회 알림은 낮은 우선순위"),
    ]
    for i, (title, model, note) in enumerate(flow):
        x = 110 + i * 470
        parts += [
            rect(x, 1640, 420, 185, "#eef2f7", rx=8),
            text(x + 24, 1688, title, 25, "#172033", 800),
            text(x + 24, 1735, model, 18, "#2563eb", 700),
            text(x + 24, 1782, note, 18, "#667085", 500),
        ]
        if i < 2:
            parts.append(text(x + 440, 1740, "→", 42, "#98a2b3", 700))

    parts += [
        text(70, 2030, "Generated from ai-model-release/metrics.json", 18, "#667085", 500),
        "</svg>",
    ]
    return "\n".join(parts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", type=Path, default=Path("metrics.json"))
    parser.add_argument("--svg-output", type=Path, required=True)
    parser.add_argument("--png-output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    final = json.loads(args.metrics.read_text(encoding="utf-8"))["iccas_final_data"]
    args.svg_output.parent.mkdir(parents=True, exist_ok=True)
    args.svg_output.write_text(render_svg(final), encoding="utf-8")
    args.png_output.parent.mkdir(parents=True, exist_ok=True)
    converted = False
    try:
        subprocess.run(
            ["sips", "-s", "format", "png", str(args.svg_output), "--out", str(args.png_output)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        converted = True
    except (FileNotFoundError, subprocess.CalledProcessError):
        subprocess.run(
            ["qlmanage", "-t", "-s", "2000", "-o", str(args.png_output.parent), str(args.svg_output)],
            check=True,
        )
        quicklook_output = args.png_output.parent / f"{args.svg_output.name}.png"
        if quicklook_output.exists():
            shutil.copyfile(quicklook_output, args.png_output)
            converted = True
    if not converted or not args.png_output.exists():
        raise SystemExit("PNG conversion failed. Try opening the SVG and exporting it as PNG.")
    print(json.dumps({"svg_output": str(args.svg_output), "png_output": str(args.png_output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
