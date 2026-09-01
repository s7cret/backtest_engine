from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from marketdata_provider.canonical.bar import make_canonical_bar
from openpine_contracts import (
    Finality,
    canonical_dumps,
    seal_content_hash,
    validate_payload,
    verify_content_hash,
)

from backtest_engine import BacktestCallbacks, BacktestConfig, BacktestEngine, Bar
from backtest_engine.core.protocol_boundary import (
    _closed_trade_rows,
    _engine_identity,
    _semver,
    _trade_for_fill,
    emit_protocol_bar_begin,
    emit_protocol_bar_commit,
    prepare_protocol_run,
    sealed_broker_projection,
)

STACK_ID = "sha256:" + ("d" * 64)
HASH_A = "sha256:" + ("a" * 64)
HASH_B = "sha256:" + ("b" * 64)
HASH_C = "sha256:" + ("c" * 64)
HASH_D = "sha256:" + ("e" * 64)
ENGINE_COMMIT = "0dc0b955a1b57a8ec0b9cc2f585853c6979e2a90"
MARKETDATA_COMMIT = "f" * 40
STACK_COMPONENTS = (
    "openpine-contracts",
    "marketdata-provider",
    "pinelib",
    "pine2ast",
    "ast2python",
    "backtest_engine",
    "optimizer",
    "openpine",
)


class StatefulEnter:
    required_runtime_capabilities: tuple[str, ...] = ()

    def __init__(self, params: dict[str, Any], runtime: object, ctx: object) -> None:
        del params, runtime
        self.ctx = ctx
        self.calls = 0

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        del bar
        self.calls += 1
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)

    def export_state(self) -> dict[str, int]:
        return {"calls": self.calls}


class StatelessStrategy:
    required_runtime_capabilities: tuple[str, ...] = ()

    def __init__(self, params: dict[str, Any], runtime: object, ctx: object) -> None:
        del params, runtime, ctx

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        del bar, bar_index


def _execution_context() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_id": "openpine.execution_context.v1",
        "schema_version": "1.0.0",
        "producer": "openpine",
        "producer_version": "5.0.0-rc.5",
        "producer_commit": "1" * 40,
        "stack_id": STACK_ID,
        "created_at_utc_ms": 0,
        "serializer_id": "openpine.canonical.json.v1",
        "content_hash_alg": "sha256",
        "run_id": "run-rc4",
        "strategy_id": "strategy-rc4",
        "session_id": "session-rc4",
        "stack_manifest_hash": STACK_ID,
        "wheel_identities": [
            {"name": name, "version": "5.0.0rc5", "content_hash": HASH_A}
            for name in STACK_COMPONENTS
        ],
        "schema_hashes": {
            "openpine.execution_context.v1": HASH_A,
            "openpine.worker.protocol.v2": HASH_B,
            "openpine.checkpoint.v1": HASH_C,
            "openpine.checkpoint.proof.v1": HASH_D,
            "openpine.intent.v2": HASH_A,
        },
        "generated_artifact_hash": HASH_B,
        "source_hash": HASH_C,
        "emitted_module_hash": HASH_D,
        "data_snapshot_hash": HASH_A,
        "series_id": "binance:spot:BTCUSDT:1m",
        "instrument_id": "binance:spot:BTCUSDT",
        "exchange": "binance",
        "market": "spot",
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "timezone": "UTC",
        "currency": "USDT",
        "mintick": "0.01",
        "pointvalue": "1",
        "session_policy": "24x7",
        "semantic_profile": "strict_5x",
        "finality_policy": "CLOSED_BAR_ONLY",
        "warmup_policy": "CALC_ONLY",
        "score_policy": "ALL_BARS",
        "end_policy": "PRESERVE_OPEN_POSITIONS",
        "capabilities": [
            "closed_bar",
            "deterministic_clock",
            "sealed_artifact_refs",
        ],
        "producer_commits": {
            "openpine-contracts": "a" * 40,
            "pine2ast": "b" * 40,
            "ast2python": "c" * 40,
            "pinelib": "d" * 40,
            "marketdata-provider": MARKETDATA_COMMIT,
            "backtest_engine": ENGINE_COMMIT,
            "optimizer": "2" * 40,
            "openpine": "1" * 40,
        },
        "policy_registry_version": "openpine.policies.rc4.v1",
        "schema_registry_version": "openpine.schemas.rc4.v1",
        "capability_registry_version": "openpine.capabilities.rc4.v1",
    }
    sealed = seal_content_hash(payload, schema_id="openpine.execution_context.v1")
    validate_payload("openpine.execution_context.v1", sealed)
    return sealed


