from __future__ import annotations

from typing import Any


def render(result: Any) -> str:
    getter = (
        result.get
        if isinstance(result, dict)
        else lambda name, default=None: getattr(result, name, default)
    )
    lines = [
        "# Backtest summary",
        f"final_equity: {getter('final_equity')}",
        f"net_profit: {getter('net_profit')}",
        f"total_trades: {getter('total_trades')}",
    ]
    return "\n".join(lines) + "\n"
