from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backtest_engine.models import EquityPoint


@dataclass(frozen=True)
class DrawdownPoint:
    index: int
    equity: float
    peak: float
    drawdown: float
    drawdown_percent: float


def calculate_drawdowns(equity_values: Iterable[float]) -> list[DrawdownPoint]:
    points: list[DrawdownPoint] = []
    peak: float | None = None
    for idx, equity in enumerate(equity_values):
        peak = equity if peak is None else max(peak, equity)
        drawdown = max(0.0, peak - equity)
        pct = drawdown / peak * 100.0 if peak else 0.0
        points.append(DrawdownPoint(idx, equity, peak, drawdown, pct))
    return points


def max_drawdown(equity_values: Iterable[float]) -> DrawdownPoint | None:
    points = calculate_drawdowns(equity_values)
    return max(points, key=lambda p: (p.drawdown, p.drawdown_percent), default=None)


def max_drawdown_from_curve(curve: Iterable[EquityPoint]) -> DrawdownPoint | None:
    return max_drawdown(point.equity for point in curve)
