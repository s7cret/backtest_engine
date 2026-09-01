from __future__ import annotations

from typing import Any

import pytest

from openpine_contracts import validate_payload, verify_content_hash
from pinelib.errors import PL_DELEGATED_HANDLER_FAILURE, PineRuntimeError
from pinelib import (
    CallbackFrame,
    RuntimeLanguageContext,
    RuntimeSession,
)
from pinelib.events import SourceSpan

from backtest_engine.core.delegated_strategy_intents import (
    CLOSE_CAPABILITY_ID,
    DELEGATION_SCHEMA_ID,
    ENTRY_CAPABILITY_ID,
    OWNER,
    DelegatedStrategyIntentHandler,
    build_delegated_strategy_dispatcher,
)
from backtest_engine.core.intent_replay import IntentReplayIdentity


SOURCE_HASH = "sha256:" + "a" * 64
STACK_ID = "sha256:" + "b" * 64
PRODUCER_COMMIT = "c" * 40
ENTRY_SYMBOL = "pine:function:strategy.entry"
ENTRY_OVERLOAD = f"{ENTRY_SYMBOL}#canonical"
CLOSE_SYMBOL = "pine:function:strategy.close"
CLOSE_OVERLOAD = f"{CLOSE_SYMBOL}#canonical"


def _language() -> RuntimeLanguageContext:
    return RuntimeLanguageContext(
        6,
        "2026-09-01",
        "pine-v6",
        "sha256:" + "d" * 64,
        "compiler_annotation",
    )


def _span(line: int) -> SourceSpan:
    return SourceSpan(SOURCE_HASH, "main.pine", line, 2, line, 24)


def _identity() -> IntentReplayIdentity:
    return IntentReplayIdentity(
        run_id="run-rc6",
        strategy_id="strategy-rc6",
        stack_id=STACK_ID,
        semantic_profile="strict_5x",
        series_id="series-rc6",
        instrument_id="BINANCE:BTCUSDT",
        timeframe="1m",
    )


def _handler() -> DelegatedStrategyIntentHandler:
    return DelegatedStrategyIntentHandler(
        identity=_identity(),
        producer_commit=PRODUCER_COMMIT,
        bar_open_time_utc_ms={4: 1_725_145_600_000},
        default_qty_value=1.5,
    )


def _runtime(handler: DelegatedStrategyIntentHandler) -> RuntimeSession:
    dispatcher = build_delegated_strategy_dispatcher(handler)
    return RuntimeSession(_language(), delegated_dispatcher=dispatcher)


def _dispatch(
    tx: Any,
    *,
    symbol_id: str,
    overload_id: str,
    arguments: object,
    line: int,
) -> object:
    return tx.dispatch_delegated(
        owner=OWNER,
        schema_id=DELEGATION_SCHEMA_ID,
        capability_id=(
            ENTRY_CAPABILITY_ID
            if symbol_id == ENTRY_SYMBOL
            else CLOSE_CAPABILITY_ID
        ),
        symbol_id=symbol_id,
        overload_id=overload_id,
        arguments=arguments,
        call_site_id=f"main.pine:{line}:2",
        source_span=_span(line),
    )


def _seal_committed(
    handler: DelegatedStrategyIntentHandler,
    committed: Any,
    *,
    start_sequence: int = 0,
) -> list[dict[str, Any]]:
    return list(
        handler.seal_committed(
            [output.value for output in committed.delegated_outputs],
            start_sequence=start_sequence,
        )
    )


def test_manifest_aligned_dispatcher_registers_entry_and_direction_values() -> None:
    handler = _handler()
    dispatcher = build_delegated_strategy_dispatcher(handler)
    runtime = RuntimeSession(_language(), delegated_dispatcher=dispatcher)
    tx = runtime.begin(CallbackFrame("HISTORICAL_EVAL", 0, bar_index=4))

    direction = tx.resolve_delegated_value(
        owner=OWNER,
        schema_id=DELEGATION_SCHEMA_ID,
        capability_id="strategy.long",
    )
    tx.dispatch_delegated(
        owner=OWNER,
        schema_id=DELEGATION_SCHEMA_ID,
        capability_id=ENTRY_CAPABILITY_ID,
        symbol_id=ENTRY_SYMBOL,
        overload_id=ENTRY_OVERLOAD,
        arguments={"positional": ["L", direction], "named": {}},
        call_site_id="main.pine:3:2",
        source_span=_span(3),
    )
    committed = tx.commit()
    intents = _seal_committed(handler, committed)

    assert direction == "strategy.long"
    assert intents[0]["direction"] == "LONG"


