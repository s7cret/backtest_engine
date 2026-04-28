import pytest
from backtest_engine import BacktestConfig, BacktestEngine, Bar, BacktestCallbacks
from backtest_engine.errors import ConfigError
from backtest_engine.results import compare_trades

BARS = [Bar(1, 10, 11, 9, 10), Bar(2, 12, 13, 11, 12), Bar(3, 14, 16, 13, 15), Bar(4, 15, 16, 8, 9)]


def cfg(**kw):
    d = dict(symbol="S", timeframe="1D", start_time=1, end_time=4, commission_type="none")
    d.update(kw)
    return BacktestConfig(**d)


class BuyAtVisibleIndex:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx
        self.seen = []

    def _process_bar(self, bar, bar_index):
        self.seen.append(bar.time)
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)


class BuyThenReadState:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx
        self.reads = []

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 2:
            self.reads.append(self.ctx.state.opentrades_entry_id(0))
            self.ctx.close("L", immediately=True)


class Provider:
    def __init__(self):
        self.called = 0
        self.lower_called = 0

    def get_bars(self, *a):
        self.called += 1
        return BARS

    def get_lower_tf_bars(self, *a):
        self.lower_called += 1
        # Better intrabar path reaches long limit at 10 before parent synthetic high/low ambiguity matters.
        return [Bar(20, 12, 12, 10, 11)]


class LimitEntry:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1, limit=10)


def test_run_slices_to_config_range_and_honors_max_bars_back():
    r = BacktestEngine(cfg(start_time=2, end_time=3, max_bars_back=1)).run(
        BuyAtVisibleIndex, bars=BARS
    )
    assert r.bars_processed == 3
    assert r.equity_curve[0].time == 1
    assert r.equity_curve[-1].time == 3


def test_engine_resets_state_when_reused():
    e = BacktestEngine(cfg())
    r1 = e.run(BuyAtVisibleIndex, bars=BARS)
    r2 = e.run(BuyAtVisibleIndex, bars=BARS)
    assert len(r1.open_trades) == 1
    assert len(r2.open_trades) == 1


def test_state_view_trade_methods_are_backed_by_engine_trades():
    r = BacktestEngine(cfg(process_orders_on_close=True)).run(BuyThenReadState, bars=BARS)
    assert r.closed_trades[0].entry_id == "L"


def test_streaming_drawdown_without_equity_curve_is_preserved():
    class Buy:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx

        def _process_bar(self, bar, bar_index):
            if bar_index == 0:
                self.ctx.entry("L", "long", qty=1)

    r = BacktestEngine(cfg(collect_equity_curve=False)).run(Buy, bars=BARS)
    assert r.equity_curve is None
    assert r.max_drawdown > 0


def test_callback_disable_policy_records_once_and_continues():
    calls = {"n": 0}

    def bad(*args):
        calls["n"] += 1
        raise RuntimeError("boom")

    cbs = BacktestCallbacks(on_bar_start=bad)
    r = BacktestEngine(cfg(callback_error_policy="disable_callbacks")).run(
        BuyAtVisibleIndex, bars=BARS, callbacks=cbs
    )
    assert calls["n"] == 1
    assert any(d.code == "CALLBACK_ERROR" for d in r.warnings)


def test_config_validation_margin_and_streaming_compare():
    with pytest.raises(ConfigError):
        BacktestEngine(cfg(margin_long=50, unsupported_margin_policy="error")).run(
            BuyAtVisibleIndex, bars=BARS
        )
    with pytest.raises(ConfigError):
        BacktestEngine(cfg(tradingview_compare_mode="streaming", execution_mode="normal")).run(
            BuyAtVisibleIndex, bars=BARS
        )


def test_bar_magnifier_uses_provider_lower_timeframe():
    p = Provider()
    r = BacktestEngine(
        cfg(data_provider=p, use_bar_magnifier=True, bar_magnifier_lower_tf="60")
    ).run(LimitEntry)
    assert p.called == 1
    assert p.lower_called >= 1
    assert r.open_trades[0].entry_price == 10


def test_compare_trades_reports_first_mismatch():
    report = compare_trades([{"entry_price": 1, "qty": 1}], [{"entry_price": "2", "qty": "1"}])
    assert not report.matched
    assert report.first_mismatch_index == 0
