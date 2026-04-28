from __future__ import annotations

from statistics import mean, pstdev
from typing import Iterable

from backtest_engine.models import Trade
from backtest_engine.results.statistics import summarize


def trade_profits(trades: Iterable[Trade]) -> list[float]:
    return [float(trade.profit) for trade in trades]


def summary_metrics(
    *, profits: Iterable[float], initial_capital: float, final_equity: float
) -> dict[str, float | int]:
    return summarize(list(profits), initial_capital, final_equity)


def sharpe_ratio(returns: Iterable[float], risk_free_rate: float = 0.0) -> float | None:
    xs = [r - risk_free_rate for r in returns]
    if len(xs) < 2:
        return None
    sigma = pstdev(xs)
    return None if sigma == 0 else mean(xs) / sigma


def sortino_ratio(returns: Iterable[float], target_return: float = 0.0) -> float | None:
    xs = [r - target_return for r in returns]
    downside = [min(0.0, r) for r in xs]
    if len(xs) < 2 or not any(downside):
        return None
    sigma = pstdev(downside)
    return None if sigma == 0 else mean(xs) / sigma
