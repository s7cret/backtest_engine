"""Absolute named exits use the same opening-fill quantity rules as all exits."""

from dataclasses import replace
import pytest
from backtest_engine import BacktestEngine
from tests.unit.test_deferred_market_exits import run, candles
from tests.unit.test_all_entry_exit_scope import mirror


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("leg", ["profit", "loss", "bracket"])
@pytest.mark.parametrize("scope", ["named", "all"])
@pytest.mark.parametrize("quantity", ["percent", "units", "both"])
def test_absolute_exits_size_each_matching_execution(direction, leg, scope, quantity):
    tp, sl = (120, 95) if direction == "long" else (80, 105)
    prices = (
        {"limit": tp}
        if leg == "profit"
        else {"stop": sl}
        if leg == "loss"
        else {"limit": tp, "stop": sl}
    )
    sizing = (
        {"qty_percent": 50}
        if quantity == "percent"
        else {"qty": 1}
        if quantity == "units"
        else {"qty": 1, "qty_percent": 100}
    )

    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=2)
        if i == 1:
            ctx.entry("A", direction, qty=6)
        if i == 2:
            ctx.exit(
                "X",
                "A" if scope == "named" else None,
                **prices,
                **sizing,
                comment_profit="TP",
                comment_loss="SL",
            )

    event = (110, 121, 109, 115) if leg != "loss" else (110, 111, 94, 100)
    e, r = run(
        commands,
        mirror(
            candles((100, 101, 99, 100), (100, 101, 99, 100), (110, 111, 109, 110), event),
            direction,
        ),
        pyramiding=2,
    )
    assert r.status == "completed", r.errors
    quantities = [1, 3] if quantity == "percent" else [1, 1]
    assert [(t.entry_price, t.qty) for t in r.closed_trades] == list(
        zip([100, 110] if direction == "long" else [100, 90], quantities)
    )
    assert [t.qty for t in r.open_trades] == [2 - quantities[0], 6 - quantities[1]]
    expected_leg = "loss" if leg == "loss" else "profit"
    assert all(t.exit_leg == expected_leg for t in r.closed_trades)
    assert all(t.exit_comment == ("SL" if leg == "loss" else "TP") for t in r.closed_trades)
    exits = [o for o in e.orders if o.kind == "exit"]
    assert len(exits) == (4 if leg == "bracket" else 2)
    assert len({o.entry_fill_index for o in exits}) == 2


@pytest.mark.parametrize("direction", ["long", "short"])
def test_absolute_amendment_releases_each_lot_reserve(direction):
    def tp(price):
        return price if direction == "long" else 200 - price

    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=2)
        if i == 1:
            ctx.entry("A", direction, qty=6)
        if i == 2:
            ctx.exit("X", "A", limit=tp(125), qty_percent=100)
        if i == 3:
            ctx.exit("X", "A", limit=tp(125), qty_percent=50)
            ctx.exit("Y", "A", limit=tp(120), qty_percent=100)

    _, r = run(
        commands,
        mirror(
            candles(
                (100, 101, 99, 100),
                (100, 101, 99, 100),
                (110, 111, 109, 110),
                (110, 111, 109, 110),
                (110, 121, 109, 115),
                (115, 126, 114, 120),
            ),
            direction,
        ),
        pyramiding=2,
    )
    assert r.status == "completed", r.errors
    assert [(t.exit_parent_id, t.qty) for t in r.closed_trades] == [
        ("Y", 1),
        ("Y", 3),
        ("X", 1),
        ("X", 3),
    ]
    assert not r.open_trades


@pytest.mark.parametrize("direction", ["long", "short"])
def test_absolute_relative_replacement_keeps_lot_identity(direction):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=2)
        if i == 1:
            ctx.entry("A", direction, qty=6)
        if i == 2:
            ctx.exit("X", "A", profit=30, qty_percent=50, comment_profit="old")
        if i == 3:
            ctx.exit(
                "X",
                "A",
                limit=120 if direction == "long" else 80,
                qty_percent=50,
                comment_profit="new",
            )

    e, r = run(
        commands,
        mirror(
            candles(
                (100, 101, 99, 100),
                (100, 101, 99, 100),
                (110, 111, 109, 110),
                (110, 111, 109, 110),
                (110, 121, 109, 115),
            ),
            direction,
        ),
        pyramiding=2,
    )
    assert r.status == "completed", r.errors
    assert [t.qty for t in r.closed_trades] == [1, 3]
    assert [t.exit_comment for t in r.closed_trades] == ["new", "new"]
    assert len([o for o in e.orders if o.kind == "exit"]) == 2


def test_same_bar_executions_and_partial_reissue_never_reuse_completed_lot():
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", "long", qty=2)
            ctx.order("A", "long", qty=6)
        if i == 1:
            ctx.exit("X", "A", limit=120, qty_percent=50)
        if i == 2:
            ctx.exit("X", "A", limit=120, qty_percent=50)

    e, r = run(
        commands,
        candles(
            (100, 101, 99, 100), (100, 101, 99, 100), (100, 121, 99, 115), (115, 121, 114, 115)
        ),
        pyramiding=2,
    )
    assert r.status == "completed", r.errors
    assert [t.qty for t in r.closed_trades] == [1, 3]
    assert len([o for o in e.orders if o.kind == "exit"]) == 2
    assert [t.qty for t in r.open_trades] == [1, 3]


@pytest.mark.parametrize("direction", ["long", "short"])
def test_native_resume_preserves_absolute_named_lots_and_comments(direction):
    from tests.unit.test_p1_deterministic_tick_replay import _legacy_config

    class Strategy:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx
            self.sent = set()

        def run_bar(self, bar, bar_index):
            if bar_index in self.sent:
                return
            self.sent.add(bar_index)
            if bar_index == 0:
                self.ctx.entry("A", direction, qty=2, comment="first")
            if bar_index == 1:
                self.ctx.entry("A", direction, qty=6, comment="second")
            if bar_index == 2:
                self.ctx.exit(
                    "X",
                    "A",
                    limit=120 if direction == "long" else 80,
                    qty_percent=50,
                    comment_profit="TP",
                    alert_profit="snapshot",
                )

        def export_state(self):
            return {"sent": sorted(self.sent)}

        def restore_state(self, state):
            self.sent = set(state["sent"])

    rows = mirror(
        candles(
            (100, 101, 99, 100), (100, 101, 99, 100), (110, 111, 109, 110), (110, 121, 109, 115)
        ),
        direction,
    )
    cfg = replace(_legacy_config(rows), pyramiding=2, mintick=1, force_close_on_end=False)
    whole = BacktestEngine(cfg)
    expected = whole.run(Strategy, bars=rows)
    prefix = BacktestEngine(cfg).run(Strategy, bars=rows[:3])
    resumed = BacktestEngine(cfg)
    actual = resumed.run(Strategy, bars=rows, resume_state=prefix.resume_state)
    assert actual.status == expected.status == "completed"
    assert (
        actual.closed_trades == expected.closed_trades
        and actual.equity_curve == expected.equity_curve
    )
    assert resumed.fills == whole.fills
    assert [(t.qty, t.exit_alert_message) for t in actual.closed_trades] == [
        (1, "snapshot"),
        (3, "snapshot"),
    ]
