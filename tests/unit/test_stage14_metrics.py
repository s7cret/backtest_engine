import pytest

from backtest_engine import BacktestConfig, BacktestEngine, Bar
from backtest_engine.errors import UnsupportedRiskRuleError
from backtest_engine.context import StrategyContext


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


def test_trade_max_runup_drawdown_include_entry_commission_like_tradingview():
    bars = [
        Bar(1, 100, 100, 100, 100),
        Bar(2, 100, 110, 95, 105),
        Bar(3, 105, 105, 105, 105),
    ]
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=3,
        initial_capital=10000,
        commission_type="fixed_per_order",
        commission_value=2.0,
        process_orders_on_close=True,
        collect_trade_details=True,
    )

    result = BacktestEngine(cfg).run(LongShortExcursions, bars=bars)

    assert result.closed_trades is not None
    trade = result.closed_trades[0]
    assert trade.mfe == pytest.approx(10.0)
    assert trade.mae == pytest.approx(-5.0)
    assert trade.commission_entry == pytest.approx(2.0)
    assert trade.max_runup == pytest.approx(8.0)
    assert trade.max_drawdown == pytest.approx(7.0)


class ShortExitNextOpenExcursion:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("S", "short", qty=1)
        if bar_index == 1:
            self.ctx.close("S")


def test_short_exit_on_next_open_excludes_exit_bar_high_from_trade_drawdown():
    bars = [
        Bar(1, 100, 100, 100, 100),
        Bar(2, 100, 100, 90, 95),
        Bar(3, 95, 200, 95, 100),
    ]
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=3,
        initial_capital=10000,
        commission_type="none",
        commission_value=0.0,
        process_orders_on_close=False,
        collect_trade_details=True,
    )

    result = BacktestEngine(cfg).run(ShortExitNextOpenExcursion, bars=bars)

    assert result.closed_trades is not None
    trade = result.closed_trades[0]
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_price == pytest.approx(95.0)
    assert trade.profit == pytest.approx(5.0)
    assert trade.max_runup == pytest.approx(10.0)
    assert trade.max_drawdown == pytest.approx(0.0)


class LongExitNextOpenExcursion:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 1:
            self.ctx.close("L")


def test_long_exit_on_next_open_excludes_exit_bar_low_from_trade_drawdown():
    bars = [
        Bar(1, 100, 100, 100, 100),
        Bar(2, 100, 110, 100, 105),
        Bar(3, 105, 105, 50, 100),
    ]
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=3,
        initial_capital=10000,
        commission_type="none",
        commission_value=0.0,
        process_orders_on_close=False,
        collect_trade_details=True,
    )

    result = BacktestEngine(cfg).run(LongExitNextOpenExcursion, bars=bars)

    assert result.closed_trades is not None
    trade = result.closed_trades[0]
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_price == pytest.approx(105.0)
    assert trade.profit == pytest.approx(5.0)
    assert trade.max_runup == pytest.approx(10.0)
    assert trade.max_drawdown == pytest.approx(0.0)


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


class RiskMaxPositionPendingEntriesStrategy:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.risk_max_position_size(1)
            self.ctx.entry("first", "long", qty=1)
            self.ctx.entry("second", "long", qty=1)


def test_risk_max_position_size_counts_pending_same_bar_entries():
    bars = [
        Bar(1, 100, 100, 100, 100),
        Bar(2, 100, 100, 100, 100),
    ]
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=2,
        commission_type="none",
        process_orders_on_close=True,
        pyramiding=2,
    )

    result = BacktestEngine(cfg).run(RiskMaxPositionPendingEntriesStrategy, bars=bars)

    assert result.open_trades is not None
    assert [trade.entry_id for trade in result.open_trades] == ["first"]
    assert any(
        d.code == "ORDER_REJECTED_RISK_MAX_POSITION_SIZE"
        for d in result.warnings
    )