def _bar_envelopes() -> list[dict[str, Any]]:
    return [
        make_canonical_bar(
            instrument_id="binance:spot:BTCUSDT",
            timeframe="1m",
            open_time_utc_ms=index * 60_000,
            open=str(100 + index),
            high=str(102 + index),
            low=str(99 + index),
            close=str(101 + index),
            volume=str(10 + index),
            snapshot_id="snapshot-rc4",
            provider="binance",
            provider_revision={"known": True, "revision": "binance-rc4"},
            producer_commit=MARKETDATA_COMMIT,
            stack_id=STACK_ID,
            finality="FINAL",
            created_at_utc_ms=0,
        )
        for index in range(2)
    ]


def _engine_bars(envelopes: list[dict[str, Any]]) -> list[Bar]:
    return [
        Bar(
            time=int(envelope["open_time_utc_ms"]),
            open=float(envelope["open"]),
            high=float(envelope["high"]),
            low=float(envelope["low"]),
            close=float(envelope["close"]),
            volume=float(envelope["volume"]),
            time_close=int(envelope["close_time_utc_ms"]),
            finality=Finality.FINAL,
        )
        for envelope in envelopes
    ]


def _config() -> BacktestConfig:
    return BacktestConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
        initial_capital=10_000,
        commission_type="none",
        process_orders_on_close=True,
        currency="USDT",
    )


