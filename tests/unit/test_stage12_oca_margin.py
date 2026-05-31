from backtest_engine import BacktestConfig, BacktestEngine, Bar


def cfg(**kw):
    d = dict(symbol="S", timeframe="1D", start_time=1, end_time=5, commission_type="none")
    d.update(kw)
    return BacktestConfig(**d)


class TwoCompetingLimitEntries:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L_FAST", "long", qty=1, limit=9, oca_name="entry-choice", oca_type="cancel")
            self.ctx.entry("L_SLOW", "long", qty=1, limit=8, oca_name="entry-choice", oca_type="cancel")


class BracketAndGlobalExit:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L1", "long", qty=1)
            self.ctx.entry("L2", "long", qty=1)
        if bar_index == 2:
            self.ctx.exit("BRACKET", from_entry="L1", qty=1, limit=20, stop=5)
            self.ctx.exit("GLOBAL", qty=2, stop=9)


class BuyOnce:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)


def test_oca_cancel_entry_fills_one_and_cancels_sibling():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 10, 10, 8, 9),
        Bar(3, 10, 10, 7, 8),
    ]
    r = BacktestEngine(cfg(pyramiding=2)).run(TwoCompetingLimitEntries, bars=bars)

    assert [(t.entry_id, t.qty, t.entry_price) for t in r.open_trades] == [("L_FAST", 1.0, 9)]
    assert any(e.code == "ORDER_CANCELLED" and e.order_id == "L_SLOW" for e in (r.events or []))


def test_oca_reduce_reservation_prevents_global_exit_over_closing_reserved_entry():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 10, 10, 10, 10),
        Bar(3, 10, 10, 10, 10),
        Bar(4, 10, 10, 8, 8),
    ]
    r = BacktestEngine(cfg(pyramiding=2)).run(BracketAndGlobalExit, bars=bars)

    assert [(t.entry_id, t.exit_id, t.qty) for t in r.closed_trades] == [("L2", "GLOBAL:S", 1.0)]
    assert [(t.entry_id, t.qty) for t in r.open_trades] == [("L1", 1.0)]
    assert not any(
        d.code == "ORDER_REJECTED_NO_AVAILABLE_POSITION_QTY" and d.order_id == "BRACKET"
        for d in r.warnings
    )


def test_nonstandard_margin_runs_without_unsupported_warning_when_no_call():
    bars = [Bar(1, 10, 10, 10, 10), Bar(2, 10, 10, 10, 10)]
    r = BacktestEngine(cfg(margin_long=50, unsupported_margin_policy="error")).run(BuyOnce, bars=bars)
    assert r.status == "completed"
    assert not any(d.code == "UNSUPPORTED_MARGIN_LIQUIDATION_MODEL" for d in r.warnings)

    r = BacktestEngine(cfg(margin_short=50, unsupported_margin_policy="warn")).run(BuyOnce, bars=bars)
    assert r.status == "completed"
    assert not any(d.code == "UNSUPPORTED_MARGIN_LIQUIDATION_MODEL" for d in r.warnings)
