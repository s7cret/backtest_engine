"""Cancellation names the public exit ID, not an internal TP/SL child ID."""

import pytest
from backtest_engine import BacktestConfig, BacktestEngine
from backtest_engine.models import Bar
from openpine_contracts import Finality


@pytest.mark.parametrize("cancel_all", [False, True])
def test_cancel_exit_parent_removes_every_leg_not_an_unrelated_order(cancel_all):
    class Strategy:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx

        def run_bar(self, bar, bar_index):
            if bar_index == 0:
                self.ctx.entry("L", "long", qty=2)
            if bar_index == 1:
                self.ctx.exit("X", from_entry="L", limit=200, stop=50)
                self.ctx.order("other", "long", qty=1, limit=10)
            if bar_index == 2:
                if cancel_all:
                    self.ctx.cancel_all()
                else:
                    self.ctx.cancel("X")

    cfg = BacktestConfig(
        "S", "1m", 0, 240_000, commission_type="none", commission_value=0, force_close_on_end=False
    )
    engine = BacktestEngine(cfg)
    result = engine.run(
        Strategy,
        bars=[
            Bar(
                time=i * 60_000,
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1,
                finality=Finality.FINAL,
            )
            for i in range(4)
        ],
    )
    assert result.status == "completed"
    orders = {o.id: o for o in engine.orders}
    assert orders["X:L"].status == orders["X:S"].status == "cancelled"
    assert orders["other"].status == ("cancelled" if cancel_all else "active")
    assert len(result.closed_trades) == 0
