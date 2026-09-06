"""Position-scoped exit lifetimes and per-opening-fill relative brackets."""

from dataclasses import replace

import pytest

from tests.unit.test_deferred_market_exits import candles, run


def mirror(rows, direction):
    if direction == "long":
        return rows
    return [
        replace(b, open=200 - b.open, high=200 - b.low, low=200 - b.high, close=200 - b.close)
        for b in rows
    ]


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("relative", [False, True])
@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
def test_all_current_entries_close_independently(direction, relative, on_close, recalc):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=2)
            ctx.entry("B", direction, qty=3)
            ctx.exit(
                "X",
                **(
                    {"profit": 5, "loss": 20}
                    if relative
                    else {
                        "limit": 105 if direction == "long" else 95,
                        "stop": 80 if direction == "long" else 120,
                    }
                ),
            )

    rows = mirror(candles((100, 101, 99, 100), (100, 106, 99, 100), (100, 101, 99, 100)), direction)
    e, result = run(
        commands, rows, pyramiding=2, process_orders_on_close=on_close, calc_on_order_fills=recalc
    )
    assert result.status == "completed", result.errors
    assert [(t.entry_id, t.qty, t.exit_price) for t in result.closed_trades] == [
        ("A", 2, 105 if direction == "long" else 95),
        ("B", 3, 105 if direction == "long" else 95),
    ]
    assert not result.open_trades and not e._all_entry_exits
    assert len({t.entry_fill_index for t in result.closed_trades}) == 2


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("same_id", [False, True])
def test_one_unqualified_exit_protects_subsequent_entries_until_flat(direction, same_id):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=2)
        if i == 1:
            ctx.exit("X", profit=50, loss=70)
        if i == 2:
            ctx.entry("A" if same_id else "B", direction, qty=3)
        if i == 5:
            ctx.entry("new-position", direction, qty=1)

    rows = mirror(
        candles(
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (110, 111, 109, 110),
            (110, 151, 109, 110),
            (110, 161, 109, 110),
            (110, 111, 109, 110),
            (110, 180, 109, 110),
        ),
        direction,
    )
    e, result = run(commands, rows, pyramiding=3)
    assert result.status == "completed", result.errors
    assert [(t.entry_price, t.exit_price, t.qty) for t in result.closed_trades] == [
        (100, 150 if direction == "long" else 50, 2),
        (110 if direction == "long" else 90, 160 if direction == "long" else 40, 3),
    ]
    assert [t.entry_id for t in result.open_trades] == ["new-position"]
    assert not e._all_entry_exits


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("cancel", ["X", "all"])
def test_cancel_removes_future_subscription_and_all_existing_children(direction, cancel):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=1)
        if i == 1:
            ctx.exit("X", profit=20, loss=50)
        if i == 2:
            ctx.cancel_all() if cancel == "all" else ctx.cancel("X")
            ctx.entry("B", direction, qty=2)

    e, result = run(
        commands,
        mirror(
            candles(
                (100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100), (100, 150, 99, 100)
            ),
            direction,
        ),
        pyramiding=2,
    )
    assert result.status == "completed", result.errors
    assert not result.closed_trades and len(result.open_trades) == 2
    assert not e._all_entry_exits
    assert all(o.status == "cancelled" for o in e.orders if o.kind == "exit")


@pytest.mark.parametrize("scope", ["all", "explicit"])
@pytest.mark.parametrize("direction", ["long", "short"])
def test_relative_repeated_ids_use_each_execution_price_and_quantity(scope, direction):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=2)
        if i == 1:
            ctx.entry("A", direction, qty=6)
        if i == 2:
            ctx.exit(
                "X", **({} if scope == "all" else {"from_entry": "A"}), profit=20, qty_percent=50
            )

    e, result = run(
        commands,
        mirror(
            candles(
                (100, 101, 99, 100),
                (100, 101, 99, 100),
                (110, 111, 109, 110),
                (110, 125, 109, 110),
                (110, 135, 109, 110),
            ),
            direction,
        ),
        pyramiding=2,
    )
    assert result.status == "completed", result.errors
    assert [(t.entry_price, t.exit_price, t.qty) for t in result.closed_trades] == [
        (100, 120 if direction == "long" else 80, 1),
        (110 if direction == "long" else 90, 130 if direction == "long" else 70, 3),
    ]
    assert [t.qty for t in result.open_trades] == [1, 3]
    assert sum(f.qty for f in e.fills if f.position_effect == "reduce") == 4


