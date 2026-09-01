from pathlib import Path

import pytest

from backtest_engine import BacktestConfig, BacktestEngine, Bar
from backtest_engine.errors import BarValidationError
from backtest_engine.models.bar import to_contract_bar
from marketdata_provider.contracts import InstrumentKey, parse_timeframe
from openpine_contracts import Finality


class BuyFirst:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)


class BuyLast:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 2:
            self.ctx.entry("L", "long", qty=1)


def _cfg(**kw: object) -> BacktestConfig:
    return BacktestConfig(
        symbol="S",
        timeframe="1m",
        start_time=1,
        end_time=4,
        commission_type="none",
        finality_policy=str(kw.pop("finality_policy", "CLOSED_BAR_ONLY")),
        **kw,  # type: ignore[arg-type]
    )


def test_to_contract_bar_missing_closed_is_not_final() -> None:
    with pytest.raises(TypeError, match="closed"):
        to_contract_bar(
            Bar(time=0, open=1, high=1, low=1, close=1),
            instrument=InstrumentKey("binance", "spot", "BTCUSDT"),
            timeframe=parse_timeframe("1m"),
        )


def test_closed_bar_only_rejects_missing_finality() -> None:
    bars = [Bar(1, 10, 11, 9, 10), Bar(2, 11, 12, 10, 11)]
    with pytest.raises(BarValidationError, match="finality"):
        BacktestEngine(_cfg()).run(BuyLast, bars=bars)


def test_backtest_config_defaults_to_closed_bar_only() -> None:
    config = BacktestConfig(symbol="S", timeframe="1m", start_time=1, end_time=2)
    assert config.finality_policy == "CLOSED_BAR_ONLY"


def test_closed_bar_only_does_not_trade_open_bar() -> None:
    bars = [
        Bar(1, 10, 11, 9, 10, finality=Finality.FINAL),
        Bar(2, 11, 12, 10, 11, finality=Finality.FINAL),
        Bar(3, 12, 13, 11, 12, finality=Finality.OPEN),
    ]
    result = BacktestEngine(_cfg()).run(BuyLast, bars=bars)
    assert not result.closed_trades
    assert not result.open_trades


def test_allow_open_can_trade_open_bar() -> None:
    bars = [
        Bar(1, 10, 11, 9, 10, finality=Finality.OPEN),
        Bar(2, 11, 12, 10, 11, finality=Finality.OPEN),
        Bar(3, 12, 13, 11, 12, finality=Finality.OPEN),
    ]
    result = BacktestEngine(_cfg(finality_policy="ALLOW_OPEN")).run(BuyFirst, bars=bars)
    assert result.open_trades or result.closed_trades


def test_contracts_dependency_is_exact_rc4_wheel() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert '"openpine-contracts==5.0.0rc6"' in text
    assert "openpine-contracts @ git+" not in text


def test_admit_unknown_policy_and_series_fail_closed() -> None:
    from backtest_engine.core.finality import admit_bars
    from backtest_engine.models import BarSeries

    with pytest.raises(BarValidationError, match="unknown"):
        admit_bars([], policy="MAYBE")
    with pytest.raises(BarValidationError, match="finality"):
        admit_bars(
            BarSeries.from_bars([Bar(1, 1, 1, 1, 1)]),
            policy="CLOSED_BAR_ONLY",
        )


def test_closed_policy_filters_bar_series_by_explicit_finality_vector() -> None:
    from backtest_engine.core.finality import admit_bars
    from backtest_engine.models import BarSeries

    series = BarSeries.from_records(
        [
            {"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "finality": "FINAL"},
            {"time": 2, "open": 2, "high": 2, "low": 2, "close": 2, "finality": "OPEN"},
        ]
    )
    admitted = admit_bars(series, policy="CLOSED_BAR_ONLY")
    assert isinstance(admitted, BarSeries)
    assert list(admitted.time) == [1]
    assert list(admitted.finality or []) == [Finality.FINAL]


def test_closed_bar_series_rejects_missing_unknown_and_misaligned_finality() -> None:
    from backtest_engine.core.finality import admit_bars
    from backtest_engine.models import BarSeries

    bare = BarSeries([1], [1], [1], [1], [1])
    with pytest.raises(BarValidationError, match="explicit finality"):
        admit_bars(bare, policy="CLOSED_BAR_ONLY")
    unknown = BarSeries([1], [1], [1], [1], [1], finality=["MAYBE"])  # type: ignore[list-item]
    with pytest.raises(BarValidationError, match="unknown bar finality"):
        admit_bars(unknown, policy="CLOSED_BAR_ONLY")
    with pytest.raises(ValueError, match="finality length"):
        BarSeries([1], [1], [1], [1], [1], finality=[])
