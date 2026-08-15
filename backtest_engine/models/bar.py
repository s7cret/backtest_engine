from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openpine_contracts import Finality

if TYPE_CHECKING:
    from marketdata_provider.contracts import InstrumentKey, Timeframe
    from marketdata_provider.contracts.bar import Bar as ContractBar


class BarFinalityError(ValueError):
    code = "FINALITY_REQUIRED"


@dataclass(frozen=True, slots=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    time_close: int | None = None
    finality: Finality | None = None


def _require_finality(
    *,
    finality: Finality | None = None,
    closed: bool | None = None,
) -> Finality:
    if finality is not None and closed is not None:
        mapped = Finality.FINAL if closed else Finality.OPEN
        if mapped is not finality:
            raise BarFinalityError("finality and closed disagree")
        return finality
    if finality is not None:
        return finality
    if closed is True:
        return Finality.FINAL
    if closed is False:
        return Finality.OPEN
    raise BarFinalityError("missing finality cannot default to FINAL")


def to_contract_bar(
    bar: Bar,
    *,
    instrument: InstrumentKey,
    timeframe: Timeframe,
    finality: Finality | None = None,
    closed: bool | None = None,
) -> ContractBar:
    from marketdata_provider.contracts.bar import Bar as ContractBar

    resolved = _require_finality(finality=finality or bar.finality, closed=closed)
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
        closed=resolved is Finality.FINAL,
    )


def from_contract_bar(bar: ContractBar) -> Bar:
    if not hasattr(bar, "closed"):
        raise BarFinalityError("missing finality cannot default to FINAL")
    resolved = _require_finality(closed=bar.closed)
    return Bar(
        time=bar.time,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        time_close=bar.time_close,
        finality=resolved,
    )


def admit_closed_bar_only(bar: Bar) -> Bar:
    if bar.finality is not Finality.FINAL:
        raise BarFinalityError("CLOSED_BAR_ONLY accepts FINAL bars only")
    return bar
