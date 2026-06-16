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
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 11, 12, 9, 11),
        Bar(3, 8, 10, 7, 8),
        Bar(4, 7, 9, 6, 7),
    ]
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


class FractionalLimitRoundTrip:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1, limit=0.1591)
        if bar_index == 2:
            self.ctx.exit("X", from_entry="L", qty=1, limit=0.1402)


def test_fractional_limit_fills_round_directionally_to_tick_grid():
    bars = [
        Bar(1, 0.1607, 0.1607, 0.1607, 0.1607),
        Bar(2, 0.1568, 0.1568, 0.1568, 0.1568),
        Bar(3, 0.1361, 0.1361, 0.1361, 0.1361),
        Bar(4, 0.1467, 0.1467, 0.1467, 0.1467),
    ]
    r = BacktestEngine(cfg(end_time=4, mintick=0.01, process_orders_on_close=True)).run(
        FractionalLimitRoundTrip, bars=bars
    )
    assert r.closed_trades[0].entry_price == 0.15
    # Buy limits round down; sell limits round up to the next tick, matching
    # TradingView's directional limit price normalization.
    assert r.closed_trades[0].exit_price == 0.15


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


class DefaultCashEntryClose:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long")
        if bar_index == 1:
            self.ctx.close("L")


class DefaultPercentEntryClose(DefaultCashEntryClose):
    pass


class PyramidingCloseByEntryId:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L1", "long")
        if bar_index == 1:
            self.ctx.entry("L2", "long")
        if bar_index == 2:
            self.ctx.close("L1")
            self.ctx.close("L2")


class FractionalStopEntry:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("S", "long", qty=1, stop=0.1639)


class DefaultPercentNextOpenEntry:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long")


def test_default_cash_sizing_uses_fill_price_and_qty_step_floor():
    bars = [
        Bar(1, 0.160714, 0.160714, 0.160714, 0.160714),
        Bar(2, 0.13, 0.13, 0.13, 0.13),
    ]
    r = BacktestEngine(
        cfg(
            end_time=2,
            default_qty_type="cash",
            default_qty_value=100,
            qty_step=1,
            process_orders_on_close=True,
        )
    ).run(DefaultCashEntryClose, bars=bars)
    assert r.closed_trades[0].qty == 622


def test_default_percent_sizing_reserves_percent_commission_and_qty_step_floor():
    bars = [
        Bar(1, 0.160714, 0.160714, 0.160714, 0.160714),
        Bar(2, 0.13, 0.13, 0.13, 0.13),
    ]
    r = BacktestEngine(
        cfg(
            end_time=2,
            default_qty_type="percent_of_equity",
            default_qty_value=10,
            commission_type="percent",
            commission_value=0.1,
            qty_step=1,
            process_orders_on_close=True,
        )
    ).run(DefaultPercentEntryClose, bars=bars)
    assert r.closed_trades[0].qty == 621


def test_default_percent_next_open_entry_sizes_from_creation_close():
    bars = [Bar(1, 100, 100, 100, 100), Bar(2, 110, 110, 110, 110)]
    r = BacktestEngine(
        cfg(
            end_time=2,
            default_qty_type="percent_of_equity",
            default_qty_value=10,
            commission_type="percent",
            commission_value=1,
            process_orders_on_close=False,
        )
    ).run(DefaultPercentNextOpenEntry, bars=bars)
    assert round(r.open_trades[0].qty, 9) == round(100.0 / (100.0 * 1.01), 9)
    assert r.open_trades[0].entry_price == 110


def test_strategy_close_id_closes_matching_pyramid_entry_not_default_lot():
    bars = [Bar(1, 10, 10, 10, 10), Bar(2, 10, 10, 10, 10), Bar(3, 12, 12, 12, 12)]
    r = BacktestEngine(cfg(end_time=3, pyramiding=2, process_orders_on_close=True)).run(
        PyramidingCloseByEntryId, bars=bars
    )
    assert len(r.closed_trades) == 2
    assert len(r.open_trades) == 0
    assert {t.entry_id for t in r.closed_trades} == {"L1", "L2"}


def test_fractional_stop_market_rounds_trigger_to_tick_before_slippage():
    bars = [Bar(1, 0.160714, 0.17, 0.160714, 0.17)]
    r = BacktestEngine(
        cfg(end_time=1, mintick=0.01, slippage=1, process_orders_on_close=True)
    ).run(FractionalStopEntry, bars=bars)
    assert r.open_trades[0].entry_price == 0.18
