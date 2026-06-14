from __future__ import annotations

import pytest

from backtest_engine.models.bar import Bar, from_contract_bar, to_contract_bar

contracts = pytest.importorskip(
    "marketdata_provider.contracts",
    reason="marketdata-provider is an optional integration test dependency",
)
InstrumentKey = contracts.InstrumentKey
parse_timeframe = contracts.parse_timeframe


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
            Bar(time=0, open=1, high=1, low=1, close=1),
            instrument=InstrumentKey("binance", "spot", "BTCUSDT"),
            timeframe=parse_timeframe("1M"),
        )


def test_contract_bar_round_trip_preserves_engine_shape() -> None:
    instrument = InstrumentKey("binance", "spot", "BTCUSDT")
    timeframe = parse_timeframe("1m")
    engine_bar = Bar(
        time=0, open=1, high=2, low=0.5, close=1.5, volume=10.0, time_close=59_999
    )

    round_trip = from_contract_bar(
        to_contract_bar(
            engine_bar, instrument=instrument, timeframe=timeframe, closed=True
        )
    )

    assert round_trip == engine_bar