def test_strategy_context_registers_risk_rules_without_mutating_config():
    cfg = BacktestConfig(symbol="S", timeframe="1D", start_time=1, end_time=1)
    ctx = StrategyContext(cfg)

    ctx.risk_allow_entry_in("short")
    ctx.risk_max_drawdown(10, "percent_of_equity")
    ctx.risk_max_position_size(1)

    assert cfg.allow_long is True
    assert cfg.allow_short is True
    assert cfg.max_drawdown_stop_percent is None
    assert cfg.max_position_size is None
    assert [(r.name, r.value, r.value_type, r.direction) for r in ctx.risk_rules] == [
        ("allow_entry_in", None, None, "short"),
        ("max_drawdown", 10.0, "percent_of_equity", None),
        ("max_position_size", 1.0, "fixed", None),
    ]


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


class RiskAllowEntryCloseOnlyStrategy:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("long", "long", qty=2)
        if bar_index == 1:
            self.ctx.risk_allow_entry_in("long")
            self.ctx.entry("short_reduces", "short", qty=1)


def test_risk_allow_entry_in_opposite_direction_reduces_existing_position():
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

    result = BacktestEngine(cfg).run(RiskAllowEntryCloseOnlyStrategy, bars=bars)

    assert result.closed_trades is not None
    assert [(trade.entry_id, trade.exit_id, trade.qty) for trade in result.closed_trades] == [
        ("long", "short_reduces", 1.0)
    ]
    assert result.open_trades is not None
    assert [(trade.entry_id, trade.direction, trade.qty) for trade in result.open_trades] == [
        ("long", "long", 1.0)
    ]


class RiskStateMutationStrategy:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.risk_allow_entry_in("short")
            self.ctx.risk_max_drawdown(1, "percent_of_equity")
            self.ctx.risk_max_position_size(1)


class PlainLongStrategy:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("long", "long", qty=2)


def test_engine_risk_rules_do_not_mutate_shared_config_between_runs():
    bars = [
        Bar(1, 100, 100, 100, 100),
        Bar(2, 100, 100, 100, 100),
    ]
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=2,
        commission_type="none",
        process_orders_on_close=True,
    )

    BacktestEngine(cfg).run(RiskStateMutationStrategy, bars=bars)
    result = BacktestEngine(cfg).run(PlainLongStrategy, bars=bars)

    assert cfg.allow_long is True
    assert cfg.allow_short is True
    assert cfg.early_stop_enabled is False
    assert cfg.max_drawdown_stop_percent is None
    assert cfg.max_position_size is None
    assert result.open_trades is not None
    assert [(trade.entry_id, trade.qty) for trade in result.open_trades] == [("long", 2.0)]


class RiskCashDrawdownStrategy:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.risk_max_drawdown(5, "cash")
            self.ctx.entry("long", "long", qty=1)


def test_risk_max_drawdown_cash_uses_peak_equity_not_initial_capital():
    bars = [
        Bar(1, 100, 100, 100, 100),
        Bar(2, 100, 120, 100, 120),
        Bar(3, 120, 120, 114, 114),
        Bar(4, 114, 114, 114, 114),
    ]
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=4,
        commission_type="none",
        process_orders_on_close=True,
    )

    result = BacktestEngine(cfg).run(RiskCashDrawdownStrategy, bars=bars)

    assert result.status == "early_stopped"
    assert result.early_stop_reason == "max_drawdown_stop_cash"


class UnsupportedIntradayRiskStrategy:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.risk_max_intraday_loss(10, "percent_of_equity")


def test_unsupported_intraday_risk_rule_fails_closed():
    bars = [
        Bar(1, 100, 100, 100, 100),
        Bar(2, 100, 100, 100, 100),
    ]
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1D",
        start_time=1,
        end_time=2,
        commission_type="none",
    )

    with pytest.raises(UnsupportedRiskRuleError, match="max_intraday_loss"):
        BacktestEngine(cfg).run(UnsupportedIntradayRiskStrategy, bars=bars)
