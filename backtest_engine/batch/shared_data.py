from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from backtest_engine.models.bar_series import BarSeries


class SharedBarCache:
    """Small in-process cache for pre-normalized bar series used by batch jobs.

    The current engine does not provide cross-process shared memory buffers, but this
    class still centralizes normalized ``BarSeries`` reuse instead of being a pass-only
    placeholder. Callers can store raw records once and receive the same immutable-ish
    series object for repeated jobs in sequential/threaded batches.
    """

    def __init__(self) -> None:
        self._series: dict[str, BarSeries] = {}

    def put(self, key: str, bars: BarSeries | list[dict[str, Any]]) -> BarSeries:
        series = bars if isinstance(bars, BarSeries) else BarSeries.from_records(bars)
        self._series[key] = series
        return series

    def get(self, key: str) -> BarSeries:
        return self._series[key]

    def get_or_put(self, key: str, bars: BarSeries | list[dict[str, Any]]) -> BarSeries:
        return self._series.get(key) or self.put(key, bars)

    def __contains__(self, key: object) -> bool:
        return key in self._series

    def __len__(self) -> int:
        return len(self._series)

    def __iter__(self) -> Iterator[str]:
        return iter(self._series)

    def clear(self) -> None:
        self._series.clear()
