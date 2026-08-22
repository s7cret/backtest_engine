from __future__ import annotations

import pytest
from openpine_contracts import seal_content_hash

from backtest_engine import BacktestConfig
from backtest_engine.core.engine_validation import validate_backtest_config
from backtest_engine.core.intent_replay import (
    IntentReplayError,
    apply_intent,
    apply_intents_for_bar,
    apply_live_intents_for_bar,
    require_live_tape,
)
from backtest_engine.core.warmup import (
    WarmupPhase,
    WarmupPhaseMachine,
    strategy_reads_broker_projection,
)
from backtest_engine.errors import ConfigError


class _Context:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def _record(self, name: str, *args: object, **kwargs: object) -> None:
        self.calls.append((name, args, kwargs))

    def entry(self, *args: object, **kwargs: object) -> None:
        self._record("entry", *args, **kwargs)

    def order(self, *args: object, **kwargs: object) -> None:
        self._record("order", *args, **kwargs)

    def exit(self, *args: object, **kwargs: object) -> None:
        self._record("exit", *args, **kwargs)

    def close(self, *args: object, **kwargs: object) -> None:
        self._record("close", *args, **kwargs)

    def cancel(self, *args: object, **kwargs: object) -> None:
        self._record("cancel", *args, **kwargs)

    def cancel_all(self) -> None:
        self._record("cancel_all")

    def close_all(self, *args: object, **kwargs: object) -> None:
        self._record("close_all", *args, **kwargs)

    def risk_allow_entry_in(self, *args: object, **kwargs: object) -> None:
        self._record("risk_allow_entry_in", *args, **kwargs)


def _event(kind: str, **overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "schema_id": "openpine.intent.v2",
        "schema_version": "2.1.0",
        "producer": "pinelib",
        "producer_version": "5.0.0-rc.3",
        "producer_commit": "801b908e0ba53d1387cfd032cb6d29aa53ba0ca0",
        "stack_id": "stack-5",
        "created_at_utc_ms": 0,
        "serializer_id": "openpine.canonical.json.v1",
        "content_hash_alg": "sha256",
        "event_id": "event-0",
        "sequence": 0,
        "command_id": "command",
        "kind": kind,
        "run_id": "run",
        "strategy_id": "strategy",
        "series_id": "series",
        "instrument_id": "S",
        "timeframe": "1m",
        "bar_index": 0,
        "bar_open_time_utc_ms": 0,
        "phase": "score",
        "recalc_iteration": 0,
        "semantic_profile": "strict_5x",
        "source_span": {
            "start_offset": 0,
            "end_offset": 1,
            "start_line": 1,
            "start_col": 0,
            "end_line": 1,
            "end_col": 1,
        },
        "idempotency_key": "delivery-0",
    }
    if kind in {"entry", "order"}:
        event.update(order_id="command", direction="long", qty="1.25", stop="100.5")
    elif kind == "exit":
        event.update(order_id="X", from_entry="L")
    elif kind == "close":
        event.update(from_entry="L")
    elif kind == "cancel":
        event.update(order_id="command")
    elif kind == "risk":
        event.update(
            risk_rule="allow_entry_in",
            risk_value="0",
            risk_unit="long",
            risk_scope="entries",
        )
    event.update(overrides)
    return seal_content_hash(event)


def test_intent_replay_covers_all_commands_and_fail_closed_edges() -> None:
    ctx = _Context()
    for kind in (
        "entry",
        "order",
        "exit",
        "close",
        "close_all",
        "cancel",
        "cancel_all",
        "risk",
    ):
        apply_intent(ctx, _event(kind))

    assert [call[0] for call in ctx.calls] == [
        "entry",
        "order",
        "exit",
        "close",
        "close_all",
        "cancel",
        "cancel_all",
        "risk_allow_entry_in",
    ]
    assert ctx.calls[0][2]["qty"] == 1.25
    assert ctx.calls[0][2]["limit"] is None
    assert ctx.calls[0][2]["stop"] == 100.5

    tampered = _event("cancel")
    tampered["command_id"] = "tampered"
    with pytest.raises(IntentReplayError, match="content hash"):
        apply_intent(ctx, tampered)
    with pytest.raises(IntentReplayError, match="schema validation"):
        apply_intent(ctx, _event("unknown"))

    assert apply_intents_for_bar(ctx, [_event("risk", bar_index=1)], 0) == 0
    assert apply_intents_for_bar(ctx, [_event("risk")], 0) == 1

    with pytest.raises(IntentReplayError, match="empty"):
        require_live_tape([])
    unsealed = _event("risk")
    unsealed.pop("content_hash")
    with pytest.raises(IntentReplayError, match="schema validation"):
        require_live_tape([unsealed])
    assert apply_live_intents_for_bar(ctx, [_event("risk")], 0) == 1


def test_warmup_machine_and_semantic_profile_error_edges() -> None:
    with pytest.raises(ValueError, match="unknown warmup_policy"):
        WarmupPhaseMachine("UNKNOWN", warmup_end_index=0, score_end_index=1, series_len=2)

    machine = WarmupPhaseMachine(
        "CALC_THEN_RESET_BROKER",
        warmup_end_index=0,
        score_end_index=1,
        series_len=2,
    )
    machine.finalize()
    decision = machine.begin_bar(0)
    assert decision.phase is WarmupPhase.FINALIZED
    assert decision.intent_emission_allowed is False
    assert decision.broker_execution_allowed is False
    assert (
        strategy_reads_broker_projection(
            type("DynamicStrategy", (), {"required_runtime_capabilities": ()})
        )
        is False
    )

    config = BacktestConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
    finality_policy="ALLOW_OPEN",
     )
    config.semantic_profile = "unknown-profile"  # type: ignore[assignment]
    with pytest.raises(ConfigError, match="semantic_profile"):
        validate_backtest_config(config)
