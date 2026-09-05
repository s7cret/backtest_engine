from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from openpine_contracts import Finality, seal_content_hash

import backtest_engine.core.intent_replay as replay_module
from backtest_engine import BacktestConfig, BacktestEngine, Bar
from backtest_engine.core.intent_replay import (
    IntentIdentityError,
    IntentReplayIdentity,
    IntentReplayError,
    IntentTapeValidationError,
    UnsupportedRiskIntentError,
    apply_intents_for_bar,
    require_live_tape,
    validate_intent_tape,
)


IDENTITY = IntentReplayIdentity(
    run_id="run",
    strategy_id="strategy",
    stack_id="sha256:" + ("d" * 64),
    semantic_profile="strict_5x",
    series_id="series",
    instrument_id="S",
    timeframe="1m",
)


def _event(
    sequence: int,
    *,
    kind: str = "entry",
    command_id: str = "L",
    bar_index: int = 2,
    bar_time: int = 1_002,
    recalc_iteration: int = 0,
    **updates: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_id": "openpine.intent.v2",
        "schema_version": "2.2.0",
        "producer": "pinelib",
        "producer_version": "5.0.0-rc.5",
        "producer_commit": "801b908e0ba53d1387cfd032cb6d29aa53ba0ca0",
        "stack_id": IDENTITY.stack_id,
        "created_at_utc_ms": bar_time,
        "serializer_id": "openpine.canonical.json.v1",
        "content_hash_alg": "sha256",
        "event_id": f"event-{sequence}",
        "sequence": sequence,
        "command_id": command_id,
        "kind": kind,
        "run_id": IDENTITY.run_id,
        "strategy_id": IDENTITY.strategy_id,
        "series_id": IDENTITY.series_id,
        "instrument_id": IDENTITY.instrument_id,
        "timeframe": IDENTITY.timeframe,
        "bar_index": bar_index,
        "bar_open_time_utc_ms": bar_time,
        "phase": "score",
        "recalc_iteration": recalc_iteration,
        "semantic_profile": IDENTITY.semantic_profile,
        "source_span": {
            "known": True,
            "source_hash": "sha256:" + ("c" * 64),
            "start_offset": 0,
            "end_offset": 1,
            "start_line": 1,
            "start_col": 0,
            "end_line": 1,
            "end_col": 1,
        },
        "idempotency_key": f"misleading:key:{sequence}",

    }
    if kind in {"entry", "order"}:
        payload.update(order_id=command_id, direction="LONG", qty="1")
    elif kind == "exit":
        payload.update(order_id=command_id, from_entry="L")
    elif kind == "close":
        payload.update(from_entry="L")
    elif kind == "cancel":
        payload.update(order_id=command_id)
    elif kind == "risk":
        payload.update(
            risk_rule="allow_entry_in",
            risk_value="1",
            risk_unit="long",
            risk_scope="entries",
        )
    payload.update(updates)
    return seal_content_hash(payload, schema_id="openpine.intent.v2")


class RecordingContext:
    def __init__(self, **config: Any) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.config = SimpleNamespace(
            qty_step=config.get("qty_step"),
            mintick=config.get("mintick"),
            qty_rounding=config.get("qty_rounding", "floor"),
            price_rounding=config.get("price_rounding", "nearest"),
        )

    def __getattr__(self, name: str):
        def record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return record


class LiveEntry:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 2:
            self.ctx.entry("L", "long", qty=1.0)


class ReplayEntry:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx
        self.tape = params["tape"]

    def _process_bar(self, bar, bar_index):
        apply_intents_for_bar(
            self.ctx,
            self.tape,
            bar_index,
            bar_open_time_utc_ms=bar.time,
            expected_identity=IDENTITY,
        )


def _bars():
    return [
        Bar(
            time=1_000 + i,
            open=10.0 + i,
            high=11.0 + i,
            low=9.0 + i,
            close=10.5 + i,
            finality=Finality.FINAL,
        )
        for i in range(6)
    ]


def _cfg():
    return BacktestConfig(
        symbol="S",
        timeframe="1m",
        start_time=1_000,
        end_time=1_005,
        commission_type="none",
        initial_capital=10_000,
        score_start_time=1_000,
        score_end_time=1_005,
        semantic_profile="strict_5x",
    )


def test_strict_tape_replay_matches_live_score_ledger_hash() -> None:
    live = BacktestEngine(_cfg()).run(LiveEntry, bars=_bars())
    tape = [_event(0)]
    replayed = BacktestEngine(_cfg()).run(ReplayEntry, {"tape": tape}, bars=_bars())
    assert live.score_ledger_hash
    assert replayed.score_ledger_hash == live.score_ledger_hash


def test_schema_and_sealed_hash_are_verified_before_any_apply() -> None:
    ctx = RecordingContext()
    invalid_schema = _event(0)
    invalid_schema["not_in_schema"] = True
    with pytest.raises(IntentTapeValidationError, match="schema"):
        apply_intents_for_bar(ctx, [invalid_schema], 2, expected_identity=IDENTITY)
    assert ctx.calls == []

    tampered = _event(0)
    tampered["qty"] = "99"
    with pytest.raises(IntentTapeValidationError, match="content hash"):
        apply_intents_for_bar(ctx, [tampered], 2, expected_identity=IDENTITY)
    assert ctx.calls == []


