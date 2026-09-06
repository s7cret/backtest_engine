"""Entry-only risk, actual-fill capacity, market-only reversal suppression and state."""

from dataclasses import replace
import math
import pytest

from backtest_engine import BacktestConfig, BacktestEngine
from backtest_engine.context import StrategyContext
from tests.unit.test_deferred_market_exits import candles, run
from tests.unit.test_all_entry_exit_scope import mirror


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("kind", ["market", "limit", "stop", "stop_limit"])
@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
def test_oversized_entry_clips_actual_fills_with_attached_exit(direction, kind, on_close, recalc):
    def commands(ctx, i):
        if i == 0:
            prices = {
                "market": {},
                "limit": {"limit": 101},
                "stop": {"stop": 101},
                "stop_limit": {"stop": 101, "limit": 102},
            }[kind]
            if direction == "short":
                prices = {k: 200 - v for k, v in prices.items()}
            # Register after the order: rule order within the callback is irrelevant.
            ctx.entry("A", direction, qty=10, **prices)
            ctx.exit("X", "A", profit=10, loss=10)
            ctx.risk_max_position_size(3)

    engine, result = run(
        commands,
        mirror(candles((100, 101, 99, 100), (100, 103, 97, 100), (100, 115, 85, 100)), direction),
        process_orders_on_close=on_close,
        calc_on_order_fills=recalc,
    )
    assert result.status == "completed", result.errors
    assert len(result.closed_trades) == 1 and result.closed_trades[0].qty == 3
    assert engine.fills[0].qty == 3
    assert not result.open_trades


@pytest.mark.parametrize("direction", ["long", "short"])
def test_two_pending_orders_share_real_capacity_not_their_nominal_sizes(direction):
    def commands(ctx, i):
        if i == 0:
            ctx.risk_max_position_size(5)
            ctx.entry("first", direction, qty=3, limit=100)
            ctx.entry("second", direction, qty=4, limit=100)

    engine, result = run(commands, candles((100, 101, 99, 100), (100, 101, 99, 100)), pyramiding=2)
    assert result.status == "completed", result.errors
    assert [(f.order_id, f.qty) for f in engine.fills] == [("first", 3), ("second", 2)]
    assert sum(t.qty for t in result.open_trades) == 5


@pytest.mark.parametrize("direction", ["long", "short"])
def test_reversal_closing_component_is_not_charged_against_new_position_cap(direction):
    opposite = "short" if direction == "long" else "long"

    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=8)
        if i == 1:
            ctx.risk_max_position_size(3)
            ctx.entry("B", opposite, qty=10)

    engine, result = run(
        commands, candles((100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100))
    )
    assert result.status == "completed", result.errors
    assert engine.fills[-1].qty == 11
    assert result.closed_trades[0].qty == 8
    assert [(t.direction, t.qty) for t in result.open_trades] == [(opposite, 3)]


@pytest.mark.parametrize("kind", ["market", "limit", "stop", "stop_limit"])
@pytest.mark.parametrize("direction", ["long", "short"])
def test_disallowed_direction_closes_whole_position_at_market_ignoring_entry_price_conditions(
    kind, direction
):
    other = "short" if direction == "long" else "long"

    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=5)
        if i == 1:
            ctx.risk_allow_entry_in(direction)
            prices = {
                "market": {},
                "limit": {"limit": 500},
                "stop": {"stop": 1},
                "stop_limit": {"limit": 500, "stop": 1},
            }[kind]
            ctx.entry("B", other, qty=1, comment="risk-close", alert_message="closed", **prices)

    engine, result = run(
        commands, candles((100, 101, 99, 100), (100, 101, 99, 100), (102, 103, 101, 102))
    )
    assert result.status == "completed", result.errors
    assert not result.open_trades
    assert [(t.exit_price, t.qty, t.exit_comment) for t in result.closed_trades] == [
        (102, 5, "risk-close")
    ]
    assert engine.fills[-1].alert_message == "closed"


