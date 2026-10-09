"""Reject detached corruption before consulting a live resume owner."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from backtest_engine import BacktestEngine, JsonResumeStateSerializer
from backtest_engine.core import resume_accounting as accounting
from backtest_engine.core import resume_json as wire
from backtest_engine.core.resume_realtime import admit_realtime_resume
from backtest_engine.errors import ResumeUnsupportedError
from tests.unit.test_typed_json_resume import (
    BracketStrategy,
    FixedOcaStrategy,
    PartialBracket,
    checkpoint,
    inputs,
    assert_rejected_without_live_mutation,
)
from tests.unit.test_typed_json_realtime_resume import CheckedStrategy, export_cut, scenario


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("strategy_state", {"type": "tuple", "items": {}}, "tuple fields"),
        ("strategy_state", {"type": "Bar", "fields": []}, "model fields"),
        ("strategy_state", {}, "untyped object"),
        ("order_book_state", {"type": "mapping", "items": {}}, "foreign order_book"),
    ],
)
def test_malformed_registered_projection(field, value, message):
    payload = json.loads(JsonResumeStateSerializer().dumps(checkpoint()))
    payload["state"]["fields"][field] = value
    with pytest.raises(ResumeUnsupportedError, match=message):
        JsonResumeStateSerializer().loads(json.dumps(payload).encode())


@pytest.mark.parametrize(
    "name,value,message",
    [
        ("resume_contract", "foreign", "resume contract"),
        ("bar_prefix_fingerprint", True, "invalid bar_prefix"),
        ("realtime_tick_schedule_fingerprint", "bad", "invalid realtime"),
    ],
)
def test_metadata_identity_corruption(name, value, message, monkeypatch):
    payload = json.loads(JsonResumeStateSerializer().dumps(checkpoint()))
    payload["state"]["fields"]["metadata"]["items"][name] = value
    assert_rejected_without_live_mutation(json.dumps(payload).encode(), monkeypatch)


@pytest.mark.parametrize(
    "name,value",
    [
        ("equity_curve", [None]),
        ("events", [None]),
        ("closed_trade_stats_count", 1),
    ],
)
def test_wrong_statistics_owner_values(name, value):
    state = checkpoint()
    state.statistics_state[name] = value
    with pytest.raises(ResumeUnsupportedError):
        JsonResumeStateSerializer().dumps(state)


def test_statistics_point_outside_committed_cursor():
    state = checkpoint()
    state.statistics_state["score_equity_points"] = [
        replace(state.statistics_state["equity_curve"][0], bar_index=state.bar_index + 1)
    ]
    with pytest.raises(ResumeUnsupportedError, match="point index outside"):
        JsonResumeStateSerializer().dumps(state)


def test_broker_owner_must_be_typed_even_for_in_memory_export():
    with pytest.raises(ResumeUnsupportedError, match="typed BrokerSnapshot"):
        JsonResumeStateSerializer().dumps(replace(checkpoint(), broker_state={}))


def test_missing_opening_trade_fill_is_rejected():
    state = checkpoint()
    state.broker_state.open_trades[0].entry_fill_index = len(state.broker_state.fills)
    with pytest.raises(ResumeUnsupportedError, match="entry fill link"):
        JsonResumeStateSerializer().dumps(state)


@pytest.mark.parametrize("value", [None, {}, True])
def test_root_must_be_a_registered_resume_state(value):
    codec = JsonResumeStateSerializer()
    with pytest.raises(ResumeUnsupportedError, match="root"):
        codec.dumps(value)
    envelope = json.loads(codec.dumps(checkpoint()))
    envelope["state"] = value if value != {} else {"type": "mapping", "items": {}}
    with pytest.raises(ResumeUnsupportedError, match="root"):
        codec.loads(json.dumps(envelope).encode())


@pytest.mark.parametrize("value,message", [("x" * 129, "byte budget"), ("\ud800", "UTF-8")])
def test_strings_reject_byte_overflow_and_invalid_unicode(value, message):
    with pytest.raises(ResumeUnsupportedError, match=message):
        wire._Budget(128, 64, 200000).touch(0, value)


def test_float_field_rejects_integer_larger_than_float_domain():
    with pytest.raises(ResumeUnsupportedError, match="finite number"):
        wire._check_type(1 << 4095, float, "price")


def test_serialized_envelope_overhead_is_included_in_byte_limit():
    state = checkpoint()
    payload = JsonResumeStateSerializer().dumps(state)
    with pytest.raises(ResumeUnsupportedError, match="byte budget"):
        JsonResumeStateSerializer(max_bytes=len(payload) - 1).dumps(state)


def test_export_converts_inverse_instrument_arithmetic_failure_to_resume_error():
    from backtest_engine import InstrumentModel

    config, bars = inputs(instrument_model=InstrumentModel(mode="inverse_futures"))
    state = BacktestEngine(config).run(FixedOcaStrategy, bars=bars[:2]).resume_state
    state.metadata["native_accounting"]["mark_price"] = 0
    with pytest.raises(ResumeUnsupportedError, match="invalid state"):
        JsonResumeStateSerializer().dumps(state)


def closed_state():
    config, bars = inputs([(100, 101, 99, 100), (100, 106, 99, 100)])
    return BacktestEngine(config).run(BracketStrategy, bars=bars).resume_state


@pytest.mark.parametrize(
    "fault,message",
    [
        ("entry_missing", "opening fill"),
        ("negative_commission", "negative allocated"),
        ("closing_missing", "closing fill identity"),
        ("chain", "direction chain"),
        ("signed_direction", "signed quantity"),
        ("opening_capacity", "exceed their fill"),
        ("opening_side", "side/direction"),
        ("no_link", "durable opening/closing"),
        ("unknown_close", "unknown closing fill"),
        ("position_direction", "final fill"),
        ("lot_direction", "open trade direction"),
    ],
)
def test_ledger_refuses_corrupted_durable_links(fault, message):
    broker = deepcopy(
        closed_state().broker_state
        if fault in {"closing_missing", "unknown_close"}
        else checkpoint().broker_state
    )
    trade = (broker.closed_trades or broker.open_trades)[0]
    if fault == "entry_missing":
        trade.entry_qty = None
    elif fault == "negative_commission":
        trade.commission_entry = -1
    elif fault == "closing_missing":
        trade.exit_time = None
    elif fault == "chain":
        broker.fills[0].position_direction_before = "long"
    elif fault == "signed_direction":
        broker.fills[0].position_direction_after = "short"
    elif fault == "opening_capacity":
        trade.qty = trade.entry_qty = broker.fills[0].qty + 1
    elif fault == "opening_side":
        broker.fills[0].direction = "short"
    elif fault == "no_link":
        broker.open_trades.clear()
    elif fault == "unknown_close":
        trade.qty /= 2
        broker.closed_trades.append(deepcopy(trade))
        trade.exit_id = "unknown"
    elif fault == "position_direction":
        broker.position.direction = "short"
    else:
        trade.direction = "short"
    with pytest.raises(ResumeUnsupportedError, match=message):
        accounting.validate_broker_ledger(broker, qty_epsilon=1e-12)


def test_split_lot_must_retain_same_original_entry_quantity():
    config, bars = inputs([(100, 100, 100, 100), (100, 100, 100, 100), (100, 106, 100, 100)])
    state = BacktestEngine(config).run(PartialBracket, bars=bars).resume_state
    state.broker_state.closed_trades[0].entry_qty += 1
    with pytest.raises(ResumeUnsupportedError, match="opening lot entry_qty"):
        accounting.validate_broker_ledger(state.broker_state, qty_epsilon=1e-12)


@pytest.mark.parametrize("flat", [True, False])
def test_equity_history_average_price_agrees_with_position_presence(flat):
    state = checkpoint()
    point = replace(
        state.statistics_state["equity_curve"][0],
        position_size=0 if flat else 1,
        position_avg_price=100 if flat else None,
    )
    with pytest.raises(ResumeUnsupportedError, match="equity history"):
        accounting.validate_broker_values(
            state.broker_state, inputs()[0], mark_price=None, mark_tick=None, equity_points=[point]
        )


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("initial_capital", 1 << 4095, "numeric field"),
        ("commission_value", -1, "native domain"),
        ("mintick", None, None),
        ("mark_price", None, None),
        ("instrument_model", {}, "instrument fields"),
    ],
)
def test_accounting_context_numeric_and_optional_domains(field, value, message):
    context = deepcopy(checkpoint().metadata["native_accounting"])
    context[field] = value
    if message:
        with pytest.raises(ResumeUnsupportedError, match=message):
            accounting.read_accounting_inputs(context)
    else:
        assert getattr(accounting.read_accounting_inputs(context), field) is None


@pytest.mark.parametrize("size", [1 << 4095, True, float("nan")])
def test_instrument_contract_size_rejects_unrepresentable_numbers(size):
    context = deepcopy(checkpoint().metadata["native_accounting"])
    context["instrument_model"]["contract_size"] = size
    with pytest.raises(ResumeUnsupportedError, match="instrument scalar"):
        accounting.read_accounting_inputs(context)


@pytest.mark.parametrize(
    "fault,message",
    [
        ("boundary", "parent-bar boundary"),
        ("cursor", "committed input bar"),
        ("ticks", "explicit list/tuple"),
        ("missing_strategy", "missing strategy_state"),
        ("marker", "differs from config"),
        ("validator_return", "must return None"),
    ],
)
def test_realtime_preflight_refuses_incomplete_trusted_owner(fault, message):
    state = export_cut()
    config, bars = scenario()
    strategy = CheckedStrategy
    if fault == "boundary":
        state.metadata["realtime_resume_boundary"] = "foreign"
    elif fault == "cursor":
        state = replace(state, bar_index=len(bars))
    elif fault == "ticks":
        config.realtime_ticks = iter(config.realtime_ticks)
    elif fault == "missing_strategy":
        state = replace(state, strategy_state=None)
    elif fault == "marker":
        state.metadata["realtime_runtime_owner"] = "strategy-checkpoint-v1"
    else:

        class ReturningValidator(CheckedStrategy):
            @staticmethod
            def validate_resume_state(state, **kwargs):
                return True

        strategy = ReturningValidator

    # Admission only needs the immutable bar sequence protocol before refusal.
    class Series:
        def __len__(self):
            return len(bars)

        def get_bar(self, index):
            return bars[index]

    with pytest.raises(ResumeUnsupportedError, match=message):
        admit_realtime_resume(state, config, strategy, Series())


@pytest.mark.parametrize("fault", ["row_fields", "time", "direction"])
def test_exit_template_complete_shape_and_temporal_domain(fault):
    from tests.unit.test_typed_json_resume import PersistentExitStrategy

    config, bars = inputs([(100, 101, 99, 100)] * 3)
    state = BacktestEngine(config).run(PersistentExitStrategy, bars=bars[:2]).resume_state
    row = state.broker_state.all_entry_exits["X"]
    if fault == "row_fields":
        row["extra"] = True
    elif fault == "time":
        row["time"] = True
    else:
        row["direction"] = "flat"
    with pytest.raises(ResumeUnsupportedError, match="exit"):
        JsonResumeStateSerializer().dumps(state)


def test_order_fill_link_cannot_change_its_entry_identity():
    state = checkpoint()
    state.broker_state.orders[0].entry_fill_index = 0
    state.broker_state.orders[0].from_entry = "foreign"
    with pytest.raises(ResumeUnsupportedError, match="order entry fill identity"):
        JsonResumeStateSerializer().dumps(state)


def test_strict_context_requires_prefix_identity_before_receiver_reset(monkeypatch):
    envelope = json.loads(JsonResumeStateSerializer().dumps(checkpoint()))
    del envelope["state"]["fields"]["metadata"]["items"]["bar_prefix_fingerprint"]
    assert_rejected_without_live_mutation(json.dumps(envelope).encode(), monkeypatch)


def test_lenient_context_defers_mismatch_to_existing_owner():
    config, bars = inputs(resume_validation_policy="lenient")
    producer = BacktestEngine(config)
    state = producer.run(FixedOcaStrategy, bars=bars[:2]).resume_state
    consumer = BacktestEngine(config)
    result = consumer.run(
        FixedOcaStrategy, bars=bars, resume_state=JsonResumeStateSerializer().dumps(state)
    )
    assert result.status == "completed", result.errors
    assert consumer.position.size == 4 and consumer.equity == 100023


def test_large_integer_json_text_counts_toward_output_byte_limit():
    state = replace(checkpoint(), strategy_state=[1 << 4095] * 100)
    full = JsonResumeStateSerializer().dumps(state)
    assert len(full) > 25000
    with pytest.raises(ResumeUnsupportedError, match="byte budget"):
        JsonResumeStateSerializer(max_bytes=40000).dumps(state)


@pytest.mark.parametrize(
    "fault,message",
    [
        ("undeclared", "must declare"),
        ("external", "external owner"),
        ("marker", "differs from selected strategy"),
        ("commit", "bar commit hook"),
    ],
)
def test_strategy_owned_tick_runtime_requires_single_declared_owner(fault, message):
    state = export_cut()
    config, bars = scenario()

    class StrategyOwner(CheckedStrategy):
        realtime_resume_runtime = "strategy" if fault != "undeclared" else None

    if fault not in {"external", "undeclared"}:
        config.runtime = None
        state = replace(state, runtime_state=None)
    if fault == "commit":
        state.metadata["realtime_runtime_owner"] = "strategy-checkpoint-v1"

    class Series:
        def __len__(self):
            return len(bars)

        def get_bar(self, index):
            return bars[index]

    with pytest.raises(ResumeUnsupportedError, match=message):
        admit_realtime_resume(state, config, StrategyOwner, Series())
