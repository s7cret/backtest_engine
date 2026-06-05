from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backtest_engine.models import EquityPoint


@dataclass(frozen=True, slots=True)
class EquityCurveSummary:
    final_equity: float
    final_cash: float
    trough_equity: float
    max_drawdown: float
    max_drawdown_percent: float
    max_runup: float
    max_runup_percent: float


@dataclass(frozen=True, slots=True)
class EquityExtremes:
    peak_equity: float
    trough_equity: float
    max_drawdown: float
    max_drawdown_percent: float
    max_runup: float
    max_runup_percent: float
    drawdown: float
    drawdown_percent: float
    runup: float
    runup_percent: float


@dataclass(frozen=True, slots=True)
class EquityMove:
    drawdown: float
    drawdown_percent: float
    runup: float
    runup_percent: float


def equity_move_from_baseline(*, baseline: float, adverse_equity: float, favorable_equity: float) -> EquityMove:
    drawdown = max(0.0, baseline - adverse_equity)
    drawdown_percent = drawdown / baseline * 100 if baseline else 0.0
    runup = max(0.0, favorable_equity - baseline)
    runup_percent = runup / baseline * 100 if baseline else 0.0
    return EquityMove(
        drawdown=drawdown,
        drawdown_percent=drawdown_percent,
        runup=runup,
        runup_percent=runup_percent,
    )


def update_equity_extremes(
    *,
    equity: float,
    peak_equity: float,
    trough_equity: float,
    max_drawdown: float,
    max_drawdown_percent: float,
    max_runup: float,
    max_runup_percent: float,
) -> EquityExtremes:
    peak = max(peak_equity, equity)
    trough = min(trough_equity, equity)
    drawdown = max(0.0, peak - equity)
    drawdown_percent = drawdown / peak * 100 if peak else 0.0
    runup = max(0.0, equity - trough)
    runup_percent = runup / trough * 100 if trough else 0.0
    return EquityExtremes(
        peak_equity=peak,
        trough_equity=trough,
        max_drawdown=max(max_drawdown, drawdown),
        max_drawdown_percent=max(max_drawdown_percent, drawdown_percent),
        max_runup=max(max_runup, runup),
        max_runup_percent=max(max_runup_percent, runup_percent),
        drawdown=drawdown,
        drawdown_percent=drawdown_percent,
        runup=runup,
        runup_percent=runup_percent,
    )


def equity_point(
    *,
    bar_index: int,
    time: int,
    equity: float,
    cash: float,
    position_size: float,
    position_avg_price: float | None,
    open_profit: float,
    realized_profit: float,
    peak: float,
    trough: float,
) -> EquityPoint:
    drawdown = max(0.0, peak - equity)
    drawdown_percent = drawdown / peak * 100 if peak else 0.0
    runup = max(0.0, equity - trough)
    runup_percent = runup / trough * 100 if trough else 0.0
    return EquityPoint(
        bar_index,
        time,
        equity,
        cash,
        position_size,
        position_avg_price,
        open_profit,
        realized_profit,
        drawdown,
        drawdown_percent,
        runup,
        runup_percent,
    )


def summarize_equity_curve(
    curve: Iterable[EquityPoint],
    *,
    default_equity: float = 0.0,
    default_cash: float | None = None,
) -> EquityCurveSummary:
    points = list(curve)
    if not points:
        cash = default_equity if default_cash is None else default_cash
        return EquityCurveSummary(default_equity, cash, default_equity, 0.0, 0.0, 0.0, 0.0)
    last = points[-1]
    return EquityCurveSummary(
        final_equity=last.equity,
        final_cash=last.cash,
        trough_equity=min(point.equity for point in points),
        max_drawdown=max(point.drawdown for point in points),
        max_drawdown_percent=max(point.drawdown_percent for point in points),
        max_runup=max(point.runup for point in points),
        max_runup_percent=max(point.runup_percent for point in points),
    )


def equity_values(curve: Iterable[EquityPoint]) -> list[float]:
    return [point.equity for point in curve]


def returns(curve: Iterable[EquityPoint]) -> list[float]:
    values = equity_values(curve)
    return [(b - a) / a if a else 0.0 for a, b in zip(values, values[1:], strict=False)]


def final_equity(curve: Iterable[EquityPoint], default: float = 0.0) -> float:
    last = default
    for point in curve:
        last = point.equity
    return last
