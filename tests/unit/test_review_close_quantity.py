"""Reduce-only closes cannot generate fills larger than the remaining entry."""

from dataclasses import replace

from backtest_engine import BacktestEngine
from tests.unit.test_engine_projection import _bars, _plain_config


class CloseEveryCallback:
    required_runtime_capabilities = ()

    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def run_bar(self, bar, index):
        if index == 0:
            self.ctx.entry("L", "long", qty=1)
        if index == 1:
            self.ctx.close("L", qty=100)


def test_oversized_repeated_close_has_no_phantom_fills_or_reversal():
    engine = BacktestEngine(
        replace(_plain_config(), calc_on_order_fills=True, force_close_on_end=False)
    )
    result = engine.run(CloseEveryCallback, bars=_bars())
    assert result.status == "completed"
    assert [fill.qty for fill in engine.fills] == [1, 1]
    assert engine.position.size == 0
    assert not engine.open_trades
    assert len(engine.closed_trades) == 1
    assert engine.closed_trades[0].qty == 1
    assert not any(d.code == "MAX_RECALC_DEPTH_REACHED" for d in engine.warnings)


class CloseNamedPercent:
    required_runtime_capabilities = ()

    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def run_bar(self, bar, index):
        if index == 0:
            self.ctx.entry("A", "long", qty=4)
            self.ctx.entry("B", "long", qty=10)
        if index == 1:
            self.ctx.close("A", qty_percent=50)


def test_named_percentage_uses_matching_entry_quantity():
    engine = BacktestEngine(replace(_plain_config(), pyramiding=2, force_close_on_end=False))
    engine.run(CloseNamedPercent, bars=_bars())
    assert [fill.qty for fill in engine.fills] == [4, 10, 2]
    assert engine.position.size == 12
