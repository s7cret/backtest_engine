import pytest

from backtest_engine.core import infer_price_tick
from backtest_engine.models import Bar, BarSeries


def test_infer_price_tick_uses_deepest_decimal_place_across_ohlc() -> None:
    series = BarSeries.from_bars(
        [
            Bar(1, 100.0, 100.125, 99.5, 100.25),
            Bar(2, 101.0, 101.0, 100.9999, 101.0),
        ]
    )

    assert infer_price_tick(series) == pytest.approx(0.0001)


def test_infer_price_tick_defaults_to_one_for_integer_prices() -> None:
    series = BarSeries.from_bars([Bar(1, 100, 101, 99, 100)])

    assert infer_price_tick(series) == pytest.approx(1.0)


def test_infer_price_tick_preserves_engine_sample_limit() -> None:
    bars = [Bar(i, 100, 101, 99, 100) for i in range(100)]
    bars.append(Bar(101, 100.001, 100.001, 100.001, 100.001))

    assert infer_price_tick(BarSeries.from_bars(bars)) == pytest.approx(1.0)
    assert infer_price_tick(
        BarSeries.from_bars(bars), sample_size=101
    ) == pytest.approx(0.001)
