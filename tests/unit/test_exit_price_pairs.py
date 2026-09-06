"""Fixed exit price arbitration, per-fill policy persistence and replay versioning."""

from dataclasses import replace
import math

import pytest

from backtest_engine.core.exit_prices import resolve_exit_prices
from tests.unit.test_deferred_market_exits import candles, run
from tests.unit.test_all_entry_exit_scope import mirror


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("leg", ["profit", "loss"])
@pytest.mark.parametrize(
    "distance,absolute_distance", [(5, 10), (10, 5), (0, 10), (5, 5), (-5, 10)]
)
@pytest.mark.parametrize("policy", ["absolute_first", "first_trigger"])
def test_selector_uses_directional_trigger_not_absolute_distance(
    direction, leg, distance, absolute_distance, policy
):
    sign = (1 if direction == "long" else -1) * (1 if leg == "profit" else -1)
    absolute = 100 + sign * absolute_distance
    params = dict(limit=None, stop=None, profit=None, loss=None)
    params.update({leg: distance, "limit" if leg == "profit" else "stop": absolute})
    levels = resolve_exit_prices(
        direction=direction, entry_price=100, mintick=1, policy=policy, **params
    )
    chosen = absolute_distance if policy == "absolute_first" else min(distance, absolute_distance)
    assert levels == (
        (100 + sign * chosen, None) if leg == "profit" else (None, 100 + sign * chosen)
    )


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("leg", ["profit", "loss"])
@pytest.mark.parametrize("policy", ["absolute_first", "first_trigger"])
def test_one_real_exit_leg_at_chosen_price(direction, leg, policy):
    absolute = {"profit": 105, "loss": 95}[leg]
    if direction == "short":
        absolute = 200 - absolute

    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=2)
            ctx.exit(
                "X",
                "A",
                price_pair_policy=policy,
                **{leg: 2, "limit" if leg == "profit" else "stop": absolute},
            )

    e, result = run(commands, mirror(candles((100, 101, 99, 100), (100, 106, 94, 100)), direction))
    assert result.status == "completed", result.errors
    sign = (1 if direction == "long" else -1) * (1 if leg == "profit" else -1)
    assert [(t.exit_price, t.qty) for t in result.closed_trades] == [
        (100 + sign * (2 if policy == "first_trigger" else 5), 2)
    ]
    assert len([o for o in e.orders if o.kind == "exit"]) == 1
    assert len(e.fills) == 2


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
def test_dual_bracket_selects_levels_before_causal_scanning(direction, on_close, recalc):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=1)
            ctx.exit(
                "X",
                profit=3,
                limit=110 if direction == "long" else 90,
                loss=4,
                stop=90 if direction == "long" else 110,
                price_pair_policy="first_trigger",
            )

    e, r = run(
        commands,
        mirror(candles((100, 101, 99, 100), (100, 108, 98, 100)), direction),
        process_orders_on_close=on_close,
        calc_on_order_fills=recalc,
    )
    assert r.status == "completed", r.errors
    assert [(t.entry_price, t.exit_price) for t in r.closed_trades] == [
        (100, 103 if direction == "long" else 97)
    ]
    assert len([o for o in e.orders if o.kind == "exit"]) == 2
    assert not r.open_trades


@pytest.mark.parametrize("direction", ["long", "short"])
def test_gap_resolves_from_real_entry_and_selects_absolute_if_nearer(direction):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=1)
            ctx.exit(
                "X",
                profit=10,
                limit=115 if direction == "long" else 85,
                price_pair_policy="first_trigger",
            )

    _, r = run(commands, mirror(candles((100, 101, 99, 100), (110, 121, 109, 110)), direction))
    assert r.status == "completed", r.errors
    expected = [(110, 115)] if direction == "long" else [(90, 85)]
    assert [(t.entry_price, t.exit_price) for t in r.closed_trades] == expected


