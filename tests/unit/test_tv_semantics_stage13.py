from backtest_engine import BacktestConfig, BacktestEngine, Bar


def cfg(**kw):
    d = dict(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=6,
        commission_type="none",
        initial_capital=1000,
    )
    d.update(kw)
    return BacktestConfig(**d)


class LongTrailingExit:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index >= 1:
            self.ctx.exit("TR", from_entry="L", trail_points=10, trail_offset=5)


class PyramidingHoldThenClose:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L1", "long", qty=1)
        if bar_index == 1:
            self.ctx.entry("L2", "long", qty=1)
        if bar_index == 3:
            self.ctx.close_all()


def test_trailing_stop_created_after_entry_fill_does_not_look_back_intrabar():
    bars = [
        Bar(1, 100, 100, 100, 100),
        Bar(2, 100, 112, 99, 108),
        Bar(3, 106, 120, 90, 100),
    ]
    r = BacktestEngine(cfg(end_time=3)).run(LongTrailingExit, bars=bars)
    assert r.closed_trades[0].exit_bar_index == 2
    assert r.closed_trades[0].exit_price == 115
    assert r.closed_trades[0].profit == 15


def test_intrabar_drawdown_uses_initial_capital_baseline_not_prior_open_profit_peak():
    bars = [
        Bar(1, 100, 100, 100, 100),
        Bar(2, 110, 110, 110, 110),
        Bar(3, 110, 110, 90, 105),
        Bar(4, 105, 105, 105, 105),
        Bar(5, 105, 105, 105, 105),
    ]
    r = BacktestEngine(cfg(end_time=5, pyramiding=2)).run(
        PyramidingHoldThenClose, bars=bars
    )
    assert r.max_drawdown == 40
    assert r.max_drawdown_percent == 4.0