@pytest.mark.parametrize(
    ("events", "message"),
    [
        ([_event(1)], "sequence"),
        ([_event(0), _event(2)], "sequence"),
        ([_event(0, bar_index=3), _event(1, bar_index=2)], "bar order"),
        (
            [
                _event(0, recalc_iteration=1),
                _event(1, recalc_iteration=0),
            ],
            "recalc",
        ),
    ],
)
def test_sequence_bar_and_recalc_order_are_fail_closed(
    events: list[dict[str, Any]], message: str
) -> None:
    with pytest.raises(IntentTapeValidationError, match=message):
        validate_intent_tape(events, expected_identity=IDENTITY)


def test_event_id_conflict_is_fail_closed() -> None:
    events = [_event(0), _event(1)]
    events[1]["event_id"] = events[0]["event_id"]
    events[1] = seal_content_hash(events[1])
    with pytest.raises(IntentTapeValidationError, match="event_id conflict"):
        validate_intent_tape(events, expected_identity=IDENTITY)


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "strategy_id",
        "stack_id",
        "semantic_profile",
        "series_id",
        "instrument_id",
        "timeframe",
    ],
)
def test_run_strategy_stack_profile_and_series_identity_are_checked(field: str) -> None:
    event = _event(0)
    event[field] = (
        "legacy_4x"
        if field == "semantic_profile"
        else ("sha256:" + ("e" * 64) if field == "stack_id" else "drift")
    )
    event = seal_content_hash(event, schema_id="openpine.intent.v2")
    with pytest.raises(IntentIdentityError, match=field):
        validate_intent_tape([event], expected_identity=IDENTITY)


def test_current_bar_identity_is_checked_before_apply() -> None:
    ctx = RecordingContext()
    with pytest.raises(IntentIdentityError, match="bar_open_time_utc_ms"):
        apply_intents_for_bar(
            ctx,
            [_event(0)],
            2,
            bar_open_time_utc_ms=9_999,
            expected_identity=IDENTITY,
        )
    assert ctx.calls == []


def test_direct_command_id_and_direction_are_used_without_origin_guessing() -> None:
    ctx = RecordingContext()
    apply_intents_for_bar(ctx, [_event(0, command_id="DIRECT", direction="LONG")], 2)
    assert ctx.calls[0][0] == "entry"
    assert ctx.calls[0][1] == ("DIRECT", "long")


def test_same_command_id_same_bar_replacements_remain_ordered_distinct_events() -> None:
    ctx = RecordingContext()
    events = [
        _event(0, command_id="L", qty="1", recalc_iteration=0),
        _event(1, command_id="L", qty="2", recalc_iteration=1),
    ]
    assert apply_intents_for_bar(ctx, events, 2) == 2
    assert [call[2]["qty"] for call in ctx.calls] == [1.0, 2.0]


def test_close_all_is_not_guessed_from_close() -> None:
    ctx = RecordingContext()
    events = [_event(0, kind="close_all", command_id="all")]
    apply_intents_for_bar(ctx, events, 2)
    assert ctx.calls == [("close_all", (), {"immediately": False, "comment": None})]


def test_all_supported_risk_intents_are_applied() -> None:
    ctx = RecordingContext()
    events = [
        _event(0, kind="risk", command_id="allow", risk_rule="allow_entry_in"),
        _event(
            1,
            kind="risk",
            command_id="dd-pct",
            risk_rule="max_drawdown",
            risk_value="12.5",
            risk_unit="percent_of_equity",
            risk_scope="strategy",
        ),
        _event(
            2,
            kind="risk",
            command_id="dd-cash",
            risk_rule="max_drawdown",
            risk_value="250",
            risk_unit="cash",
            risk_scope="strategy",
        ),
        _event(
            3,
            kind="risk",
            command_id="position",
            risk_rule="max_position_size",
            risk_value="3",
            risk_unit="fixed",
            risk_scope="strategy",
        ),
    ]
    apply_intents_for_bar(ctx, events, 2)
    assert ctx.calls == [
        ("risk_allow_entry_in", ("long",), {}),
        ("risk_max_drawdown", (12.5, "percent_of_equity"), {}),
        ("risk_max_drawdown", (250.0, "cash"), {}),
        ("risk_max_position_size", (3.0, "fixed"), {}),
    ]


def test_unsupported_risk_intent_is_typed_fail_closed() -> None:
    ctx = RecordingContext()
    with pytest.raises(UnsupportedRiskIntentError, match="max_intraday_loss"):
        apply_intents_for_bar(
            ctx,
            [_event(0, kind="risk", risk_rule="max_intraday_loss")],
            2,
        )
    assert ctx.calls == []


