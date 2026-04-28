from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Iterable

from backtest_engine.models import Trade


def trade_to_row(trade: Trade | dict[str, Any]) -> dict[str, Any]:
    if is_dataclass(trade):
        return asdict(trade)
    return dict(trade)


def trades_to_rows(trades: Iterable[Trade | dict[str, Any]]) -> list[dict[str, Any]]:
    return [trade_to_row(trade) for trade in trades]


def closed_trade_rows(result: object) -> list[dict[str, Any]]:
    return trades_to_rows(getattr(result, 'closed_trades', None) or [])
