from backtest_engine import BacktestConfig, BacktestEngine, Bar


def cfg(**kw):
    d = dict(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=5,
        commission_type="none",
        initial_capital=1000,
    )
    d.update(kw)
    return BacktestConfig(**d)


class LongMarketableLimitExit:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 2:
            self.ctx.exit("X", from_entry="L", qty=1, limit=5)


class LongTouchedTakeProfit:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 2:
            self.ctx.exit("X", from_entry="L", qty=1, limit=13)


class ShortMarketableLimitExit:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("S", "short", qty=1)
        if bar_index == 2:
            self.ctx.exit("X", from_entry="S", qty=1, limit=20)


class LongPendingLimit:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 2:
            self.ctx.exit("X", from_entry="L", qty=1, limit=20)


def test_long_marketable_limit_exit_fills_next_open_not_literal_limit():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 11, 12, 9, 11),
        Bar(3, 12, 13, 10, 12),
        Bar(4, 15, 16, 14, 15),
    ]
    r = BacktestEngine(cfg(end_time=4)).run(LongMarketableLimitExit, bars=bars)
    assert r.closed_trades[0].exit_price == 15
    assert r.closed_trades[0].profit == 4


def test_long_take_profit_limit_touched_intrabar_fills_at_limit():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 11, 12, 9, 11),
        Bar(3, 12, 12, 10, 12),
        Bar(4, 12, 14, 10, 12),
    ]
    r = BacktestEngine(cfg(end_time=4)).run(LongTouchedTakeProfit, bars=bars)
    assert r.closed_trades[0].exit_price == 13


def test_short_marketable_limit_exit_fills_next_open_not_literal_limit():
    bars = [Bar(1, 10, 10, 10, 10), Bar(2, 11, 12, 9, 11), Bar(3, 8, 10, 7, 8), Bar(4, 7, 9, 6, 7)]
    r = BacktestEngine(cfg(end_time=4)).run(ShortMarketableLimitExit, bars=bars)
    assert r.closed_trades[0].exit_price == 7
    assert r.closed_trades[0].profit == 4


def test_non_marketable_limit_remains_pending_until_touched():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 11, 12, 9, 11),
        Bar(3, 12, 13, 10, 12),
        Bar(4, 14, 19, 13, 14),
        Bar(5, 14, 21, 13, 14),
    ]
    r = BacktestEngine(cfg(end_time=5)).run(LongPendingLimit, bars=bars)
    assert r.closed_trades[0].exit_price == 20
    assert r.closed_trades[0].exit_bar_index == 4


def test_intrabar_drawdown_uses_open_trade_adverse_price():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 10, 11, 5, 10),
        Bar(3, 10, 10, 10, 10),
        Bar(4, 10, 10, 10, 10),
    ]
    r = BacktestEngine(cfg(end_time=4)).run(LongMarketableLimitExit, bars=bars)
    assert r.max_drawdown == 5
    assert r.max_drawdown_percent == 0.5
    assert r.gross_loss == 0
