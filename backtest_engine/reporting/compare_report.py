from __future__ import annotations
import json
from backtest_engine.results.comparison import ComparisonReport


def render(report: ComparisonReport, *, format: str = "text") -> str:
    if format == "json":
        return json.dumps(report.to_dict(), indent=2, sort_keys=True, default=str)
    lines = [
        f"matched: {report.matched}",
        f"our trades: {report.our_count}",
        f"reference trades: {report.reference_count}",
        f"first mismatch: {report.first_mismatch_index}",
    ]
    for d in report.diagnostics[:5]:
        lines.append(f"- {d.code}: {d.message} {d.context or {}}")
    return "\n".join(lines)