def test_explicit_exit_does_not_subscribe_to_future_entries_with_same_id():
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", "long", qty=2)
        if i == 1:
            ctx.exit("X", "A", profit=20)
        if i == 2:
            ctx.entry("A", "long", qty=3)

    _, result = run(
        commands,
        candles(
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (110, 111, 109, 110),
            (110, 125, 109, 110),
            (110, 150, 109, 110),
        ),
        pyramiding=2,
    )
    assert result.status == "completed", result.errors
    assert len(result.closed_trades) == 1 and result.closed_trades[0].qty == 2
    assert len(result.open_trades) == 1 and result.open_trades[0].qty == 3


def test_no_position_and_no_pending_entry_does_not_arm_future_positions():
    def commands(ctx, i):
        if i == 0:
            ctx.exit("X", profit=2)
        if i == 1:
            ctx.entry("A", "long", qty=1)

    e, result = run(
        commands, candles((100, 101, 99, 100), (100, 101, 99, 100), (100, 150, 99, 100))
    )
    assert result.status == "completed", result.errors
    assert not result.closed_trades and len(result.open_trades) == 1 and not e._all_entry_exits


def test_partial_consumed_exit_does_not_rearm_old_lots_but_new_lot_is_protected():
    def commands(ctx, i):
        if i in (0, 2):
            ctx.entry("A", "long", qty=2)
        if i in (1, 3):
            ctx.exit("X", profit=5, qty_percent=50)

    _, result = run(
        commands,
        candles(
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (100, 106, 99, 100),
            (110, 111, 109, 110),
            (110, 116, 109, 110),
        ),
        pyramiding=2,
    )
    assert result.status == "completed", result.errors
    assert [(t.entry_price, t.qty, t.exit_price) for t in result.closed_trades] == [
        (100, 1, 105),
        (110, 1, 115),
    ]
    assert [t.qty for t in result.open_trades] == [1, 1]


def test_scope_snapshot_roundtrip_keeps_future_entry_policy_and_is_detached():
    from backtest_engine import BacktestConfig, BacktestEngine
    from backtest_engine.core.state_snapshot import clone_state
    from backtest_engine.context import StrategyContext

    rows = candles((100, 101, 99, 100), (100, 101, 99, 100), (110, 111, 109, 110))
    e = BacktestEngine(BacktestConfig("S", "1m", 0, 120000, mintick=1))
    ctx = StrategyContext(e.config, e.state)
    ctx.entry("A", "long", qty=1)
    e._flush(ctx, rows[0], 0)
    e.orders[0].status = "active"
    e._fill(e.orders[0], rows[1], 1, 100, "open")
    ctx.exit("X", profit=20)
    e._flush(ctx, rows[1], 1)
    snap = clone_state(e._export_realtime_broker_state())
    e._all_entry_exits["X"]["payload"]["profit"] = 999
    restored = BacktestEngine(e.config)
    restored._restore_realtime_broker_state(snap)
    assert restored._all_entry_exits["X"]["payload"]["profit"] == 20
    ctx = StrategyContext(restored.config, restored.state)
    ctx.order("B", "long", qty=1)
    restored._flush(ctx, rows[1], 1)
    entry = restored.orders[-1]
    entry.status = "active"
    restored._fill(entry, rows[2], 2, 110, "open")
    assert sorted(o.limit_price for o in restored.orders if o.kind == "exit") == [120, 130]
    assert snap.all_entry_exits["X"]["payload"]["profit"] == 20


