from backtest_engine import BacktestConfig, BacktestEngine, Bar


def cfg(**kw):
    d = dict(symbol="S", timeframe="1D", start_time=1, end_time=10, commission_type="none")
    d.update(kw)
    return BacktestConfig(**d)


class CurrentBarLimitExitOnlySeesClose:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 1:
            self.ctx.exit("X", from_entry="L", qty=1, limit=15)


class CurrentBarProfitLossExitOnlySeesClose:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 1:
            self.ctx.exit("XP", from_entry="L", qty=1, profit=5, loss=2)


class CurrentBarStopLimitOnlySeesClose:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.order("SL", "long", qty=1, stop=15, limit=13)


class CurrentBarTrailingOnlySeesClose:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 1:
            self.ctx.exit("TR", from_entry="L", qty=1, trail_price=15, trail_offset=1)


class CurrentBarCloseAllAtClose:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 1:
            self.ctx.close_all()


def test_process_orders_on_close_limit_exit_does_not_look_back_intrabar():
    bars = [Bar(1, 10, 10, 10, 10), Bar(2, 10, 15, 9, 10), Bar(3, 10, 15, 10, 10)]
    r = BacktestEngine(cfg(process_orders_on_close=True)).run(CurrentBarLimitExitOnlySeesClose, bars=bars)
    assert r.closed_trades[0].exit_bar_index == 2
    assert r.closed_trades[0].exit_price == 15


def test_process_orders_on_close_profit_loss_exit_does_not_look_back_intrabar():
    bars = [Bar(1, 10, 10, 10, 10), Bar(2, 10, 15, 8, 10), Bar(3, 10, 15, 10, 10)]
    r = BacktestEngine(cfg(process_orders_on_close=True)).run(CurrentBarProfitLossExitOnlySeesClose, bars=bars)
    assert r.closed_trades[0].exit_bar_index == 2
    assert r.closed_trades[0].exit_id == "XP:L"
    assert r.closed_trades[0].exit_price == 15


def test_process_orders_on_close_stop_limit_does_not_look_back_intrabar():
    bars = [Bar(1, 14, 15, 12, 14), Bar(2, 13, 15, 13, 13)]
    r = BacktestEngine(cfg(process_orders_on_close=True)).run(CurrentBarStopLimitOnlySeesClose, bars=bars)
    assert r.open_trades[0].entry_bar_index == 1
    assert r.open_trades[0].entry_price == 13


def test_process_orders_on_close_trailing_exit_does_not_look_back_intrabar():
    bars = [Bar(1, 10, 10, 10, 10), Bar(2, 10, 16, 9, 10), Bar(3, 14, 16, 14, 14)]
    r = BacktestEngine(cfg(process_orders_on_close=True)).run(CurrentBarTrailingOnlySeesClose, bars=bars)
    assert r.closed_trades[0].exit_bar_index == 2
    assert r.closed_trades[0].exit_price == 15


def test_process_orders_on_close_close_all_fills_current_close():
    bars = [Bar(1, 10, 10, 10, 10), Bar(2, 20, 20, 20, 20)]
    r = BacktestEngine(cfg(process_orders_on_close=True)).run(CurrentBarCloseAllAtClose, bars=bars)
    assert r.closed_trades[0].exit_bar_index == 1
    assert r.closed_trades[0].exit_price == 20
