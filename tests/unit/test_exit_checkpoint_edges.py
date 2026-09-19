"""Legacy checkpoint and stale-exit paths must preserve actual exposure."""
from dataclasses import asdict, replace

import pytest

from backtest_engine import BacktestConfig, BacktestEngine
from backtest_engine.context import StrategyContext
from backtest_engine.context.command_buffer import ExitPayload
from backtest_engine.core.deferred_exits import activate_deferred_exits
from backtest_engine.core.exit_prices import resolve_exit_prices, resolve_trailing_prices
from backtest_engine.core.exit_scope import activate_persistent_exits, expire_all_entry_exits
from backtest_engine.core.fill_execution import execute_fill
from backtest_engine.core.fill_scanner import _scan_orders_at_path_point, update_trailing_order
from backtest_engine.core.intent_replay import IntentTapeValidationError, validate_intent_tape
from backtest_engine.core.price_events import next_price_event
from backtest_engine.core.strategy_command_processor import _add_or_modify_exit_order, _apply_exit_command
from backtest_engine.models import Bar, Position, Trade
from tests.unit.test_broker_guard_edges import engine, order
from tests.unit.test_deferred_market_exits import candles, run


def trade(**updates):
    value = Trade("T", "A", None, "long", 0, 0, 100, None, None, None, 1, 0, 0, 0, 0, is_open=True)
    return replace(value, **updates)


@pytest.mark.parametrize("updates", [{"direction": "flat"}, {"policy": "unknown"},
                                      {"mintick": 0}, {"entry_price": None}, {"mintick": None},
                                      {"profit": 1e308, "mintick": 1e308}])
def test_exit_price_boundary_rejects_invalid_policy_inputs_and_real_overflow(updates):
    args = dict(direction="long", entry_price=100, mintick=1, limit=None, stop=None, profit=None, loss=None)
    args.update(updates)
    with pytest.raises(ValueError):
        resolve_exit_prices(**args)


@pytest.mark.parametrize("offset", [None, -1])
def test_trailing_boundary_rejects_missing_or_negative_offset(offset):
    with pytest.raises(ValueError):
        resolve_trailing_prices(direction="long", entry_price=100, mintick=1,
                                trail_price=110, trail_points=None, trail_offset=offset)


def test_deferred_exit_never_attaches_to_an_unrelated_open_trade():
    broker = engine()
    broker.position = Position(size=1, avg_price=100, direction="long")
    broker.open_trades = [trade(entry_id="B")]
    pending = order(pending_exits={"X": {"payload": asdict(ExitPayload("X", "A", limit=110))}})
    activate_deferred_exits(broker, pending, Bar(0, 100, 100, 100, 100), 0)
    assert pending.pending_exits == {} and broker.orders == []


def test_expiry_removes_only_all_entry_templates():
    broker = engine()
    pending = order(pending_exits={
        "global": {"payload": asdict(ExitPayload("global", limit=110))},
        "named": {"payload": asdict(ExitPayload("named", "A", limit=110))},
    })
    broker.orders = [pending]
    expire_all_entry_exits(broker)
    assert set(pending.pending_exits) == {"named"}


def test_persistent_exit_from_previous_direction_cannot_attach():
    broker = engine()
    broker.position = Position(size=1, avg_price=100, direction="long")
    broker._all_entry_exits = {"X": {"direction": "short", "payload": asdict(ExitPayload("X", limit=90))}}
    activate_persistent_exits(broker, order(), Bar(0, 100, 100, 100, 100), 0)
    assert broker.orders == []


@pytest.mark.parametrize("kind,from_entry", [("close", "A"), ("exit", "A"), ("exit", None)])
def test_stale_reducing_order_never_charges_commission_or_fills(kind, from_entry):
    broker = engine()
    pending = order(kind=kind, from_entry=from_entry, position_effect="close", reduce_only=True,
                    position_direction="long", side="sell")
    cash = broker.cash
    execute_fill(broker, pending, Bar(0, 100, 100, 100, 100), 0, 100, "open")
    assert broker.cash == cash and broker.fills == []
    if kind == "close":
        assert pending.status == "cancelled"


@pytest.mark.parametrize("direction,stop,price", [("long", 95, 98), ("short", 105, 102)])
def test_legacy_trailing_stop_restores_best_price_without_regressing(direction, stop, price):
    pending = order(kind="exit", direction=direction, trail_activated=True,
                    trail_offset=5, stop_price=stop, trail_best_price=None)
    update_trailing_order(pending, price)
    assert pending.trail_best_price == 100
    assert pending.stop_price == stop


@pytest.mark.parametrize("direction,activation,old_stop,new_stop", [("long", 120, 95, 97), ("short", 80, 105, 103)])
def test_amending_legacy_trail_retains_best_price(direction, activation, old_stop, new_stop):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=1)
            ctx.exit("X", "A", trail_price=activation, trail_offset=5)

    broker, result = run(commands, candles((100, 101, 99, 100), (100, 101, 99, 100)))
    assert result.status == "completed", result.errors
    existing = next(o for o in broker.orders if o.kind == "exit")
    existing.trail_activated = True
    existing.trail_best_price = None
    existing.stop_price = old_stop
    amended = replace(existing, trail_offset=3)
    _add_or_modify_exit_order(broker, amended, Bar(120000, 100, 100, 100, 100), 2)
    assert existing.trail_best_price == 100
    assert existing.stop_price == new_stop


def test_stale_named_exit_cannot_change_next_price_event():
    broker = engine()
    broker.orders = [order(kind="exit", status="active", from_entry="missing",
                          order_type="limit", limit_price=105)]
    assert next_price_event(broker, 100, 110, 1) == 110


def test_explicit_stale_trade_cannot_create_exit_on_flat_position():
    broker = engine()
    _apply_exit_command(broker, ExitPayload("X", limit=110), Bar(0, 100, 100, 100, 100),
                        0, False, 110, None, register_pending=False, target_trade=trade())
    assert broker.orders == []


def test_schema_validated_fast_path_still_requires_mapping_rows():
    with pytest.raises(IntentTapeValidationError, match="not a mapping"):
        validate_intent_tape([None], schema_validate=False)


def test_real_margin_liquidation_restarts_scan_after_callback():
    broker = BacktestEngine(BacktestConfig("S", "1m", 0, 120000,
        initial_capital=1000, margin_short=100, calc_on_order_fills=True, commission_type="none"))
    broker.position = Position(size=-80, avg_price=10, direction="short")
    broker.cash = broker.equity = 1000
    broker.open_trades = [trade(entry_id="Short", direction="short", entry_price=10, qty=80)]
    callbacks = []

    class Strategy:
        def run_bar(self, bar, index):
            callbacks.append(index)

    restart, recalc, filled = _scan_orders_at_path_point(
        broker, Strategy(), StrategyContext(broker.config, broker.state),
        Bar(60000, 10, 12, 10, 12), 1, 12, "high", False, 1, 0, False,
        False, False, False, None)
    assert (restart, recalc, filled) == (True, 1, True)
    assert callbacks == [1]
    assert broker.closed_trades[0].exit_id == "Margin call"
    assert broker.closed_trades[0].qty == pytest.approx(40)
