from __future__ import annotations

import pytest

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


def _event(kind: str, **overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "kind": kind,
        "idempotency_key": "strategy:command:0",
        "bar_index": 0,
        "origin_command_kind": "strategy.entry.long",
        "qty": "1.25",
        "limit": None,
        "stop": "100.5",
    }
    event.update(overrides)
    return event


def test_intent_replay_covers_all_commands_and_fail_closed_edges() -> None:
    ctx = _Context()
    for kind in ("entry", "order", "exit", "close", "cancel", "cancel_all", "risk"):
        apply_intent(ctx, _event(kind))

    assert [call[0] for call in ctx.calls] == [
        "entry",
        "order",
        "exit",
        "close",
        "cancel",
        "cancel_all",
    ]
    assert ctx.calls[0][2]["qty"] == 1.25
    assert ctx.calls[0][2]["limit"] is None
    assert ctx.calls[0][2]["stop"] == 100.5

    with pytest.raises(IntentReplayError, match="idempotency_key"):
        apply_intent(ctx, _event("cancel", idempotency_key="invalid"))
    with pytest.raises(IntentReplayError, match="origin_command_kind"):
        apply_intent(ctx, _event("entry", origin_command_kind="strategy.entry"))
    with pytest.raises(IntentReplayError, match="unknown intent kind"):
        apply_intent(ctx, _event("unknown"))

    assert apply_intents_for_bar(ctx, [_event("risk", bar_index=1)], 0) == 0
    assert apply_intents_for_bar(ctx, [_event("risk")], 0) == 1

    with pytest.raises(IntentReplayError, match="empty"):
        require_live_tape([])
    with pytest.raises(IntentReplayError, match="live pinelib tape required"):
        require_live_tape([_event("risk")])
    live = _event(
        "risk",
        schema_id="openpine.intent.v2",
        content_hash="sha256:fixture",
    )
    assert apply_live_intents_for_bar(ctx, [live], 0) == 1


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
    assert strategy_reads_broker_projection(type("DynamicStrategy", (), {})) is False

    config = BacktestConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
    )
    config.semantic_profile = "unknown-profile"  # type: ignore[assignment]
    with pytest.raises(ConfigError, match="semantic_profile"):
        validate_backtest_config(config)