def _run_protocol(
    strategy_class: type = StatefulEnter,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    context = _execution_context()
    envelopes = _bar_envelopes()
    events: list[dict[str, Any]] = []
    callbacks = BacktestCallbacks(on_protocol_callback=events.append)
    BacktestEngine(_config()).run(
        strategy_class,
        bars=_engine_bars(envelopes),
        callbacks=callbacks,
        execution_context=context,
        bar_envelopes=envelopes,
    )
    return events, envelopes, context


def test_protocol_bar_begin_forwards_exact_admitted_envelope_and_both_artifacts() -> (
    None
):
    events, envelopes, context = _run_protocol()

    assert [event["kind"] for event in events] == [
        "BAR_BEGIN",
        "BAR_COMMIT",
        "BAR_BEGIN",
        "BAR_COMMIT",
    ]
    begins = [event for event in events if event["kind"] == "BAR_BEGIN"]
    assert begins[0]["bar"] is envelopes[0]
    assert begins[1]["bar"] is envelopes[1]
    assert begins[0]["bar_hash"] == envelopes[0]["bar_content_hash"]
    assert (
        begins[0]["broker_projection"]["schema_id"] == "openpine.broker_projection.v1"
    )
    assert begins[0]["run_id"] == context["run_id"]
    assert begins[0]["strategy_id"] == context["strategy_id"]
    assert begins[0]["series_id"] == context["series_id"]
    assert begins[0]["instrument_id"] == context["instrument_id"]
    assert begins[0]["timeframe"] == context["timeframe"]
    assert begins[0]["phase"] == "score"


def test_tampered_bar_envelope_is_rejected_before_any_protocol_callback() -> None:
    context = _execution_context()
    envelopes = _bar_envelopes()
    envelopes[0]["close"] = "999"
    events: list[dict[str, Any]] = []

    with pytest.raises(ValueError, match="content hash"):
        BacktestEngine(_config()).run(
            StatefulEnter,
            bars=_engine_bars(_bar_envelopes()),
            callbacks=BacktestCallbacks(on_protocol_callback=events.append),
            execution_context=context,
            bar_envelopes=envelopes,
        )

    assert events == []


def test_valid_but_misaligned_bar_envelope_is_rejected_before_callback() -> None:
    context = _execution_context()
    envelopes = _bar_envelopes()
    wrong = _bar_envelopes()
    wrong[0] = make_canonical_bar(
        instrument_id="binance:spot:BTCUSDT",
        timeframe="1m",
        open_time_utc_ms=120_000,
        open="200",
        high="202",
        low="199",
        close="201",
        volume="20",
        snapshot_id="snapshot-rc4",
        provider="binance",
        provider_revision={"known": True, "revision": "binance-rc4"},
        producer_commit=MARKETDATA_COMMIT,
        stack_id=STACK_ID,
        finality="FINAL",
        created_at_utc_ms=0,
    )
    events: list[dict[str, Any]] = []

    with pytest.raises(ValueError, match="does not match engine bar"):
        BacktestEngine(_config()).run(
            StatefulEnter,
            bars=_engine_bars(envelopes),
            callbacks=BacktestCallbacks(on_protocol_callback=events.append),
            execution_context=context,
            bar_envelopes=wrong,
        )

    assert events == []


@pytest.mark.parametrize(
    ("context", "envelopes", "missing"),
    [
        (_execution_context(), None, "bar_envelopes"),
        (None, _bar_envelopes(), "execution_context"),
    ],
)
def test_sealed_protocol_callback_fails_closed_when_required_input_is_missing(
    context: dict[str, Any] | None,
    envelopes: list[dict[str, Any]] | None,
    missing: str,
) -> None:
    events: list[dict[str, Any]] = []
    bars = _engine_bars(_bar_envelopes())

    with pytest.raises(ValueError, match=missing):
        BacktestEngine(_config()).run(
            StatefulEnter,
            bars=bars,
            callbacks=BacktestCallbacks(on_protocol_callback=events.append),
            execution_context=context,
            bar_envelopes=envelopes,
        )

    assert events == []


def test_broker_projection_is_schema_valid_sealed_decimal_and_deterministic() -> None:
    first, _, _ = _run_protocol()
    second, _, _ = _run_protocol()
    first_projections = [event["broker_projection"] for event in first]
    second_projections = [event["broker_projection"] for event in second]

    assert [item["content_hash"] for item in first_projections] == [
        item["content_hash"] for item in second_projections
    ]
    for projection in first_projections:
        validate_payload("openpine.broker_projection.v1", projection)
        assert verify_content_hash(
            projection, schema_id="openpine.broker_projection.v1"
        )
        assert isinstance(projection["cash"], str)
        assert isinstance(projection["equity"], str)
        assert isinstance(projection["max_drawdown"], str)
        assert isinstance(projection["max_runup"], str)
        assert isinstance(projection["position"]["qty"], str)


def test_projection_preserves_engine_drawdown_and_runup() -> None:
    engine = BacktestEngine(_config())
    engine.state.max_drawdown = 42.5
    engine.state.max_runup = 17.25
    projection = sealed_broker_projection(
        engine,
        _execution_context(),
        bar=_engine_bars(_bar_envelopes())[0],
        bar_index=0,
        recalc_iteration=0,
    )

    assert projection["max_drawdown"] == "42.5"
    assert projection["max_runup"] == "17.25"
    validate_payload("openpine.broker_projection.v1", projection)


def test_bar_commit_carries_exact_sealed_state_bytes_and_final_projection() -> None:
    events, _, context = _run_protocol()
    first_begin = events[0]
    first_commit = events[1]

    assert first_begin["broker_projection"]["position"]["direction"] == "FLAT"
    assert first_commit["broker_projection"]["position"]["direction"] == "LONG"
    assert (
        first_commit["broker_projection_hash"]
        == first_commit["broker_projection"]["content_hash"]
    )

    state_artifact = first_commit["state_artifact"]
    state_payload = json.loads(state_artifact["bytes"])
    assert state_artifact["size_bytes"] == len(state_artifact["bytes"])
    assert state_artifact["artifact_hash"] == state_payload["content_hash"]
    assert state_artifact["bytes"] == canonical_dumps(state_payload).encode("utf-8")
    assert verify_content_hash(state_payload, schema_id="openpine.runtime.state.v1")
    assert state_payload["run_id"] == context["run_id"]
    assert state_payload["strategy_id"] == context["strategy_id"]
    assert state_payload["bar_index"] == 0
    assert state_payload["phase"] == "score"

    projection_artifact = first_commit["broker_projection_artifact"]
    assert (
        projection_artifact["artifact_hash"] == first_commit["broker_projection_hash"]
    )
    assert json.loads(projection_artifact["bytes"]) == first_commit["broker_projection"]

    body = {
        "run_id": first_commit["run_id"],
        "bar_index": first_commit["bar_index"],
        "recalc_iteration": first_commit["recalc_iteration"],
        "state_hash": state_artifact["artifact_hash"],
        "broker_projection_hash": projection_artifact["artifact_hash"],
        "state_ref": {
            key: state_artifact[key]
            for key in ("artifact_hash", "schema_id", "codec", "size_bytes")
        }
        | {"uri": "file:///persisted/state.json"},
        "broker_projection_ref": {
            key: projection_artifact[key]
            for key in ("artifact_hash", "schema_id", "codec", "size_bytes")
        }
        | {"uri": "file:///persisted/projection.json"},
    }
    message = seal_content_hash(
        {
            "schema_id": "openpine.worker.protocol.v2",
            "schema_version": "2.3.0",
            "producer": "backtest_engine",
            "producer_version": first_commit["broker_projection"]["producer_version"],
            "producer_commit": first_commit["broker_projection"]["producer_commit"],
            "stack_id": context["stack_manifest_hash"],
            "created_at_utc_ms": first_commit["bar_open_time_utc_ms"],
            "serializer_id": "openpine.canonical.json.v1",
            "content_hash_alg": "sha256",
            "message_id": "commit-0",
            "sender_role": "engine",
            "session_id": context["session_id"],
            "run_id": context["run_id"],
            "sequence": 0,
            "correlation_id": "correlation-rc4",
            "causation_id": None,
            "kind": "BAR_COMMIT",
            "body": body,
        },
        schema_id="openpine.worker.protocol.v2",
    )
    validate_payload("openpine.worker.protocol.v2", message)
    assert verify_content_hash(message, schema_id="openpine.worker.protocol.v2")


def test_bar_commit_state_bytes_and_hash_are_deterministic() -> None:
    first, _, _ = _run_protocol()
    second, _, _ = _run_protocol()
    first_commits = [event for event in first if event["kind"] == "BAR_COMMIT"]
    second_commits = [event for event in second if event["kind"] == "BAR_COMMIT"]

    assert [event["state_artifact"]["artifact_hash"] for event in first_commits] == [
        event["state_artifact"]["artifact_hash"] for event in second_commits
    ]
    assert [event["state_artifact"]["bytes"] for event in first_commits] == [
        event["state_artifact"]["bytes"] for event in second_commits
    ]


def test_bar_commit_fails_closed_without_strategy_state_export() -> None:
    context = _execution_context()
    envelopes = _bar_envelopes()
    events: list[dict[str, Any]] = []

    with pytest.raises(ValueError, match="strategy.*export_state"):
        BacktestEngine(_config()).run(
            StatelessStrategy,
            bars=_engine_bars(envelopes),
            callbacks=BacktestCallbacks(on_protocol_callback=events.append),
            execution_context=context,
            bar_envelopes=envelopes,
        )

    assert [event["kind"] for event in events] == ["BAR_BEGIN"]


def test_protocol_boundary_negative_branches_are_fail_closed() -> None:
    assert _semver("5.0.0-rc.5") == "5.0.0-rc.5"
    with pytest.raises(ValueError, match="execution identity"):
        _engine_identity({})

    context = _execution_context()
    tampered = dict(context)
    tampered["run_id"] = "foreign"
    with pytest.raises(ValueError, match="content hash"):
        BacktestEngine(_config()).run(
            StatefulEnter,
            bars=_engine_bars(_bar_envelopes()),
            callbacks=BacktestCallbacks(on_protocol_callback=lambda event: None),
            execution_context=tampered,
            bar_envelopes=_bar_envelopes(),
        )

    with pytest.raises(ValueError, match="length"):
        BacktestEngine(_config()).run(
            StatefulEnter,
            bars=_engine_bars(_bar_envelopes()),
            callbacks=BacktestCallbacks(on_protocol_callback=lambda event: None),
            execution_context=context,
            bar_envelopes=_bar_envelopes()[:1],
        )

    wrong_identity = _bar_envelopes()
    wrong_identity[0]["producer_commit"] = "3" * 40
    wrong_identity[0] = seal_content_hash(
        wrong_identity[0], schema_id="openpine.marketdata.bar.v2"
    )
    with pytest.raises(ValueError, match="identity mismatch"):
        BacktestEngine(_config()).run(
            StatefulEnter,
            bars=_engine_bars(_bar_envelopes()),
            callbacks=BacktestCallbacks(on_protocol_callback=lambda event: None),
            execution_context=context,
            bar_envelopes=wrong_identity,
        )


def test_protocol_projection_and_no_callback_edge_branches() -> None:
    empty = SimpleNamespace(open_trades=[], closed_trades=[])
    assert _trade_for_fill(empty, "missing") is None
    with pytest.raises(ValueError, match="missing exit identity"):
        _closed_trade_rows(
            SimpleNamespace(
                closed_trades=[
                    SimpleNamespace(
                        exit_price=None, exit_bar_index=None, exit_time=None
                    )
                ]
            )
        )
    closed = SimpleNamespace(
        id="trade-1",
        entry_id="L",
        direction="long",
        qty=1.0,
        entry_price=100.0,
        entry_bar_index=0,
        entry_time=0,
        exit_price=101.0,
        exit_bar_index=1,
        exit_time=60_000,
        profit=1.0,
        commission_entry=0.1,
        commission_exit=0.1,
    )
    assert (
        _closed_trade_rows(SimpleNamespace(closed_trades=[closed]))[0]["realized_pnl"]
        == "1"
    )

    engine = BacktestEngine(_config())
    engine.position.direction = "sideways"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="position direction"):
        sealed_broker_projection(
            engine,
            _execution_context(),
            bar=_engine_bars(_bar_envelopes())[0],
            bar_index=0,
            recalc_iteration=0,
        )

    no_callback = SimpleNamespace(callbacks=BacktestCallbacks())
    prepare_protocol_run(no_callback, None, None, None)
    emit_protocol_bar_begin(no_callback, _engine_bars(_bar_envelopes())[0], 0)
    emit_protocol_bar_commit(
        no_callback, StatefulEnter, _engine_bars(_bar_envelopes())[0], 0
    )

    missing = SimpleNamespace(
        callbacks=BacktestCallbacks(on_protocol_callback=lambda event: None),
        _protocol_execution_context=None,
        _protocol_bar_envelopes=None,
    )
    with pytest.raises(ValueError, match="not initialized"):
        emit_protocol_bar_begin(missing, _engine_bars(_bar_envelopes())[0], 0)
    with pytest.raises(ValueError, match="not initialized"):
        emit_protocol_bar_commit(
            missing, StatefulEnter, _engine_bars(_bar_envelopes())[0], 0
        )