def test_reversal_expires_old_scope_instead_of_protecting_opposite_position():
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", "long", qty=1)
        if i == 1:
            ctx.exit("X", profit=30, loss=50)
            ctx.entry("B", "short", qty=2)

    e, result = run(
        commands, candles((100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 20, 100))
    )
    assert result.status == "completed", result.errors
    assert not e._all_entry_exits and len(result.open_trades) == 1
    assert result.open_trades[0].direction == "short" and result.open_trades[0].qty == 2


@pytest.mark.parametrize(
    "convert", ["all_to_named", "named_to_all", "relative_to_absolute", "absolute_to_relative"]
)
def test_replacement_does_not_leave_obsolete_targets_or_price_legs(convert):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", "long", qty=2)
            ctx.entry("B", "long", qty=3)
        if i == 1:
            ctx.exit(
                "X",
                **(
                    {"from_entry": "A"}
                    if convert in ("named_to_all", "absolute_to_relative")
                    else {}
                ),
                **(
                    {"profit": 20, "loss": 20}
                    if convert == "relative_to_absolute"
                    else {"limit": 120, "stop": 80}
                ),
            )
        if i == 2:
            ctx.exit(
                "X",
                **(
                    {"from_entry": "A"}
                    if convert in ("all_to_named", "relative_to_absolute")
                    else {}
                ),
                **({"profit": 30} if convert == "absolute_to_relative" else {"limit": 130}),
            )

    e, result = run(
        commands,
        candles(
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (100, 121, 79, 100),
            (100, 131, 99, 100),
        ),
        pyramiding=2,
    )
    assert result.status == "completed", result.errors
    expected = 2 if convert in ("all_to_named", "relative_to_absolute") else 5
    assert sum(t.qty for t in result.closed_trades) == expected
    assert all(t.exit_price == 130 for t in result.closed_trades)
    assert all(o.status not in ("pending", "active") for o in e.orders if o.kind == "exit")


def test_opposite_order_reduces_a_reserved_position_without_phantom_fill():
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", "long", qty=4)
        if i == 1:
            ctx.exit("X", profit=30)
            ctx.order("reduce", "short", qty=1)

    e, result = run(
        commands,
        candles((100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100), (100, 131, 99, 100)),
    )
    assert result.status == "completed", result.errors
    assert [(t.qty, t.exit_price) for t in result.closed_trades] == [(1, 100), (3, 130)]
    assert not result.open_trades and not e._all_entry_exits


def test_relative_pending_repeated_entry_activates_only_after_its_real_fill():
    def commands(ctx, i):
        if i in (0, 1):
            ctx.entry("A", "long", qty=i + 1)
            ctx.exit("X", "A", profit=20)

    _, result = run(
        commands,
        candles(
            (100, 101, 99, 100), (100, 101, 99, 100), (110, 125, 109, 110), (110, 135, 109, 110)
        ),
        pyramiding=2,
    )
    assert result.status == "completed", result.errors
    assert [(t.entry_price, t.exit_price, t.qty) for t in result.closed_trades] == [
        (100, 120, 1),
        (110, 130, 2),
    ]
    assert not result.open_trades


def test_empty_native_from_entry_has_the_same_scope_as_omitted():
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", "long", qty=1)
            ctx.exit("X", "", profit=5)

    _, result = run(commands, candles((100, 101, 99, 100), (100, 106, 99, 100)))
    assert result.status == "completed" and [
        (t.qty, t.exit_price) for t in result.closed_trades
    ] == [(1, 105)]


