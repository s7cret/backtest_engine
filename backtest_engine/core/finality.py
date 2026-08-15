from __future__ import annotations

from collections.abc import Sequence

from openpine_contracts import Finality

from backtest_engine.errors import BarValidationError
from backtest_engine.models.bar import Bar
from backtest_engine.models.bar_series import BarSeries


def admit_bars(
    bars: Sequence[Bar] | BarSeries,
    *,
    policy: str,
) -> list[Bar] | BarSeries:
    if policy not in {"CLOSED_BAR_ONLY", "ALLOW_OPEN"}:
        raise BarValidationError(f"unknown finality_policy: {policy}")
    if policy == "ALLOW_OPEN":
        if isinstance(bars, BarSeries):
            return bars
        return list(bars)
    if isinstance(bars, BarSeries):
        raise BarValidationError(
            "CLOSED_BAR_ONLY requires list[Bar] with explicit finality"
        )
    kept: list[Bar] = []
    for bar in bars:
        if bar.finality is None:
            raise BarValidationError("finality required for CLOSED_BAR_ONLY")
        if bar.finality is Finality.FINAL:
            kept.append(bar)
    return kept