@pytest.mark.parametrize("direction", ["long", "short"])
def test_general_order_is_not_subject_to_entry_risk_commands(direction):
    def commands(ctx, i):
        if i == 0:
            ctx.risk_max_position_size(0)
            ctx.risk_allow_entry_in("short" if direction == "long" else "long")
            ctx.entry("blocked", direction, qty=1)
            ctx.order("free", direction, qty=9)

    engine, result = run(commands, candles((100, 101, 99, 100), (100, 101, 99, 100)))
    assert result.status == "completed", result.errors
    assert [(f.order_id, f.qty) for f in engine.fills] == [("free", 9)]


@pytest.mark.parametrize(
    "limit,step,minimum,expected",
    [
        (1.05, 0.1, None, 1.0),
        (0.25, 0.1, None, 0.2),
        (0.09, 0.1, None, 0),
        (0.15, 0.1, 0.2, 0),
        (0, None, None, 0),
    ],
)
def test_capacity_rounds_down_never_above_limit(limit, step, minimum, expected):
    def commands(ctx, i):
        if i == 0:
            ctx.risk_max_position_size(limit)
            ctx.entry("A", "long", qty=10)

    _, result = run(
        commands,
        candles((100, 101, 99, 100), (100, 101, 99, 100)),
        qty_step=step,
        min_qty=minimum,
        qty_rounding="nearest",
    )
    assert result.status == "completed", result.errors
    assert sum(t.qty for t in result.open_trades) == pytest.approx(expected)


@pytest.mark.parametrize("bad", [True, False, None, math.nan, math.inf, -math.inf, -1, "3"])
def test_invalid_limit_fails_without_mutating_command_buffers(bad):
    ctx = StrategyContext(BacktestConfig("S", "1m", 0, 1))
    with pytest.raises(ValueError):
        ctx.risk_max_position_size(bad)
    assert not ctx.risk_rules and not ctx.buffer.drain()


def test_stricter_of_multiple_limits_persists_and_config_is_not_mutated():
    def commands(ctx, i):
        if i == 0:
            ctx.risk_max_position_size(2)
            ctx.risk_max_position_size(5)
            ctx.entry("A", "long", qty=10)

    engine, result = run(commands, candles((100, 101, 99, 100), (100, 101, 99, 100)))
    assert result.status == "completed", result.errors
    assert result.open_trades[0].qty == 2 and engine.config.max_position_size is None


@pytest.mark.parametrize("direction", ["long", "short"])
def test_resume_restores_rules_registered_only_before_checkpoint(direction):
    from tests.unit.test_p1_deterministic_tick_replay import _legacy_config

    class Strategy:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx

        def run_bar(self, bar, bar_index):
            if bar_index == 0:
                self.ctx.risk_max_position_size(2)
                self.ctx.risk_allow_entry_in(direction)
            if bar_index == 2:
                self.ctx.entry("A", direction, qty=10)
            if bar_index == 3:
                self.ctx.entry("B", "short" if direction == "long" else "long", qty=1)

        def export_state(self):
            return {}

        def restore_state(self, state):
            assert state == {}

    bars = candles(*([(100, 101, 99, 100)] * 6))
    cfg = replace(_legacy_config(bars), force_close_on_end=False, mintick=1)
    whole = BacktestEngine(cfg)
    a = whole.run(Strategy, bars=bars)
    prefix = BacktestEngine(cfg).run(Strategy, bars=bars[:2])
    restored = BacktestEngine(cfg)
    b = restored.run(Strategy, bars=bars, resume_state=prefix.resume_state)
    assert a.status == b.status == "completed"
    assert a.closed_trades == b.closed_trades and a.equity_curve == b.equity_curve
    assert whole.fills == restored.fills
    assert a.closed_trades[0].qty == 2 and not a.open_trades


def test_realtime_broker_rollback_restores_limits_and_invalid_state_is_atomic():
    from backtest_engine.core.risk_rules import capture_risk_state

    e = BacktestEngine(BacktestConfig("S", "1m", 0, 1))
    ctx = StrategyContext(e.config)
    ctx.risk_max_position_size(2)
    ctx.risk_allow_entry_in("long")
    e._apply_risk_rules(ctx)
    snapshot = e._export_realtime_broker_state()
    old = capture_risk_state(e)
    e._max_position_size = 50
    e._allow_short = True
    e._restore_realtime_broker_state(snapshot)
    assert capture_risk_state(e) == old
    bad = replace(snapshot, risk_state={**snapshot.risk_state, "_allow_short": 1})
    with pytest.raises(ValueError):
        e._restore_realtime_broker_state(bad)
    assert capture_risk_state(e) == old


