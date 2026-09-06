"""Causal trailing activation, entry-lot identity and stable amendments."""

from dataclasses import replace
import math

import pytest

from backtest_engine import BacktestEngine
from backtest_engine.context.command_buffer import ExitPayload
from backtest_engine.core.exit_prices import resolve_trailing_prices
from tests.unit.test_all_entry_exit_scope import mirror
from tests.unit.test_deferred_market_exits import candles, run


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("policy", ["absolute_first", "first_trigger"])
@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
def test_activation_pair_is_version_policy_per_actual_entry(direction, policy, on_close, recalc):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=2)
            ctx.exit(
                "X",
                "A",
                trail_price=110 if direction == "long" else 90,
                trail_points=5,
                trail_offset=2,
                price_pair_policy=policy,
            )

    _, r = run(
        commands,
        mirror(candles((100, 101, 99, 100), (100, 108, 99, 104), (104, 113, 103, 107)), direction),
        process_orders_on_close=on_close,
        calc_on_order_fills=recalc,
    )
    assert r.status == "completed", r.errors
    expected = 106 if policy == "first_trigger" else 111
    assert [(t.exit_price, t.qty) for t in r.closed_trades] == [
        (expected if direction == "long" else 200 - expected, 2)
    ]


@pytest.mark.parametrize("direction", ["long", "short"])
def test_trailing_boundary_precedes_farther_plain_stop_even_when_created_later(direction):
    # Put the plain opposite stop first in the order list. Both cross on the same
    # adverse segment; the already activated trail at 108 must execute first.
    def ordered(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=1)
        if i == 1:
            ctx.order(
                "opposite",
                "short" if direction == "long" else "long",
                qty=1,
                stop=104 if direction == "long" else 96,
            )
            ctx.exit("X", "A", trail_price=105 if direction == "long" else 95, trail_offset=2)

    e, r = run(
        ordered,
        mirror(candles((100, 101, 99, 100), (110, 111, 109, 110), (110, 111, 100, 101)), direction),
    )
    assert r.status == "completed", r.errors
    assert r.closed_trades[0].exit_parent_id == "X"
    assert r.closed_trades[0].exit_price == (109 if direction == "long" else 91)
    assert e.fills[1].order_id == "X:T"


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("recalc", [False, True])
def test_entry_after_prior_extreme_cannot_activate_or_stop_in_the_past(direction, recalc):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=1, stop=105 if direction == "long" else 95)
            ctx.exit("X", "A", trail_points=2, trail_offset=2)

    _, r = run(
        commands,
        mirror(candles((100, 101, 99, 100), (100, 110, 98, 109), (109, 110, 106, 109)), direction),
        calc_on_order_fills=recalc,
    )
    assert r.status == "completed", r.errors
    assert [(t.entry_price, t.exit_price, t.exit_bar_index) for t in r.closed_trades] == [
        (105, 108, 2) if direction == "long" else (95, 92, 2)
    ]


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("scope", ["named", "all"])
def test_repeated_entry_id_has_independent_activation_and_partial_reserves(direction, scope):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=2)
        if i == 1:
            ctx.entry("A", direction, qty=6)
        if i == 2:
            ctx.exit(
                "X",
                "A" if scope == "named" else None,
                trail_points=15,
                trail_offset=2,
                qty_percent=50,
            )

    e, r = run(
        commands,
        mirror(
            candles(
                (100, 101, 99, 100),
                (100, 101, 99, 100),
                (110, 111, 109, 110),
                (110, 120, 109, 117),
                (117, 130, 116, 125),
            ),
            direction,
        ),
        pyramiding=2,
    )
    assert r.status == "completed", r.errors
    expected = [(100, 118, 1), (110, 128, 3)]
    if direction == "short":
        expected = [(200 - a, 200 - b, q) for a, b, q in expected]
    assert [(t.entry_price, t.exit_price, t.qty) for t in r.closed_trades] == expected
    assert [t.qty for t in r.open_trades] == [1, 3]
    assert len([o for o in e.orders if o.kind == "exit"]) == 2


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("offset", [2, 1])
def test_reissue_keeps_best_observed_price_and_updates_in_place(direction, offset):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=1)
            ctx.exit("X", "A", trail_points=5, trail_offset=2)
        if i == 1:
            ctx.exit("X", "A", trail_points=5, trail_offset=offset)

    e, r = run(
        commands,
        mirror(candles((100, 101, 99, 100), (100, 110, 99, 109), (109, 109, 105, 107)), direction),
    )
    assert r.status == "completed", r.errors
    assert len([o for o in e.orders if o.kind == "exit"]) == 1
    expected = 110 - offset
    assert [t.exit_price for t in r.closed_trades] == [
        expected if direction == "long" else 200 - expected
    ]
    assert e.orders[-1].trail_best_price == (110 if direction == "long" else 90)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_gap_after_activation_fills_at_observed_open_not_interpolated_stop(direction):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=1)
            ctx.exit("X", trail_points=5, trail_offset=2)

    _, r = run(
        commands,
        mirror(candles((100, 101, 99, 100), (100, 110, 99, 109), (105, 108, 104, 106)), direction),
    )
    assert r.status == "completed", r.errors
    assert [t.exit_price for t in r.closed_trades] == [105 if direction == "long" else 95]


