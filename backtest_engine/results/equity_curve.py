from __future__ import annotations

from typing import Iterable

from backtest_engine.models import EquityPoint


def equity_values(curve: Iterable[EquityPoint]) -> list[float]:
    return [point.equity for point in curve]


def returns(curve: Iterable[EquityPoint]) -> list[float]:
    values = equity_values(curve)
    return [(b - a) / a if a else 0.0 for a, b in zip(values, values[1:])]


def final_equity(curve: Iterable[EquityPoint], default: float = 0.0) -> float:
    last = default
    for point in curve:
        last = point.equity
    return last