def test_protocol_uses_sealed_close_time_when_compatibility_bar_omits_it() -> None:
    context = _execution_context()
    envelopes = _bar_envelopes()
    bars = _engine_bars(envelopes)
    bars[0] = Bar(
        time=bars[0].time,
        open=bars[0].open,
        high=bars[0].high,
        low=bars[0].low,
        close=bars[0].close,
        volume=bars[0].volume,
        time_close=None,
        finality=bars[0].finality,
    )
    events: list[dict[str, Any]] = []
    BacktestEngine(_config()).run(
        StatefulEnter,
        bars=bars,
        callbacks=BacktestCallbacks(on_protocol_callback=events.append),
        execution_context=context,
        bar_envelopes=envelopes,
    )
    assert events[0]["bar"] is envelopes[0]


def test_calc_on_order_fills_emits_sealed_recalc_projection_and_broker_events() -> None:
    context = _execution_context()
    envelopes = _bar_envelopes()
    events: list[dict[str, Any]] = []
    config = _config()
    config.calc_on_order_fills = True
    BacktestEngine(config).run(
        StatefulEnter,
        bars=_engine_bars(envelopes),
        callbacks=BacktestCallbacks(on_protocol_callback=events.append),
        execution_context=context,
        bar_envelopes=envelopes,
    )

    recalc = next(event for event in events if event["kind"] == "RECALC_REQUEST")
    assert recalc["recalc_iteration"] == 1
    assert recalc["broker_events"]
    assert recalc["broker_event_batch_hash"].startswith("sha256:")
    projection = recalc["broker_projection"]
    validate_payload("openpine.broker_projection.v1", projection)
    assert verify_content_hash(projection, schema_id="openpine.broker_projection.v1")
    assert recalc["broker_projection_hash"] == projection["content_hash"]


