from __future__ import annotations

import pinelib
import pytest
from pinelib.errors import PineStrategyError

from backtest_engine import BacktestConfig, BacktestEngine, Bar

BARS = [
    Bar(1, 10, 10, 10, 10, time_close=2),
    Bar(2, 12, 12, 12, 12, time_close=3),
    Bar(3, 15, 15, 15, 15, time_close=4),
    Bar(4, 15, 15, 15, 15, time_close=5),
]


class MarketEntryCloseAll:
    def __init__(self, params, runtime, ctx):
        del params, runtime
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        del bar
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=2)
        if bar_index == 2:
            self.ctx.close_all()


def test_backtest_engine_is_the_authoritative_fill_and_trade_ledger() -> None:
    result = BacktestEngine(
        BacktestConfig(
            symbol="S",
            timeframe="1D",
            start_time=1,
            end_time=10,
            initial_capital=100_000.0,
            commission_type="percent",
            commission_value=0.0,
        )
    ).run(MarketEntryCloseAll, bars=BARS)

    assert result.closed_trades is not None
    assert len(result.closed_trades) == 1
    trade = result.closed_trades[0]
    assert trade.qty == 2
    assert trade.entry_price == 12
    assert trade.exit_price == 15
    assert result.net_profit == 6
    assert result.final_equity == 100_006


def test_pinelib_records_intents_but_refuses_to_create_a_second_fill_ledger() -> None:
    strategy = pinelib.StrategyContext()
    runtime = pinelib.PineRuntime(
        pinelib.SymbolInfo("S", mintick=0.01),
        pinelib.TimeframeInfo.from_string("D"),
        config=pinelib.RuntimeConfig(),
    )
    strategy.attach_runtime(runtime)
    bar = pinelib.Bar(1, 10, 10, 10, 10, volume=0.0, time_close=2)
    runtime.begin_bar(bar)
    strategy.entry("L", "long", qty=2)

    with pytest.raises(PineStrategyError, match="records order intents only"):
        strategy.process_orders_for_bar(runtime=runtime, bar=bar)

    assert len(strategy.pending_orders) == 1
    assert strategy.pending_orders[0].id == "L"
