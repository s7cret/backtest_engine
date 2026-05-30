import pytest

from backtest_engine import BacktestConfig, BacktestEngine, Bar


class TwoTrades:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("W", "long")
        if bar_index == 1:
            self.ctx.close("W")
        if bar_index == 2:
            self.ctx.entry("L", "long")
        if bar_index == 3:
            self.ctx.close("L")


def test_stage14_p1_trade_metrics_and_commission_total():
    bars = [
        Bar(1, 100, 100, 100, 100),
        Bar(2, 100, 102, 99, 102),
        Bar(3, 102, 103, 101, 103),
        Bar(4, 103, 103, 99, 99),
        Bar(5, 99, 100, 98, 98),
    ]
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=5,
        initial_capital=10000,
        commission_type="percent",
        commission_value=1.0,
        process_orders_on_close=True,
    )

    result = BacktestEngine(cfg).run(TwoTrades, bars=bars)

    assert result.total_trades == 2
    # Trade profit is net of both entry and exit commission.
    assert result.avg_trade == pytest.approx((-0.02 - 6.02) / 2)
    assert result.largest_win == 0.0
    assert result.largest_loss == pytest.approx(6.02)
    assert result.avg_bars_in_trade == 1.0
    assert result.commission_total == 1.0 + 1.02 + 1.03 + 0.99


class LongShortExcursions:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long")
        if bar_index == 1:
            self.ctx.close("L")
        if bar_index == 2:
            self.ctx.entry("S", "short")
        if bar_index == 3:
            self.ctx.close("S")


def test_trade_max_runup_drawdown_are_stored_for_long_and_short():
    bars = [
        Bar(1, 100, 100, 100, 100),
        Bar(2, 100, 110, 95, 105),
        Bar(3, 105, 105, 105, 105),
        Bar(4, 105, 108, 90, 95),
        Bar(5, 95, 95, 95, 95),
    ]
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=5,
        initial_capital=10000,
        commission_type="none",
        commission_value=0.0,
        process_orders_on_close=True,
        collect_trade_details=True,
    )

    result = BacktestEngine(cfg).run(LongShortExcursions, bars=bars)

    assert result.closed_trades is not None
    long_trade, short_trade = result.closed_trades
    assert long_trade.max_runup == pytest.approx(10.0)
    assert long_trade.max_drawdown == pytest.approx(5.0)
    assert short_trade.max_runup == pytest.approx(15.0)
    assert short_trade.max_drawdown == pytest.approx(3.0)
    assert result.max_runup == pytest.approx(15.0)


class RiskMaxPositionStrategy:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.risk_max_position_size(1)
            self.ctx.entry("too_large", "long", qty=2)
        if bar_index == 1:
            self.ctx.entry("allowed", "long", qty=1)


def test_risk_max_position_size_is_enforced_by_engine():
    bars = [
        Bar(1, 100, 100, 100, 100),
        Bar(2, 100, 100, 100, 100),
        Bar(3, 100, 100, 100, 100),
    ]
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=3,
        commission_type="none",
        process_orders_on_close=True,
    )

    result = BacktestEngine(cfg).run(RiskMaxPositionStrategy, bars=bars)

    assert result.open_trades is not None
    assert [trade.entry_id for trade in result.open_trades] == ["allowed"]
    assert any(
        d.code == "ORDER_REJECTED_RISK_MAX_POSITION_SIZE"
        for d in result.warnings
    )


class RiskAllowEntryStrategy:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.risk_allow_entry_in("short")
            self.ctx.entry("blocked_long", "long", qty=1)
        if bar_index == 1:
            self.ctx.entry("allowed_short", "short", qty=1)


def test_risk_allow_entry_direction_is_enforced_by_engine():
    bars = [
        Bar(1, 100, 100, 100, 100),
        Bar(2, 100, 100, 100, 100),
        Bar(3, 100, 100, 100, 100),
    ]
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=3,
        commission_type="none",
        process_orders_on_close=True,
    )

    result = BacktestEngine(cfg).run(RiskAllowEntryStrategy, bars=bars)

    assert result.open_trades is not None
    assert [trade.entry_id for trade in result.open_trades] == ["allowed_short"]