@pytest.mark.parametrize("direction", ["long", "short"])
def test_clipped_pending_order_cannot_regrow_after_old_position_closes(direction):
    def commands(ctx, i):
        if i == 0:
            ctx.risk_max_position_size(3)
            ctx.entry("A", direction, qty=2)
        if i == 1:
            ctx.entry("B", direction, qty=9, stop=110 if direction == "long" else 90)
            ctx.close("A", immediately=True)

    e, r = run(
        commands,
        mirror(candles((100, 101, 99, 100), (100, 101, 99, 100), (100, 115, 99, 112)), direction),
        pyramiding=2,
    )
    assert r.status == "completed", r.errors
    assert [(t.entry_id, t.qty) for t in r.open_trades] == [("B", 1)]


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("has_position", [True, False])
def test_native_rule_change_reconciles_pending_direction(direction, has_position):
    other = "short" if direction == "long" else "long"

    def commands(ctx, i):
        if i == 0:
            if has_position:
                ctx.entry("A", direction, qty=5)
            ctx.entry(
                "B",
                other,
                qty=2,
                stop=1 if direction == "long" else 200,
                comment="pending-risk",
                alert_message="closed",
            )
        if i == 1:
            ctx.risk_allow_entry_in(direction)

    e, r = run(commands, candles((100, 101, 99, 100), (100, 101, 99, 100), (102, 103, 101, 102)))
    assert r.status == "completed", r.errors
    assert not r.open_trades
    assert len(r.closed_trades) == int(has_position)
    if has_position:
        assert r.closed_trades[0].qty == 5 and r.closed_trades[0].exit_comment == "pending-risk"
        assert e.fills[-1].alert_message == "closed"
    else:
        assert not e.fills


@pytest.mark.parametrize("risk_state,version", [(None, 1), ({}, 1), ({}, True), ({}, 2)])
def test_missing_or_unknown_snapshot_policy_cannot_reset_active_limits(risk_state, version):
    from backtest_engine.core.risk_rules import capture_risk_state

    e = BacktestEngine(BacktestConfig("S", "1m", 0, 1))
    e._max_position_size = 2
    saved = e._export_realtime_broker_state()
    before = capture_risk_state(e)
    with pytest.raises(ValueError):
        e._restore_realtime_broker_state(
            replace(saved, risk_state=risk_state, risk_state_version=version)
        )
    assert capture_risk_state(e) == before


@pytest.mark.parametrize("direction", ["long", "short"])
def test_queued_full_close_does_not_add_a_second_closing_component_to_entry(direction):
    other = "short" if direction == "long" else "long"

    def commands(ctx, i):
        if i == 0:
            ctx.risk_max_position_size(5)
            ctx.entry("A", direction, qty=3)
        if i == 1:
            ctx.close_all()
            ctx.entry("B", other, qty=2)

    e, r = run(commands, candles(*([(100, 101, 99, 100)] * 3)))
    assert r.status == "completed", r.errors
    assert [(t.direction, t.qty) for t in r.open_trades] == [(other, 2)]
    assert e.fills[-1].qty == 2


@pytest.mark.parametrize("kind", ["limit", "stop", "stop_limit"])
def test_zero_cap_cancels_pending_entry_at_fill_without_phantom_trade(kind):
    def commands(ctx, i):
        if i == 0:
            args = {
                "limit": {"limit": 90},
                "stop": {"stop": 110},
                "stop_limit": {"stop": 110, "limit": 111},
            }[kind]
            ctx.entry("A", "long", qty=3, **args)
            ctx.exit("X", "A", profit=10)
        if i == 1:
            ctx.risk_max_position_size(0)

    e, r = run(commands, candles((100, 101, 99, 100), (100, 101, 99, 100), (100, 115, 85, 100)))
    assert r.status == "completed", r.errors
    assert not e.fills and not r.open_trades and not r.closed_trades
    assert e.orders[0].status == "cancelled" and not e.orders[0].pending_exits
