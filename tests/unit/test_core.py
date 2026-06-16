import pytest
from backtest_engine import BacktestConfig, BacktestEngine, Bar, BarSeries
from backtest_engine.models import Trade
from backtest_engine.broker.fill_simulator import build_price_path
from backtest_engine.broker.commission import calculate_commission
from backtest_engine.errors import BarValidationError
from backtest_engine.core.validation import validate_bars

BARS = [
    Bar(1, 10, 11, 9, 10),
    Bar(2, 12, 13, 11, 12),
    Bar(3, 14, 15, 13, 14),
    Bar(4, 13, 14, 10, 11),
]


class BuyOnce:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)


class BuyClose:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 2:
            self.ctx.close("L", immediately=True)


class LimitStop:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx
        self.kind = params["kind"]

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            if self.kind == "limit":
                self.ctx.entry("L", "long", qty=1, limit=11)
            if self.kind == "stop":
                self.ctx.entry("L", "long", qty=1, stop=13)
            if self.kind == "stop_limit":
                self.ctx.entry("L", "long", qty=1, stop=13, limit=12)


def cfg(**kw):
    d = dict(
        symbol="S", timeframe="1D", start_time=1, end_time=4, commission_type="none"
    )
    d.update(kw)
    return BacktestConfig(**d)


def test_barseries_and_validation():
    s = BarSeries.from_bars(BARS)
    assert len(s) == 4
    assert s.get_bar(0).open == 10
    with pytest.raises(BarValidationError):
        validate_bars(BarSeries.from_bars([Bar(1, 1, 0, 1, 1)]))


def test_ohlc_path_tie_and_variants():
    assert [p for _, p in build_price_path(Bar(1, 10, 12, 8, 10))] == [
        "open",
        "low",
        "high",
        "close",
    ]
    assert [p for _, p in build_price_path(Bar(1, 10, 11, 5, 10))] == [
        "open",
        "high",
        "low",
        "close",
    ]
    assert [p for _, p in build_price_path(Bar(1, 10, 15, 9, 10))] == [
        "open",
        "low",
        "high",
        "close",
    ]


def test_market_next_open_and_close_immediate():
    r = BacktestEngine(cfg()).run(BuyOnce, bars=BARS)
    assert r.open_trades[0].entry_price == 12
    r2 = BacktestEngine(cfg(process_orders_on_close=True)).run(BuyClose, bars=BARS)
    assert r2.closed_trades and r2.closed_trades[0].exit_price == 14


def test_update_state_tracks_closed_trade_stats_incrementally():
    engine = BacktestEngine(cfg())
    engine.closed_trades.extend(
        [
            Trade("t1", "L1", "X1", "long", 1, 0, 10, 2, 1, 15, 1, 0, 0, 5, 0),
            Trade("t2", "L2", "X2", "long", 2, 1, 15, 3, 2, 12, 1, 0, 0, -3, 0),
            Trade("t3", "L3", "X3", "long", 3, 2, 12, 4, 3, 12, 1, 0, 0, 0, 0),
        ]
    )

    engine._update_state()
    engine._update_state()

    assert engine.state.gross_profit == 5
    assert engine.state.gross_loss == 3
    assert engine.state.win_trades == 1
    assert engine.state.loss_trades == 1
    assert engine.state.even_trades == 1


def test_limit_stop_stoplimit():
    assert (
        BacktestEngine(cfg())
        .run(LimitStop, {"kind": "limit"}, BARS)
        .open_trades[0]
        .entry_price
        == 11
    )
    assert (
        BacktestEngine(cfg())
        .run(LimitStop, {"kind": "stop"}, BARS)
        .open_trades[0]
        .entry_price
        == 13
    )
    # stop-limit activates at 13 then waits for 12 in later path/bar
    assert (
        BacktestEngine(cfg())
        .run(LimitStop, {"kind": "stop_limit"}, BARS)
        .open_trades[0]
        .entry_price
        == 12
    )


def test_commission():
    assert calculate_commission(100, 2, "percent", 1) == 2
    assert calculate_commission(100, 2, "fixed_per_order", 3) == 3
    assert calculate_commission(100, 2, "fixed_per_contract", 3) == 6


def test_pyramiding_reject_and_force_close():
    class Twice:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx

        def _process_bar(self, bar, bar_index):
            if bar_index in (0, 1):
                self.ctx.entry("L" + str(bar_index), "long", qty=1)

    r = BacktestEngine(cfg(force_close_on_end=True)).run(Twice, bars=BARS)
    assert any(d.code == "ORDER_REJECTED_PYRAMIDING" for d in r.warnings)
    assert r.closed_trades


def test_pyramiding_limit_is_max_same_direction_entries_not_plus_one():
    class EveryBarEntry:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx

        def _process_bar(self, bar, bar_index):
            if bar_index in (0, 1, 2):
                self.ctx.entry("L" + str(bar_index), "long", qty=1)

    bars = BARS + [Bar(5, 13, 14, 10, 11)]
    result = BacktestEngine(cfg(pyramiding=2, force_close_on_end=True, end_time=5)).run(
        EveryBarEntry, bars=bars
    )

    assert [trade.entry_id for trade in (result.closed_trades or [])] == ["L0", "L1"]
    assert any(d.code == "ORDER_REJECTED_PYRAMIDING" for d in result.warnings)


def test_same_id_pending_entry_modification_bypasses_pyramiding_limit():
    class ModifyPendingStopEntry:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx

        def _process_bar(self, bar, bar_index):
            if bar_index == 0:
                self.ctx.entry("L", "long", qty=1, stop=20)
            if bar_index == 1:
                self.ctx.entry("L", "long", qty=1, stop=12)

    bars = [
        Bar(1, 10, 11, 9, 10),
        Bar(2, 10, 11, 9, 10),
        Bar(3, 10, 13, 9, 10),
    ]
    result = BacktestEngine(cfg(end_time=3, pyramiding=0)).run(
        ModifyPendingStopEntry, bars=bars
    )

    assert [trade.entry_price for trade in (result.open_trades or [])] == [12]
    assert any(event.code == "ORDER_MODIFIED" for event in (result.events or []))
    assert not any(
        warning.code == "ORDER_REJECTED_PYRAMIDING" for warning in (result.warnings or [])
    )


def test_strategy_exit_profit_and_loss_are_ticks_not_price_delta():
    class ProfitLossTicks:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx

        def _process_bar(self, bar, bar_index):
            if bar_index == 0:
                self.ctx.entry("L", "long", qty=1)
            if bar_index == 1:
                self.ctx.exit("X", "L", qty=1, profit=10, loss=5)

    bars = [
        Bar(1, 100.00, 100.00, 100.00, 100.00),
        Bar(2, 100.00, 100.00, 100.00, 100.00),
        Bar(3, 100.00, 100.11, 99.99, 100.00),
    ]
    result = BacktestEngine(cfg(end_time=3, mintick=0.01)).run(
        ProfitLossTicks, bars=bars
    )

    assert result.closed_trades
    assert result.closed_trades[0].exit_id == "X:L"
    assert result.closed_trades[0].exit_price == pytest.approx(100.10)


def test_early_stop_and_preloaded():
    c = cfg(early_stop_enabled=True, min_equity_stop=9999)
    r = BacktestEngine(c).run(BuyOnce, bars=BARS)
    assert r.status == "early_stopped"