def test_same_bar_same_id_lots_keep_distinct_filled_exit_keys_after_restore():
    from backtest_engine import BacktestConfig, BacktestEngine
    from backtest_engine.context import StrategyContext
    from backtest_engine.core.strategy_command_processor import _apply_exit_command
    from backtest_engine.context.command_buffer import ExitPayload

    rows = candles((100, 101, 99, 100), (100, 150, 99, 100))
    e = BacktestEngine(BacktestConfig("S", "1m", 0, 60000, mintick=1))
    ctx = StrategyContext(e.config, e.state)
    for price in (100, 110):
        ctx.order("A", "long", qty=2)
        e._flush(ctx, rows[0], 0)
        entry = e.orders[-1]
        entry.status = "active"
        e._fill(entry, rows[1], 1, price, "open")
    ctx.exit("X", profit=20, qty_percent=50)
    e._flush(ctx, rows[1], 1)
    exits = [o for o in e.orders if o.kind == "exit"]
    assert [(o.entry_fill_index, o.limit_price) for o in exits] == [(0, 120), (1, 130)]
    exits[0].status = "active"
    e._fill(exits[0], rows[1], 1, 120, "crossing")
    snap = e._export_realtime_broker_state()
    restored = BacktestEngine(e.config)
    restored._restore_realtime_broker_state(snap)
    _apply_exit_command(
        restored, ExitPayload("X", profit=20, qty_percent=50), rows[1], 1, True, None, None
    )
    active = [o for o in restored.orders if o.kind == "exit" and o.status in ("active", "pending")]
    assert len(active) == 1 and active[0].entry_fill_index == 1
    active[0].status = "active"
    restored._fill(active[0], rows[1], 1, 130, "crossing")
    assert [(t.entry_price, t.qty) for t in restored.closed_trades] == [(100, 1), (110, 1)]
    assert [t.qty for t in restored.open_trades] == [1, 1]


@pytest.mark.parametrize("direction", ["long", "short"])
def test_native_broker_resume_preserves_policy_and_per_fill_identity(direction):
    from backtest_engine import BacktestEngine
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
                self.ctx.entry("A", direction, qty=2)
            if bar_index == 1:
                self.ctx.exit("X:public", profit=20)
            if bar_index == 3:
                self.ctx.entry("A", direction, qty=3)

        def export_state(self):
            return {"sent": sorted(self.sent)}

        def restore_state(self, state):
            self.sent = set(state["sent"])

    rows = mirror(
        candles(
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (110, 111, 109, 110),
            (110, 111, 109, 110),
            (110, 115, 109, 110),
            (110, 125, 109, 110),
            (110, 135, 109, 110),
        ),
        direction,
    )

    def cfg():
        return replace(_legacy_config(rows), pyramiding=2, mintick=1, force_close_on_end=False)

    whole_engine = BacktestEngine(cfg())
    whole = whole_engine.run(Strategy, bars=rows)
    prefix = BacktestEngine(cfg()).run(Strategy, bars=rows[:3])
    assert prefix.resume_state is not None
    assert "X:public" in prefix.resume_state.broker_state.all_entry_exits
    resumed_engine = BacktestEngine(cfg())
    resumed = resumed_engine.run(Strategy, bars=rows, resume_state=prefix.resume_state)
    assert whole.status == resumed.status == "completed"
    assert len(whole.closed_trades) == 2
    assert resumed.closed_trades == whole.closed_trades
    assert resumed_engine.fills == whole_engine.fills
    assert resumed.equity_curve == whole.equity_curve
    assert not resumed_engine._all_entry_exits
    assert all(t.exit_parent_id == "X:public" for t in resumed.closed_trades)


def test_warmup_reset_and_capture_do_not_share_position_lifetime_policies():
    from backtest_engine.core.warmup import BrokerState

    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", "long", qty=1)
        if i == 1:
            ctx.exit("X", profit=20)

    engine, result = run(commands, candles((100, 101, 99, 100), (100, 101, 99, 100)))
    assert result.status == "completed" and engine._all_entry_exits
    captured = BrokerState.capture(engine)
    engine._all_entry_exits["X"]["payload"]["profit"] = 99
    assert captured.all_entry_exits["X"]["payload"]["profit"] == 20
    captured.apply_to(engine)
    assert engine._all_entry_exits["X"]["payload"]["profit"] == 20
    engine._all_entry_exits.clear()
    assert captured.all_entry_exits
    captured.apply_to(engine)
    BrokerState.canonical_initial(engine.config.initial_capital).apply_to(engine)
    assert not engine._all_entry_exits and not engine.orders and not engine.open_trades