class EnterCloseEveryOther:
    required_runtime_capabilities: tuple[str, ...] = ()

    def __init__(self, params: dict[str, Any], runtime: object, ctx: object) -> None:
        del params, runtime
        self.ctx = ctx

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        del bar
        if bar_index % 2 == 0:
            self.ctx.entry("L", "long", qty=1)
        else:
            self.ctx.close("L")

    def export_state(self) -> dict[str, str]:
        return {"kind": "flip"}


def test_broker_projection_emits_closed_trade_deltas_not_full_history() -> None:
    context = _execution_context()
    envelopes = [
        make_canonical_bar(
            instrument_id="binance:spot:BTCUSDT",
            timeframe="1m",
            open_time_utc_ms=index * 60_000,
            open=str(100 + index),
            high=str(102 + index),
            low=str(99 + index),
            close=str(101 + index),
            volume=str(10 + index),
            snapshot_id="snapshot-rc4",
            provider="binance",
            provider_revision={"known": True, "revision": "binance-rc4"},
            producer_commit=MARKETDATA_COMMIT,
            stack_id=STACK_ID,
            finality="FINAL",
            created_at_utc_ms=0,
        )
        for index in range(4)
    ]
    events: list[dict[str, Any]] = []
    config = _config()
    config.end_time = 180_000
    BacktestEngine(config).run(
        EnterCloseEveryOther,
        bars=_engine_bars(envelopes),
        callbacks=BacktestCallbacks(on_protocol_callback=events.append),
        execution_context=context,
        bar_envelopes=envelopes,
    )
    closed_batches = [
        list(event["broker_projection"]["closed_trades"])
        for event in events
        if "broker_projection" in event
    ]
    emitted = sum(len(batch) for batch in closed_batches)
    assert emitted == 2
    assert max(len(batch) for batch in closed_batches) == 1
