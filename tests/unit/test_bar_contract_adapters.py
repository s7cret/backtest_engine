from __future__ import annotations

import pytest

from marketdata_provider.contracts import InstrumentKey, parse_timeframe
from openpine_contracts import Finality

from backtest_engine.models.bar import (
    Bar,
    BarFinalityError,
    admit_closed_bar_only,
    from_contract_bar,
    to_contract_bar,
)


def test_to_contract_bar_requires_explicit_identity_and_fills_fixed_close_time() -> (
    None
):
    instrument = InstrumentKey("binance", "spot", "BTCUSDT")
    timeframe = parse_timeframe("1m")

    contract = to_contract_bar(
        Bar(time=60_000, open=1, high=2, low=0.5, close=1.5, volume=None),
        instrument=instrument,
        timeframe=timeframe,
        closed=False,
    )

    assert contract.instrument == instrument
    assert contract.timeframe == timeframe
    assert contract.time_close == 119_999
    assert contract.volume is None
    assert contract.closed is False


def test_to_contract_bar_rejects_missing_close_time_for_monthly_timeframe() -> None:
    with pytest.raises(ValueError, match="time_close is required"):
        to_contract_bar(
            Bar(time=0, open=1, high=1, low=1, close=1, finality=Finality.FINAL),
            instrument=InstrumentKey("binance", "spot", "BTCUSDT"),
            timeframe=parse_timeframe("1M"),
        )


def test_missing_finality_does_not_default_to_closed() -> None:
    instrument = InstrumentKey("binance", "spot", "BTCUSDT")
    timeframe = parse_timeframe("1m")
    with pytest.raises(BarFinalityError, match="cannot default to FINAL"):
        to_contract_bar(
            Bar(time=0, open=1, high=2, low=0.5, close=1.5, volume=10.0, time_close=59_999),
            instrument=instrument,
            timeframe=timeframe,
        )


def test_open_finality_survives_round_trip() -> None:
    instrument = InstrumentKey("binance", "spot", "BTCUSDT")
    timeframe = parse_timeframe("1m")
    engine_bar = Bar(
        time=0,
        open=1,
        high=2,
        low=0.5,
        close=1.5,
        volume=10.0,
        time_close=59_999,
        finality=Finality.OPEN,
    )
    round_trip = from_contract_bar(
        to_contract_bar(engine_bar, instrument=instrument, timeframe=timeframe)
    )
    assert round_trip.finality is Finality.OPEN
    assert round_trip.time == engine_bar.time
    assert admit_closed_bar_only(
        Bar(
            time=0,
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            time_close=59_999,
            finality=Finality.FINAL,
        )
    ).finality is Finality.FINAL
    with pytest.raises(BarFinalityError, match="FINAL"):
        admit_closed_bar_only(round_trip)
