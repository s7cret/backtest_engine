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
        if bars.finality is None:
            raise BarValidationError(
                "CLOSED_BAR_ONLY requires BarSeries with explicit finality"
            )
        kept_indexes: list[int] = []
        normalized: list[Finality] = []
        for index, value in enumerate(bars.finality):
            if value is None:
                raise BarValidationError("finality required for CLOSED_BAR_ONLY")
            try:
                finality = value if isinstance(value, Finality) else Finality(str(value))
            except ValueError as exc:
                raise BarValidationError(f"unknown bar finality: {value!r}") from exc
            if finality is Finality.FINAL:
                kept_indexes.append(index)
                normalized.append(finality)
        return BarSeries(
            [bars.time[index] for index in kept_indexes],
            [bars.open[index] for index in kept_indexes],
            [bars.high[index] for index in kept_indexes],
            [bars.low[index] for index in kept_indexes],
            [bars.close[index] for index in kept_indexes],
            (
                None
                if bars.volume is None
                else [bars.volume[index] for index in kept_indexes]
            ),
            (
                None
                if bars.time_close is None
                else [bars.time_close[index] for index in kept_indexes]
            ),
            normalized,
        )
    kept: list[Bar] = []
    for bar in bars:
        if bar.finality is None:
            raise BarValidationError("finality required for CLOSED_BAR_ONLY")
        if bar.finality is Finality.FINAL:
            kept.append(bar)
    return kept
