from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marketdata_provider.contracts import InstrumentKey, Timeframe
    from marketdata_provider.contracts.bar import Bar as ContractBar


@dataclass(frozen=True, slots=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    time_close: int | None = None


def to_contract_bar(
    bar: Bar,
    *,
    instrument: InstrumentKey,
    timeframe: Timeframe,
    closed: bool = True,
) -> ContractBar:
    from marketdata_provider.contracts.bar import Bar as ContractBar

    time_close = bar.time_close
    if time_close is None:
        if timeframe.duration_ms is None:
            raise ValueError("time_close is required for non-fixed-duration timeframes")
        time_close = bar.time + timeframe.duration_ms - 1

    return ContractBar(
        instrument=instrument,
        timeframe=timeframe,
        time=bar.time,
        time_close=time_close,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        closed=closed,
    )


def from_contract_bar(bar: ContractBar) -> Bar:
    return Bar(
        time=bar.time,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        time_close=bar.time_close,
    )
