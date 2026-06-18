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


class LongTrailingExitFractionalOffset:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index >= 1:
            self.ctx.exit("TR", from_entry="L", qty=1, trail_price=20.0, trail_offset=6.07969)


class LongGapMultipleTargets:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=10)
        if bar_index >= 1:
            self.ctx.exit("TP1", from_entry="L", qty=4, limit=105)
            self.ctx.exit("TP2", from_entry="L", qty=3, limit=110)


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


def test_trailing_stop_fractional_level_rounds_long_sell_up_like_tradingview():
    bars = [
        Bar(1, 20.0, 20.0, 20.0, 20.0),
        Bar(2, 20.0, 20.0, 20.0, 20.0),
        Bar(3, 20.0, 20.87, 18.90, 19.0),
    ]
    r = BacktestEngine(cfg(end_time=3, mintick=0.01)).run(
        LongTrailingExitFractionalOffset, bars=bars
    )
    assert r.closed_trades[0].exit_bar_index == 2
    # Raw trailing stop = 20.87 - (6.07969 * 0.01) = 20.8092031.
    # TV rounds long trailing sell stops upward to the next tick.
    assert r.closed_trades[0].exit_price == 20.81


def test_long_exit_limits_gap_at_open_fill_farthest_target_first_like_tradingview():
    bars = [
        Bar(1, 100, 100, 100, 100),
        Bar(2, 100, 100, 100, 100),
        Bar(3, 112, 112, 90, 100),
    ]
    r = BacktestEngine(cfg(end_time=3, mintick=0.01)).run(
        LongGapMultipleTargets, bars=bars
    )
    assert [trade.exit_id for trade in r.closed_trades[:2]] == ["TP2:L", "TP1:L"]
    assert [trade.exit_price for trade in r.closed_trades[:2]] == [112, 112]


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
