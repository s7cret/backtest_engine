from __future__ import annotations

from typing import Any

from backtest_engine.results.comparison import ComparisonReport, compare_trades


class StreamingTradeComparator:
    """Accumulates actual/reference trades and compares with existing parity logic."""

    def __init__(self, *, price_tolerance: float = 0.0, qty_tolerance: float = 0.0):
        self.price_tolerance = price_tolerance
        self.qty_tolerance = qty_tolerance
        self.actual: list[Any] = []
        self.reference: list[Any] = []

    def add_actual(self, trade: Any) -> None:
        self.actual.append(trade)

    def add_reference(self, trade: Any) -> None:
        self.reference.append(trade)

    def report(self) -> ComparisonReport:
        return compare_trades(self.actual, self.reference, price_tolerance=self.price_tolerance, qty_tolerance=self.qty_tolerance)
