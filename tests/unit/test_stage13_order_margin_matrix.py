import pytest

from backtest_engine import BacktestConfig, BacktestEngine, Bar
from backtest_engine.errors import ConfigError


def cfg(**kw):
    d = dict(
        symbol="S13",
        timeframe="1D",
        start_time=1,
        end_time=10,
        commission_type="none",
    )
    d.update(kw)
    return BacktestConfig(**d)


class TwoLotsThenExitMatrix:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L1", "long", qty=1)
            self.ctx.entry("L2", "long", qty=1)
        if bar_index == 1:
            self.ctx.exit("L1_TOO_BIG", from_entry="L1", qty=5, stop=9)
            self.ctx.exit("GLOBAL", qty=2, stop=8)


class CancelReleasesReservation:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 1:
            self.ctx.exit("RESERVE", from_entry="L", qty=1, stop=5)
        if bar_index == 2:
            self.ctx.cancel("RESERVE:S")
            self.ctx.exit("AFTER_CANCEL", qty=1, stop=9)


class LimitStopAndTrailingExits:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx
        self.kind = params["kind"]

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 1:
            if self.kind == "limit":
                self.ctx.exit("LX", from_entry="L", qty=1, limit=12)
            elif self.kind == "stop":
                self.ctx.exit("LX", from_entry="L", qty=1, stop=9)
            elif self.kind == "trailing":
                self.ctx.exit("LX", from_entry="L", qty=1, trail_price=12, trail_offset=1)


class StopLimitEntry:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("SL", "long", qty=1, stop=12, limit=10.5)


class BuyOnce:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)


def test_margin_non_100_error_and_warn_paths_are_explicit():
    bars = [Bar(1, 10, 10, 10, 10), Bar(2, 10, 10, 10, 10)]

    with pytest.raises(ConfigError, match="margin/liquidation model is unsupported"):
        BacktestEngine(cfg(margin_long=50, unsupported_margin_policy="error")).run(
            BuyOnce, bars=bars
        )

    result = BacktestEngine(cfg(margin_short=25, unsupported_margin_policy="warn")).run(
        BuyOnce, bars=bars
    )
    warning = next(d for d in result.warnings if d.code == "UNSUPPORTED_MARGIN_LIQUIDATION_MODEL")
    assert "margin_long=100.0" in warning.message
    assert "margin_short=25" in warning.message


def test_exit_reservations_clip_quantities_and_prevent_over_close():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 10, 10, 10, 10),
        Bar(3, 10, 10, 7, 7),
    ]
    result = BacktestEngine(cfg(pyramiding=2, process_orders_on_close=True)).run(
        TwoLotsThenExitMatrix, bars=bars
    )

    assert sorted((t.entry_id, t.exit_id, t.qty) for t in result.closed_trades) == [
        ("L1", "L1_TOO_BIG:S", 1.0),
        ("L2", "GLOBAL:S", 1.0),
    ]
    assert result.open_trades == []
    assert sum(t.qty for t in result.closed_trades) == 2.0


def test_cancel_releases_reserved_qty_for_later_exit():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 10, 10, 10, 10),
        Bar(3, 10, 10, 10, 10),
        Bar(4, 10, 10, 8, 8),
    ]
    result = BacktestEngine(cfg(process_orders_on_close=True)).run(
        CancelReleasesReservation, bars=bars
    )

    assert [(t.entry_id, t.exit_id, t.qty) for t in result.closed_trades] == [
        ("L", "AFTER_CANCEL:S", 1.0)
    ]
    assert any(e.code == "ORDER_CANCELLED" and e.order_id == "RESERVE:S" for e in result.events)


@pytest.mark.parametrize(
    ("kind", "bar", "exit_id", "exit_price"),
    [
        ("limit", Bar(3, 10, 13, 10, 13), "LX:L", 12.0),
        ("stop", Bar(3, 10, 10, 8, 8), "LX:S", 9.0),
        ("trailing", Bar(3, 10, 13, 10, 12), "LX:T", 12.5),
    ],
)
def test_exit_order_matrix_limit_stop_and_trailing(kind, bar, exit_id, exit_price):
    bars = [Bar(1, 10, 10, 10, 10), Bar(2, 10, 10, 10, 10), bar]
    result = BacktestEngine(cfg(process_orders_on_close=True, mintick=0.5)).run(
        LimitStopAndTrailingExits, params={"kind": kind}, bars=bars
    )

    assert [(t.exit_id, t.exit_price, t.qty) for t in result.closed_trades] == [
        (exit_id, exit_price, 1.0)
    ]


def test_stop_limit_entry_activates_then_fills_limit_without_gap_overclaim():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 11.5, 12, 10, 10.5),
    ]
    result = BacktestEngine(cfg()).run(StopLimitEntry, bars=bars)

    assert [(t.entry_id, t.entry_price, t.qty) for t in result.open_trades] == [("SL", 10.5, 1.0)]
    assert any(e.code == "STOP_LIMIT_ACTIVATED" and e.order_id == "SL" for e in result.events)
