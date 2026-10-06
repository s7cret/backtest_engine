"""Production JSON corruption admission, not a test-only typed resume decoder.

JSON-v1 is a primitive serializer. Typed new-engine restore is a remaining
production integration obligation; this pack does not claim to close it.
"""

import json
from dataclasses import replace

import pytest

from backtest_engine import BacktestEngine
from backtest_engine.core.state_snapshot import BrokerSnapshot, JsonStateSerializer
from backtest_engine.errors import ResumeUnsupportedError
from backtest_engine.models import BacktestResumeState
from tests.unit.test_deferred_market_exits import candles
from tests.unit.test_p1_deterministic_tick_replay import _legacy_config


class MutationStrategy:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def run_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.order("R1", "long", qty=1, limit=99,
                           oca_name="reduce", oca_type="reduce")
            self.ctx.order("R2", "long", qty=3, limit=90,
                           oca_name="reduce", oca_type="reduce")
            self.ctx.order("C1", "long", qty=1, limit=98,
                           oca_name="cancel", oca_type="cancel")
            self.ctx.order("C2", "long", qty=2, limit=89,
                           oca_name="cancel", oca_type="cancel")

    def export_state(self):
        return {}

    def restore_state(self, state):
        assert state == {}


def mutation_checkpoint():
    rows = candles((100, 100, 100, 100), (100, 101, 97, 100))
    cfg = replace(_legacy_config(rows), initial_capital=100000,
                  force_close_on_end=False, mintick=1)
    engine = BacktestEngine(cfg)
    result = engine.run(MutationStrategy, bars=rows)
    assert result.status == "completed", result.errors
    assert result.resume_state is not None
    assert isinstance(result.resume_state.broker_state, BrokerSnapshot)
    return result.resume_state


def test_primitive_json_cannot_restore_new_engine_without_production_typed_decoder():
    checkpoint = mutation_checkpoint()
    raw = JsonStateSerializer().loads(JsonStateSerializer().dumps(checkpoint))
    assert isinstance(raw, dict)
    # Only wrap the top-level model; deliberately do not reconstruct nested
    # types in a bespoke decoder. This pins the real production API gap.
    state = BacktestResumeState(**raw)
    rows = candles((100, 100, 100, 100), (100, 101, 97, 100))
    engine = BacktestEngine(replace(_legacy_config(rows), initial_capital=100000,
                                   force_close_on_end=False, mintick=1))
    assert isinstance(state.broker_state, dict)
    assert isinstance(raw["statistics_state"]["equity_curve"][0], dict)
    with pytest.raises(ResumeUnsupportedError,
                       match="statistics_state.equity_curve has invalid bar indices"):
        engine.run(MutationStrategy, bars=rows, resume_state=state)
    assert not engine.fills
    assert not engine.orders


def test_json_finite_primitives_keep_canonical_bytes_and_types():
    serializer = JsonStateSerializer()
    state = {"integer": 1, "float": 1.0, "negative_zero": -0.0, "flag": True,
             "tiny": 1e-300, "nested": [{"none": None}]}
    expected = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    assert serializer.dumps(state) == expected
    assert serializer.dumps(serializer.loads(expected)) == expected


def test_production_json_preserves_post_oca_mutation_event_oracle():
    raw = JsonStateSerializer().loads(JsonStateSerializer().dumps(mutation_checkpoint()))
    broker = raw["broker_state"]
    assert [(o["id"], o["qty"], o["status"]) for o in broker["orders"]] == [
        ("R1", 1, "filled"), ("R2", 2, "active"),
        ("C1", 1, "filled"), ("C2", 2, "cancelled"),
    ]
    assert [(f["order_id"], f["price"], f["qty"], f["commission"])
            for f in broker["fills"]] == [("R1", 99, 1, 0), ("C1", 98, 1, 0)]
    assert [(t["entry_id"], t["entry_price"], t["qty"])
            for t in broker["open_trades"]] == [("R1", 99, 1), ("C1", 98, 1)]
    assert broker["position"]["size"] == 2
    assert broker["equity"] == 100003
    assert [(e["code"], e["order_id"], e["bar_index"])
            for e in raw["statistics_state"]["events"]] == [
        ("ORDER_CREATED", "R1", 0), ("ORDER_CREATED", "R2", 0),
        ("ORDER_CREATED", "C1", 0), ("ORDER_CREATED", "C2", 0),
        ("ORDER_ACTIVATED", "R1", 1), ("ORDER_ACTIVATED", "R2", 1),
        ("ORDER_ACTIVATED", "C1", 1), ("ORDER_ACTIVATED", "C2", 1),
        ("ORDER_FILLED", "R1", 1), ("ORDER_MODIFIED", "R2", 1),
        ("ORDER_FILLED", "C1", 1), ("ORDER_CANCELLED", "C2", 1),
    ]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_json_export_rejects_nonfinite_mutated_broker_equity(value):
    checkpoint = mutation_checkpoint()
    checkpoint = replace(checkpoint, broker_state=replace(checkpoint.broker_state, equity=value))
    with pytest.raises(ValueError, match="finite|compliant"):
        JsonStateSerializer().dumps(checkpoint)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_json_load_rejects_nonfinite_nested_broker_equity(token):
    with pytest.raises(ValueError, match="finite|constant"):
        JsonStateSerializer().loads(
            ('{"broker_state":{"equity":' + token + '}}').encode()
        )


def test_json_load_rejects_duplicate_nested_order_quantity_after_mutation():
    payload = JsonStateSerializer().dumps(mutation_checkpoint())
    raw = json.loads(payload)
    assert raw["broker_state"]["orders"][1]["qty"] == 2
    # Ambiguous duplicate quantities must not silently choose the last value.
    order = json.dumps(raw["broker_state"]["orders"][1], sort_keys=True, separators=(",", ":"))
    corrupt_order = order.replace('"qty":2', '"qty":3,"qty":2')
    assert corrupt_order != order
    corrupt = payload.replace(order.encode(), corrupt_order.encode())
    assert corrupt != payload
    with pytest.raises(ValueError, match="duplicate"):
        JsonStateSerializer().loads(corrupt)