@pytest.mark.parametrize("direction", ["long", "short"])
def test_zero_offset_is_not_missing_and_fills_at_activation_boundary(direction):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=1)
            ctx.exit("X", "A", trail_points=5, trail_offset=0)

    _, r = run(commands, mirror(candles((100, 101, 99, 100), (100, 110, 99, 108)), direction))
    assert r.status == "completed", r.errors
    assert [t.exit_price for t in r.closed_trades] == [105 if direction == "long" else 95]


@pytest.mark.parametrize(
    "fields",
    [
        {"trail_price": 105},
        {"trail_points": 2},
        {"trail_offset": 2},
        {"trail_price": 105, "trail_offset": -1},
        {"trail_price": 105, "trail_offset": True},
        {"trail_price": math.inf, "trail_offset": 1},
        {"trail_price": 105, "trail_offset": math.inf},
        {"trail_price": math.nan, "trail_offset": 1},
    ],
)
def test_invalid_trailing_is_rejected_before_buffer_mutation(fields):
    with pytest.raises(ValueError):
        ExitPayload("X", **fields)


def test_runtime_range_and_missing_values_are_explicit():
    assert (
        resolve_trailing_prices(
            direction="long",
            entry_price=100,
            mintick=1,
            trail_price=math.nan,
            trail_points=None,
            trail_offset=None,
        )
        is None
    )
    with pytest.raises(ValueError):
        resolve_trailing_prices(
            direction="long",
            entry_price=100,
            mintick=1e-300,
            trail_price=105,
            trail_points=None,
            trail_offset=1e-300,
        )


@pytest.mark.parametrize("direction", ["long", "short"])
def test_native_resume_preserves_active_best_price_and_future_all_entry_policy(direction):
    from tests.unit.test_p1_deterministic_tick_replay import _legacy_config

    class Strategy:
        def __init__(self, params, runtime, ctx):
            self.ctx, self.sent = ctx, set()

        def run_bar(self, bar, bar_index):
            if bar_index in self.sent:
                return
            self.sent.add(bar_index)
            if bar_index == 0:
                self.ctx.entry("A", direction, qty=2)
                self.ctx.exit("X:public", trail_points=5, trail_offset=2)
            if bar_index == 2:
                self.ctx.entry("B", direction, qty=3)

        def export_state(self):
            return {"sent": sorted(self.sent)}

        def restore_state(self, state):
            self.sent = set(state["sent"])

    rows = mirror(
        candles(
            (100, 101, 99, 100), (100, 110, 99, 109), (109, 111, 109, 110), (112, 118, 111, 114)
        ),
        direction,
    )
    cfg = replace(_legacy_config(rows), pyramiding=2, mintick=1, force_close_on_end=False)
    e = BacktestEngine(cfg)
    whole = e.run(Strategy, bars=rows)
    prefix = BacktestEngine(cfg).run(Strategy, bars=rows[:2])
    active = [o for o in prefix.resume_state.broker_state.orders if o.kind == "exit"][0]
    assert active.trail_activated and active.trail_best_price == (
        110 if direction == "long" else 90
    )
    re = BacktestEngine(cfg)
    resumed = re.run(Strategy, bars=rows, resume_state=prefix.resume_state)
    assert whole.status == resumed.status == "completed"
    assert whole.closed_trades == resumed.closed_trades
    assert whole.equity_curve == resumed.equity_curve and e.fills == re.fills


@pytest.mark.parametrize("action", ["cancel", "cancel_all", "replace"])
@pytest.mark.parametrize("direction", ["long", "short"])
def test_cancel_or_replace_active_trail_never_resurrects_it(action, direction):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=1)
            ctx.exit("X:public", "A", trail_points=5, trail_offset=2)
        if i == 1:
            if action == "cancel":
                ctx.cancel("X:public")
            elif action == "cancel_all":
                ctx.cancel_all()
            else:
                ctx.exit("X:public", "A", limit=120 if direction == "long" else 80)

    e, r = run(
        commands,
        mirror(candles((100, 101, 99, 100), (100, 110, 99, 109), (109, 110, 90, 100)), direction),
    )
    assert r.status == "completed", r.errors
    assert not r.closed_trades and len(r.open_trades) == 1
    assert all(o.status == "cancelled" for o in e.orders if o.trail_offset is not None)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_tp_and_trail_share_reserve_and_first_trigger_wins(direction):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=3)
            ctx.exit(
                "X",
                "A",
                limit=108 if direction == "long" else 92,
                trail_points=5,
                trail_offset=2,
                qty=2,
            )

    e, r = run(commands, mirror(candles((100, 101, 99, 100), (100, 112, 99, 100)), direction))
    assert r.status == "completed", r.errors
    assert [(t.exit_id, t.qty) for t in r.closed_trades] == [("X:L", 2)]
    assert [(t.qty) for t in r.open_trades] == [1]
    assert len([o for o in e.orders if o.kind == "exit"]) == 2
    assert all(o.status in {"filled", "cancelled"} for o in e.orders if o.kind == "exit")


@pytest.mark.parametrize("direction", ["long", "short"])
def test_persistent_trail_observes_future_entry_fill_before_adverse_segment(direction):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=1)
            ctx.exit("X", trail_points=-5, trail_offset=2)
        if i == 1:
            ctx.entry("B", direction, qty=1)

    _, r = run(
        commands,
        mirror(candles((100, 101, 99, 100), (100, 101, 100, 100), (110, 130, 100, 101)), direction),
        pyramiding=2,
    )
    assert r.status == "completed", r.errors
    # Open is closer to low: both trails must first observe the actual 110 fill.
    expected = 108 if direction == "long" else 92
    assert [t.exit_price for t in r.closed_trades] == [expected, expected]
