from __future__ import annotations
import json
from typing import Any


def render(report: Any, format: str = "json") -> str:
    data = report.to_dict() if hasattr(report, "to_dict") else dict(report)
    if format == "json":
        return json.dumps(data, indent=2, sort_keys=True) + "\n"
    lines = ["# Backtest benchmark"]
    for k in ("runs", "bars", "total_bars", "wall_time_sec", "bars_per_sec", "peak_memory_bytes"):
        lines.append(f"{k}: {data.get(k)}")
    return "\n".join(lines) + "\n"
