from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any


def _row(item: Any) -> dict[str, Any]:
    if is_dataclass(item):
        return asdict(item)
    if hasattr(item, "__dict__"):
        return dict(item.__dict__)
    return {"value": item}


def render(report: Any, *, format: str = "json") -> str:
    rows = [_row(item) for item in report] if not isinstance(report, dict) else report
    if format == "json":
        return json.dumps(rows, indent=2, sort_keys=True, default=str) + "\n"
    return "\n".join(str(row) for row in rows) + "\n"
