from __future__ import annotations

import sys

import pytest

PINELIB_REPO = "[local-home]/pinelib"
if PINELIB_REPO not in sys.path:
    sys.path.insert(0, PINELIB_REPO)
pytest.importorskip("pinelib")

from backtest_engine import BacktestConfig, BacktestEngine  # noqa: E402
from backtest_engine.adapters.generated_strategy import (  # noqa: E402
    UnsupportedGeneratedStrategySemantics,
    make_generated_strategy_adapter,
)
from backtest_engine.models import Bar  # noqa: E402


class GeneratedLikeStrategy:
    def __init__(self, params=None, runtime=None):
        self.params = params or {}
        self.rt = runtime
        self.ctx = None

    def _process_bar(self, bar):
        del bar
        idx = self.rt.bar_index_series.current
        if idx == 1:
            self.ctx.entry("L", "long")
        if idx == 4:
            self.ctx.close("L")


def test_generated_strategy_adapter_runs_orders_through_backtest_engine() -> None:
    strategy_class = make_generated_strategy_adapter(GeneratedLikeStrategy)
    bars = [Bar(i, 100 + i, 101 + i, 99 + i, 100 + i, 1.0) for i in range(6)]
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=5,
        commission_type="none",
        commission_value=0.0,
        default_qty_type="fixed",
        default_qty_value=1.0,
        force_close_on_end=False,
    )

    result = BacktestEngine(config).run(strategy_class, bars=bars)

    assert result.status == "completed"
    assert result.total_trades == 1
    assert result.net_profit == pytest.approx(3.0)
    assert result.closed_trades is not None
    assert result.closed_trades[0].entry_bar_index == 2
    assert result.closed_trades[0].exit_bar_index == 5


class _Declaration:
    calc_on_order_fills = True
    calc_on_every_tick = False
    use_bar_magnifier = False
    margin_long = 100.0
    margin_short = 100.0


class _GeneratedCtx:
    declaration = _Declaration()


class UnsupportedGeneratedLikeStrategy:
    def __init__(self, params=None, runtime=None):
        del params, runtime
        self.ctx = _GeneratedCtx()


def test_generated_strategy_adapter_fails_closed_for_unsupported_recalc_semantics() -> None:
    strategy_class = make_generated_strategy_adapter(UnsupportedGeneratedLikeStrategy)
    bars = [Bar(0, 1, 1, 1, 1, 1.0)]
    config = BacktestConfig(symbol="TEST", timeframe="1", start_time=0, end_time=0)

    with pytest.raises(UnsupportedGeneratedStrategySemantics, match="calc_on_order_fills"):
        BacktestEngine(config).run(strategy_class, bars=bars)


class _MatchingDeclaration:
    initial_capital = 10000.0
    default_qty_type = "fixed"
    default_qty_value = 1.0
    pyramiding = 0
    commission_type = "none"
    commission_value = 0.0
    slippage = 0.0
    process_orders_on_close = False
    margin_long = 100.0
    margin_short = 100.0
    calc_on_order_fills = False
    calc_on_every_tick = False
    use_bar_magnifier = False


class _GeneratedCtxMatching:
    declaration = _MatchingDeclaration()


class GeneratedWithMatchingDeclaration(GeneratedLikeStrategy):
    def __init__(self, params=None, runtime=None):
        super().__init__(params=params, runtime=runtime)
        self.ctx = _GeneratedCtxMatching()


def test_generated_strategy_adapter_config_handshake_accepts_empty_diff() -> None:
    strategy_class = make_generated_strategy_adapter(GeneratedWithMatchingDeclaration)
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=1,
        commission_type="none",
        commission_value=0.0,
    )
    result = BacktestEngine(config).run(strategy_class, bars=[Bar(0, 1, 1, 1, 1), Bar(1, 1, 1, 1, 1)])
    assert result.status == "completed"


def test_generated_strategy_adapter_config_handshake_rejects_mismatch() -> None:
    strategy_class = make_generated_strategy_adapter(GeneratedWithMatchingDeclaration)
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=1,
        initial_capital=20000.0,
        commission_type="none",
        commission_value=0.0,
    )
    with pytest.raises(UnsupportedGeneratedStrategySemantics, match="initial_capital"):
        BacktestEngine(config).run(strategy_class, bars=[Bar(0, 1, 1, 1, 1), Bar(1, 1, 1, 1, 1)])