def test_decimal_boundary_rounds_before_float_conversion() -> None:
    ctx = RecordingContext(
        qty_step=0.01,
        mintick=0.05,
        qty_rounding="nearest",
        price_rounding="nearest",
    )
    event = _event(0, qty="1.005", limit="10.025")
    apply_intents_for_bar(ctx, [event], 2)
    assert ctx.calls[0][2]["qty"] == 1.01
    assert ctx.calls[0][2]["limit"] == 10.05


def test_require_live_tape_rejects_empty_and_returns_validated_copy() -> None:
    with pytest.raises(IntentReplayError, match="empty"):
        require_live_tape([])
    event = _event(0)
    validated = require_live_tape([event], expected_identity=IDENTITY)
    event["qty"] = "99"
    assert validated.events[0]["qty"] == "1"


def test_require_live_tape_can_validate_a_suffix_from_sequence_origin() -> None:
    first = _event(0)
    later = _event(1)
    require_live_tape([first, later], expected_identity=IDENTITY)
    suffix = require_live_tape(
        [later], expected_identity=IDENTITY, sequence_origin=1
    )
    assert suffix.events[0]["sequence"] == 1


def test_admit_sealed_intent_tape_skips_jsonschema_but_keeps_identity(monkeypatch) -> None:
    admit_sealed_intent_tape = getattr(replay_module, "admit_sealed_intent_tape", None)
    assert admit_sealed_intent_tape is not None

    calls = {"n": 0}
    real = replay_module.validate_payload

    def counting(schema_id, payload):
        calls["n"] += 1
        return real(schema_id, payload)

    monkeypatch.setattr(replay_module, "validate_payload", counting)
    event = _event(0)
    calls["n"] = 0
    admitted = admit_sealed_intent_tape([event], expected_identity=IDENTITY)
    assert calls["n"] == 0
    assert admitted.identity == IDENTITY
    assert admitted.events[0]["sequence"] == 0

    with pytest.raises(replay_module.IntentIdentityError):
        admit_sealed_intent_tape(
            [event],
            expected_identity=IntentReplayIdentity(
                run_id="other",
                strategy_id=IDENTITY.strategy_id,
                stack_id=IDENTITY.stack_id,
                semantic_profile=IDENTITY.semantic_profile,
                series_id=IDENTITY.series_id,
                instrument_id=IDENTITY.instrument_id,
                timeframe=IDENTITY.timeframe,
            ),
        )


def test_replay_validation_and_decimal_defensive_edges() -> None:
    with pytest.raises(IntentTapeValidationError, match="not a mapping"):
        validate_intent_tape([object()])  # type: ignore[list-item]

    for rule, unit in (
        ("allow_entry_in", "sideways"),
        ("max_drawdown", "ticks"),
        ("max_position_size", "percent"),
    ):
        with pytest.raises(UnsupportedRiskIntentError):
            validate_intent_tape(
                [_event(0, kind="risk", risk_rule=rule, risk_unit=unit)]
            )

    conflicting_time = [
        _event(0, bar_time=1_002),
        _event(1, bar_time=1_003),
    ]
    with pytest.raises(IntentIdentityError, match="conflicts"):
        validate_intent_tape(conflicting_time)

    with pytest.raises(IntentReplayError, match="direction"):
        replay_module._normalize_direction("sideways")
    with pytest.raises(IntentReplayError, match="rounding mode"):
        replay_module._rounding_mode("bankers")

    for config, value, message in (
        ({"qty_step": object()}, "1", "invalid qty rounding step"),
        ({"qty_step": -1}, "1", "positive and finite"),
        ({}, 1, "decimal string"),
        ({}, "not-a-decimal", "invalid intent"),
        ({}, "NaN", "must be finite"),
        ({}, "1e9999", "outside engine float range"),
    ):
        ctx = RecordingContext(**config)
        with pytest.raises(IntentReplayError, match=message):
            replay_module.decimal_to_engine_number(value, field="qty", ctx=ctx)

    with pytest.raises(UnsupportedRiskIntentError):
        replay_module._apply_risk_intent(
            RecordingContext(),
            {"risk_rule": "unsupported", "risk_unit": "x", "risk_value": "1"},
        )
    with pytest.raises(IntentReplayError, match="unknown intent kind"):
        replay_module._apply_validated_intent(
            RecordingContext(), {"kind": "unknown", "command_id": "x"}
        )


def test_validated_tape_expected_identity_mismatch_is_rejected() -> None:
    tape = validate_intent_tape([_event(0)])
    other = IntentReplayIdentity(
        run_id="other",
        strategy_id=IDENTITY.strategy_id,
        stack_id=IDENTITY.stack_id,
        semantic_profile=IDENTITY.semantic_profile,
        series_id=IDENTITY.series_id,
        instrument_id=IDENTITY.instrument_id,
        timeframe=IDENTITY.timeframe,
    )
    with pytest.raises(IntentIdentityError, match="validated tape identity"):
        apply_intents_for_bar(RecordingContext(), tape, 2, expected_identity=other)
