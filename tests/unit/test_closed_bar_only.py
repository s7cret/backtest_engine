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


def test_contracts_pin_is_exact_git_sha() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert (
        "openpine-contracts @ git+https://github.com/s7cret/openpine-contracts.git@"
        in text
    )
    assert "51e32ebaaf02eecb81443e8ca7e89b2543cb25a3" in text


def test_admit_unknown_policy_and_series_fail_closed() -> None:
    from backtest_engine.core.finality import admit_bars
    from backtest_engine.models import BarSeries

    with pytest.raises(BarValidationError, match="unknown"):
        admit_bars([], policy="MAYBE")
    with pytest.raises(BarValidationError, match="list\\[Bar\\]"):
        admit_bars(
            BarSeries.from_bars([Bar(1, 1, 1, 1, 1)]),
            policy="CLOSED_BAR_ONLY",
        )
