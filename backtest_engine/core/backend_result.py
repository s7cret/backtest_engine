"""Helpers for converting execution-backend results into BacktestEngine state."""

from __future__ import annotations

from typing import Any

from backtest_engine.errors import StrategyRuntimeError
from backtest_engine.models import Trade


def required_backend_trade_float(trade: Any, field: str, idx: int) -> float:
    """Extract a required float field from a PineRuntime trade object."""
    value = getattr(trade, field, None)
    if value is None:
        raise StrategyRuntimeError(
            f"Pine runtime trade {idx} is missing required ledger field {field!r}"
        )
    return float(value)


def trade_from_backend_trade(trade: Any, idx: int) -> Trade:
    """Convert a PineRuntime trade object into an internal Trade model."""
    entry_id = str(getattr(trade, "entry_id", f"entry_{idx}"))
    commission_entry = required_backend_trade_float(trade, "commission_entry", idx)
    commission_exit = required_backend_trade_float(trade, "commission_exit", idx)
    max_runup = required_backend_trade_float(trade, "max_runup", idx)
    max_drawdown = required_backend_trade_float(trade, "max_drawdown", idx)
    return Trade(
        id=f"pine_{idx}",
        entry_id=entry_id,
        exit_id=str(getattr(trade, "exit_reason", "") or "") or None,
        direction=getattr(trade, "direction", "long"),
        entry_time=int(trade.entry_time),
        entry_bar_index=int(trade.entry_bar_index),
        entry_price=float(trade.entry_price),
        exit_time=getattr(trade, "exit_time", None),
        exit_bar_index=getattr(trade, "exit_bar_index", None),
        exit_price=getattr(trade, "exit_price", None),
        qty=float(trade.qty),
        commission_entry=commission_entry,
        commission_exit=commission_exit,
        profit=required_backend_trade_float(trade, "profit", idx),
        profit_percent=required_backend_trade_float(trade, "profit_percent", idx),
        mfe=max_runup,
        mae=-max_drawdown,
        max_runup=max_runup,
        max_drawdown=max_drawdown,
        exit_reason=getattr(trade, "exit_reason", None),
        bars_held=(
            None
            if getattr(trade, "exit_bar_index", None) is None
            else int(trade.exit_bar_index) - int(trade.entry_bar_index)
        ),
    )
