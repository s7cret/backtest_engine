"""Independent mixed-OCA event oracle: name alone is not group identity.

Contract basis: Pine strategy OCA groups are identified by BOTH oca_name and
oca_type (TradingView Pine concepts/strategies, "OCA groups"). `none` does not
participate. This is a manually derived oracle, not a TradingView export.
https://www.tradingview.com/pine-script-docs/concepts/strategies/#oca-groups

Five price orders share a label but implement three independent policies.
Crossing their levels in order must cancel only C2, reduce only R2 by one,
and leave N unchanged. Expected events and executions are literal contract
values; they are never calculated from broker outputs.
"""

from dataclasses import replace

import pytest

from backtest_engine import BacktestEngine
from backtest_engine.core.state_snapshot import BrokerSnapshot, JsonStateSerializer
from backtest_engine.models import BacktestResumeState, Bar, Diagnostic, EquityPoint, Fill, Order, Position, Trade
from tests.unit.test_deferred_market_exits import candles, run
from tests.unit.test_p1_deterministic_tick_replay import _legacy_config, SerializableBarRuntime


def json_resume(state):
    """Rehydrate declared dataclass types from real JSON, never retain live objects.

    JsonStateSerializer deliberately returns primitive containers; the consumer
    must reconstruct typed snapshots before the public resume API admits them.
    """
    serializer = JsonStateSerializer()
    raw = serializer.loads(serializer.dumps(state))
    broker = raw["broker_state"]
    broker["position"] = Position(**broker["position"])
    for key, model in [("orders", Order), ("fills", Fill),
                       ("open_trades", Trade), ("closed_trades", Trade)]:
        broker[key] = [model(**item) for item in broker[key]]
    raw["broker_state"] = BrokerSnapshot(**broker)
    stats = raw["statistics_state"]
    for key, model in [("events", Diagnostic), ("warnings", Diagnostic), ("errors", Diagnostic),
                       ("equity_curve", EquityPoint), ("score_equity_points", EquityPoint)]:
        stats[key] = [model(**item) for item in stats[key]]
    if raw["runtime_state"]["current_bar"] is not None:
        raw["runtime_state"]["current_bar"] = Bar(**raw["runtime_state"]["current_bar"])
    return BacktestResumeState(**raw)


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("first", ["cancel", "reduce"])
@pytest.mark.parametrize("resume", [False, True])
def test_same_label_different_oca_types_have_independent_event_sequences(direction, first, resume):
    levels = {"C1": 99, "C2": 98, "R1": 97, "R2": 96, "N": 95}
    if first == "reduce":
        levels = {"R1": 99, "R2": 98, "C1": 97, "C2": 96, "N": 95}
    if direction == "short":
        levels = {name: 200 - value for name, value in levels.items()}

    def commands(ctx, i):
        if i == 0:
            for name, qty, policy in [
                ("C1", 1, "cancel"), ("C2", 2, "cancel"),
                ("R1", 1, "reduce"), ("R2", 3, "reduce"), ("N", 4, "none"),
            ]:
                ctx.order(name, direction, qty=qty, limit=levels[name],
                          oca_name="shared-label", oca_type=policy)

    rows = candles((100, 100, 100, 100),
                   (100, 101, 94, 100) if direction == "long" else (100, 106, 99, 100))
    if not resume:
        engine, result = run(commands, rows)
    else:
        class Strategy:
            def __init__(self, params, runtime, ctx):
                self.ctx = ctx

            def run_bar(self, bar, bar_index):
                commands(self.ctx, bar_index)

            def export_state(self):
                return {}

            def restore_state(self, state):
                assert state == {}

        cfg = replace(_legacy_config(rows), force_close_on_end=False, mintick=1)
        whole = BacktestEngine(cfg)
        full = whole.run(Strategy, bars=rows)
        prefix_engine = BacktestEngine(replace(cfg, runtime=SerializableBarRuntime()))
        prefix_result = prefix_engine.run(Strategy, bars=rows[:1])
        assert prefix_result.status == "completed", prefix_result.errors
        checkpoint = json_resume(prefix_result.resume_state)
        assert checkpoint == prefix_result.resume_state
        assert checkpoint.broker_state.orders[0] is not prefix_engine.orders[0]
        engine = BacktestEngine(replace(cfg, runtime=SerializableBarRuntime()))
        result = engine.run(Strategy, bars=rows, resume_state=checkpoint)
        assert result.events == full.events
        assert engine.fills == whole.fills
        assert result.open_trades == full.open_trades
        assert result.closed_trades == full.closed_trades
        assert result.equity_curve == full.equity_curve
        assert engine.position == whole.position
        assert engine.orders == whole.orders
    assert result.status == "completed", result.errors
    prefix = ([("ORDER_CREATED", name, 0) for name in ("C1", "C2", "R1", "R2", "N")]
              + [("ORDER_ACTIVATED", name, 1) for name in ("C1", "C2", "R1", "R2", "N")])
    cancel_events = [("ORDER_FILLED", "C1", 1), ("ORDER_CANCELLED", "C2", 1)]
    reduce_events = [("ORDER_FILLED", "R1", 1), ("ORDER_MODIFIED", "R2", 1),
                     ("ORDER_FILLED", "R2", 1)]
    expected_events = prefix + (cancel_events + reduce_events if first == "cancel"
                                else reduce_events + cancel_events) + [("ORDER_FILLED", "N", 1)]
    assert [(e.code, e.order_id, e.bar_index) for e in result.events] == expected_events
    expected_fills = {
        ("long", "cancel"): [("C1", 99, 1), ("R1", 97, 1), ("R2", 96, 2), ("N", 95, 4)],
        ("long", "reduce"): [("R1", 99, 1), ("R2", 98, 2), ("C1", 97, 1), ("N", 95, 4)],
        ("short", "cancel"): [("C1", 101, 1), ("R1", 103, 1), ("R2", 104, 2), ("N", 105, 4)],
        ("short", "reduce"): [("R1", 101, 1), ("R2", 102, 2), ("C1", 103, 1), ("N", 105, 4)],
    }[(direction, first)]
    assert [(f.order_id, f.price, f.qty) for f in engine.fills] == expected_fills
    assert all(f.commission == 0 and f.bar_index == 1 and f.direction == direction for f in engine.fills)
    assert [(o.id, o.qty, o.status) for o in engine.orders] == [
        ("C1", 1, "filled"), ("C2", 2, "cancelled"),
        ("R1", 1, "filled"), ("R2", 2, "filled"), ("N", 4, "filled"),
    ]
    assert [(t.entry_id, t.entry_price, t.qty) for t in result.open_trades] == expected_fills
    assert engine.position.size == (8 if direction == "long" else -8)