def test_manifest_aligned_dispatcher_exposes_detached_broker_projection_values() -> None:
    projection = {
        "strategy.position_size": 2.5,
        "strategy.position_avg_price": 101.25,
        "strategy.position_entry_name": "L",
    }
    dispatcher = build_delegated_strategy_dispatcher(
        _handler(), strategy_values=projection
    )
    projection["strategy.position_size"] = 99
    runtime = RuntimeSession(_language(), delegated_dispatcher=dispatcher)
    tx = runtime.begin(CallbackFrame("HISTORICAL_EVAL", 0, bar_index=4))

    assert tx.resolve_delegated_value(
        owner=OWNER,
        schema_id=DELEGATION_SCHEMA_ID,
        capability_id="strategy.position_size",
    ) == 2.5
    assert tx.resolve_delegated_value(
        owner=OWNER,
        schema_id=DELEGATION_SCHEMA_ID,
        capability_id="strategy.position_avg_price",
    ) == 101.25
    assert tx.resolve_delegated_value(
        owner=OWNER,
        schema_id=DELEGATION_SCHEMA_ID,
        capability_id="strategy.position_entry_name",
    ) == "L"
    tx.abort()


def test_strategy_entry_converts_to_sealed_intents_committed_in_invocation_order() -> None:
    handler = _handler()
    tx = _runtime(handler).begin(
        CallbackFrame("HISTORICAL_EVAL", 0, bar_index=4, tick_index=0)
    )

    first = _dispatch(
        tx,
        symbol_id=ENTRY_SYMBOL,
        overload_id=ENTRY_OVERLOAD,
        arguments={
            "positional": ["L", "strategy.long"],
            "named": {"qty": 2.25, "limit": 101.5, "comment": "first"},
        },
        line=3,
    )
    second = _dispatch(
        tx,
        symbol_id=ENTRY_SYMBOL,
        overload_id=ENTRY_OVERLOAD,
        arguments={
            "positional": [],
            "named": {"id": "S", "direction": "strategy.short"},
        },
        line=4,
    )
    committed = tx.commit()

    intents = _seal_committed(handler, committed)
    assert [output.invocation.invocation_id for output in committed.delegated_outputs] == [
        first,
        second,
    ]
    assert [intent["sequence"] for intent in intents] == [0, 1]
    assert [intent["command_id"] for intent in intents] == ["L", "S"]
    assert [intent["order_id"] for intent in intents] == ["L", "S"]
    assert [intent["direction"] for intent in intents] == ["LONG", "SHORT"]
    assert [intent["qty"] for intent in intents] == ["2.25", "1.5"]
    assert intents[0]["limit"] == "101.5"
    assert intents[0]["comment"] == "first"
    assert intents[1]["limit"] is None
    assert intents[1]["stop"] is None
    assert all(intent["bar_index"] == 4 for intent in intents)
    assert all(intent["bar_open_time_utc_ms"] == 1_725_145_600_000 for intent in intents)
    assert all(intent["producer"] == "backtest_engine" for intent in intents)
    assert all(intent["producer_version"] == "5.0.0-rc.6" for intent in intents)
    assert all(intent["producer_commit"] == PRODUCER_COMMIT for intent in intents)
    assert all(intent["stack_id"] == STACK_ID for intent in intents)
    assert all(intent["source_span"]["source_hash"] == SOURCE_HASH for intent in intents)
    assert [intent["source_span"]["start_line"] for intent in intents] == [3, 4]
    assert [
        intent["idempotency_key"].removeprefix("intent-delivery:")
        for intent in intents
    ] == [
        output.invocation.invocation_id for output in committed.delegated_outputs
    ]
    for intent in intents:
        validate_payload("openpine.intent.v2", intent)
        assert verify_content_hash(intent, schema_id="openpine.intent.v2")


