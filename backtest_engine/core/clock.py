from __future__ import annotations

from dataclasses import dataclass

from backtest_engine.models import Bar


@dataclass
class BacktestClock:
    """Mutable clock for a bar-by-bar run."""

    bar_index: int = -1
    time: int | None = None

    def advance(self, bar: Bar, bar_index: int) -> None:
        if bar_index < self.bar_index:
            raise ValueError('bar_index cannot move backwards')
        self.bar_index = bar_index
        self.time = bar.time

    @property
    def has_started(self) -> bool:
        return self.bar_index >= 0