@pytest.mark.parametrize("scope", ["all", "named"])
@pytest.mark.parametrize("direction", ["long", "short"])
def test_mixed_targets_are_selected_per_actual_fill_and_do_not_double_reserve(scope, direction):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=2)
        if i == 1:
            ctx.entry("A", direction, qty=6)
        if i == 2:
            ctx.exit(
                "X",
                from_entry=None if scope == "all" else "A",
                profit=20,
                limit=125 if direction == "long" else 75,
                qty_percent=50,
                price_pair_policy="first_trigger",
            )

    e, r = run(
        commands,
        mirror(
            candles(
                (100, 101, 99, 100),
                (100, 101, 99, 100),
                (110, 111, 109, 110),
                (110, 121, 109, 110),
                (110, 126, 109, 110),
            ),
            direction,
        ),
        pyramiding=2,
    )
    assert r.status == "completed", r.errors
    expected = (
        [(100, 120, 1), (110, 125, 3)] if direction == "long" else [(100, 80, 1), (90, 75, 3)]
    )
    assert [(t.entry_price, t.exit_price, t.qty) for t in r.closed_trades] == expected
    assert [t.qty for t in r.open_trades] == [1, 3]
    assert sum(f.qty for f in e.fills if f.position_effect == "reduce") == 4


@pytest.mark.parametrize("policy", ["absolute_first", "first_trigger"])
def test_replacement_updates_levels_without_rearming_removed_stop(policy):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", "long", qty=1)
            ctx.exit("X", profit=50, limit=200, loss=20, stop=50, price_pair_policy="first_trigger")
        if i == 1:
            ctx.exit("X", profit=3, limit=105, price_pair_policy=policy)

    e, r = run(commands, candles((100, 101, 99, 100), (100, 101, 99, 100), (100, 106, 70, 100)))
    assert r.status == "completed", r.errors
    assert [t.exit_price for t in r.closed_trades] == [103 if policy == "first_trigger" else 105]
    assert all(
        o.status == "cancelled" for o in e.orders if o.kind == "exit" and o.order_type == "stop"
    )


@pytest.mark.parametrize("direction", ["long", "short"])
def test_resume_keeps_policy_for_future_entries(direction):
    from backtest_engine import BacktestEngine
    from tests.unit.test_p1_deterministic_tick_replay import _legacy_config

    class Strategy:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx
            self.sent = set()

        def run_bar(self, bar, bar_index):
            i = bar_index
            if i in self.sent:
                return
            self.sent.add(i)
            if i == 0:
                self.ctx.entry("A", direction, qty=2)
            if i == 1:
                self.ctx.exit(
                    "X:public",
                    profit=20,
                    limit=125 if direction == "long" else 75,
                    price_pair_policy="first_trigger",
                )
            if i == 3:
                self.ctx.entry("B", direction, qty=3)

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
            (110, 121, 109, 110),
            (110, 126, 109, 110),
        ),
        direction,
    )
    cfg = replace(_legacy_config(rows), pyramiding=2, mintick=1, force_close_on_end=False)
    e = BacktestEngine(cfg)
    whole = e.run(Strategy, bars=rows)
    prefix = BacktestEngine(cfg).run(Strategy, bars=rows[:3])
    assert (
        prefix.resume_state.broker_state.all_entry_exits["X:public"]["payload"]["price_pair_policy"]
        == "first_trigger"
    )
    restored = BacktestEngine(cfg)
    resumed = restored.run(Strategy, bars=rows, resume_state=prefix.resume_state)
    assert whole.status == resumed.status == "completed"
    assert len(resumed.closed_trades) == 2
    assert resumed.closed_trades == whole.closed_trades
    assert restored.fills == e.fills and resumed.equity_curve == whole.equity_curve


@pytest.mark.parametrize("bad", [True, math.inf, -math.inf])
def test_nonfinite_and_boolean_relative_values_are_not_prices(bad):
    with pytest.raises(ValueError):
        resolve_exit_prices(
            direction="long",
            entry_price=100,
            mintick=1,
            profit=bad,
            loss=None,
            limit=110,
            stop=None,
            policy="first_trigger",
        )


def test_native_na_and_absent_values_are_not_zero_distance():
    assert resolve_exit_prices(
        direction="long",
        entry_price=100,
        mintick=1,
        profit=math.nan,
        loss=None,
        limit=110,
        stop=None,
        policy="first_trigger",
    ) == (110, None)
    from backtest_engine.context.command_buffer import ExitPayload

    with pytest.raises(ValueError, match="policy"):
        ExitPayload("X", profit=1, price_pair_policy="unknown")