def test_strategy_close_converts_to_a_sealed_intent() -> None:
    handler = _handler()
    tx = _runtime(handler).begin(
        CallbackFrame("ORDER_FILL_RECALC", 0, bar_index=4, tick_index=2)
    )

    prepared = _dispatch(
        tx,
        symbol_id=CLOSE_SYMBOL,
        overload_id=CLOSE_OVERLOAD,
        arguments={
            "positional": ["L"],
            "named": {
                "comment": "flatten L",
                "qty": 0.75,
                "qty_percent": 50,
                "immediately": True,
            },
        },
        line=8,
    )
    committed = tx.commit()
    returned = _seal_committed(handler, committed)[0]

    assert [output.invocation.invocation_id for output in committed.delegated_outputs] == [
        prepared
    ]
    assert returned["kind"] == "close"
    assert returned["sequence"] == 0
    assert returned["command_id"] == "close:L"
    assert returned["from_entry"] == "L"
    assert returned["qty"] == "0.75"
    assert returned["qty_percent"] == "50"
    assert returned["comment"] == "flatten L"
    assert returned["immediately"] is True
    assert returned["phase"] == "ORDER_FILL_RECALC"
    assert returned["recalc_iteration"] == 2
    assert returned["source_span"]["start_line"] == 8
    validate_payload("openpine.intent.v2", returned)
    assert verify_content_hash(returned, schema_id="openpine.intent.v2")


def test_aborted_transaction_does_not_consume_intent_sequence() -> None:
    handler = _handler()
    runtime = _runtime(handler)
    aborted = runtime.begin(CallbackFrame("HISTORICAL_EVAL", 0, bar_index=4, tick_index=0))
    _dispatch(
        aborted,
        symbol_id=ENTRY_SYMBOL,
        overload_id=ENTRY_OVERLOAD,
        arguments={"positional": ["ABORTED", "strategy.long"], "named": {}},
        line=10,
    )
    aborted.abort()

    committed_tx = runtime.begin(
        CallbackFrame("HISTORICAL_EVAL", 1, bar_index=4, tick_index=0)
    )
    _dispatch(
        committed_tx,
        symbol_id=ENTRY_SYMBOL,
        overload_id=ENTRY_OVERLOAD,
        arguments={"positional": ["COMMITTED", "strategy.long"], "named": {}},
        line=11,
    )

    intents = _seal_committed(handler, committed_tx.commit())
    assert [intent["sequence"] for intent in intents] == [0]
    assert [intent["command_id"] for intent in intents] == ["COMMITTED"]


@pytest.mark.parametrize(
    ("symbol_id", "overload_id", "arguments", "message"),
    [
        (
            "pine:function:strategy.unknown",
            "pine:function:strategy.unknown#canonical",
            {"positional": [], "named": {}},
            "unsupported delegated strategy symbol or overload",
        ),
        (
            ENTRY_SYMBOL,
            ENTRY_OVERLOAD,
            {"positional": ["L"], "named": {}},
            "strategy.entry requires direction",
        ),
        (
            ENTRY_SYMBOL,
            ENTRY_OVERLOAD,
            {"positional": ["L", "strategy.long"], "named": {"bogus": 1}},
            "unknown named argument",
        ),
    ],
)
def test_invalid_delegated_strategy_invocation_fails_without_output(
    symbol_id: str,
    overload_id: str,
    arguments: object,
    message: str,
) -> None:
    handler = _handler()
    runtime = _runtime(handler)
    tx = runtime.begin(
        CallbackFrame("HISTORICAL_EVAL", 0, bar_index=4, tick_index=0)
    )

    _dispatch(
        tx,
        symbol_id=symbol_id,
        overload_id=overload_id,
        arguments=arguments,
        line=12,
    )
    with pytest.raises(PineRuntimeError) as error_info:
        tx.commit()

    assert error_info.value.code == PL_DELEGATED_HANDLER_FAILURE
    assert isinstance(error_info.value.__cause__, ValueError)
    assert message in str(error_info.value.__cause__)
    assert runtime.transcript.entries[-1]["committed"] is False
