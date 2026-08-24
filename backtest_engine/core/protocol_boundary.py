"""Producer-owned sealed artifacts for the interactive worker boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from openpine_contracts import (
    aggregate_batch_hash,
    canonical_dumps,
    decimal_string,
    seal_content_hash,
    validate_payload,
    verify_content_hash,
)

from backtest_engine.models import Bar


def _decimal(value: object) -> str:
    return decimal_string(Decimal(str(value)))


def _semver(value: object) -> str:
    text = str(value)
    if "rc" in text and "-rc." not in text:
        base, marker, rc = text.partition("rc")
        if marker and base and rc.isdigit():
            return f"{base}-rc.{rc}"
    return text


def _engine_identity(context: Mapping[str, Any]) -> tuple[str, str]:
    commits = context.get("producer_commits")
    wheels = context.get("wheel_identities")
    commit = commits.get("backtest_engine") if isinstance(commits, Mapping) else None
    version = None
    if isinstance(wheels, list):
        for row in wheels:
            if isinstance(row, Mapping) and row.get("name") == "backtest_engine":
                version = _semver(row.get("version"))
                break
    if not isinstance(commit, str) or len(commit) != 40 or not version:
        raise ValueError("backtest_engine execution identity is missing")
    return version, commit


def validate_protocol_inputs(
    *,
    execution_context: Mapping[str, Any] | None,
    bars: Sequence[Bar],
    bar_envelopes: Sequence[Mapping[str, Any]] | None,
) -> tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]:
    if execution_context is None:
        raise ValueError("execution_context is required for protocol callback")
    if bar_envelopes is None:
        raise ValueError("bar_envelopes are required for protocol callback")
    validate_payload("openpine.execution_context.v1", execution_context)
    if not verify_content_hash(
        execution_context, schema_id="openpine.execution_context.v1"
    ):
        raise ValueError("execution_context content hash is invalid")
    if len(bar_envelopes) != len(bars):
        raise ValueError("bar_envelopes length does not match engine bars")
    commits = execution_context.get("producer_commits")
    expected_marketdata_commit = (
        commits.get("marketdata-provider") if isinstance(commits, Mapping) else None
    )
    for index, (bar, envelope) in enumerate(zip(bars, bar_envelopes, strict=True)):
        validate_payload("openpine.marketdata.bar.v2", envelope)
        if not verify_content_hash(envelope, schema_id="openpine.marketdata.bar.v2"):
            raise ValueError(f"bar envelope {index} content hash is invalid")
        if (
            envelope.get("stack_id") != execution_context.get("stack_manifest_hash")
            or envelope.get("producer") != "marketdata-provider"
            or envelope.get("producer_commit") != expected_marketdata_commit
            or envelope.get("series_id") != execution_context.get("series_id")
            or envelope.get("instrument_id") != execution_context.get("instrument_id")
            or envelope.get("timeframe") != execution_context.get("timeframe")
        ):
            raise ValueError(f"bar envelope {index} identity mismatch")
        values_match = (
            int(envelope["open_time_utc_ms"]) == int(bar.time)
            and (
                bar.time_close is None
                or int(envelope["close_time_utc_ms"]) == int(bar.time_close)
            )
            and Decimal(str(envelope["open"])) == Decimal(str(bar.open))
            and Decimal(str(envelope["high"])) == Decimal(str(bar.high))
            and Decimal(str(envelope["low"])) == Decimal(str(bar.low))
            and Decimal(str(envelope["close"])) == Decimal(str(bar.close))
            and Decimal(str(envelope["volume"]))
            == Decimal(str(0 if bar.volume is None else bar.volume))
        )
        if not values_match:
            raise ValueError(f"bar envelope {index} does not match engine bar")
    return execution_context, bar_envelopes


def _order_rows(engine: Any, bar_index: int) -> list[dict[str, Any]]:
    status = {
        "pending": "PENDING",
        "active": "OPEN",
        "filled": "FILLED",
        "cancelled": "CANCELED",
        "expired": "EXPIRED",
        "rejected": "REJECTED",
    }
    rows = []
    for order in engine.orders:
        filled = sum(fill.qty for fill in engine.fills if fill.order_id == order.id)
        rows.append(
            {
                "order_id": str(order.id),
                "entry_name": (
                    None if order.from_entry is None else str(order.from_entry)
                ),
                "state": status[order.status],
                "direction": str(order.direction).upper(),
                "order_type": str(order.order_type).upper(),
                "qty": _decimal(order.qty),
                "filled_qty": _decimal(filled),
                "limit_price": (
                    None if order.limit_price is None else _decimal(order.limit_price)
                ),
                "stop_price": (
                    None if order.stop_price is None else _decimal(order.stop_price)
                ),
                "created_bar_index": int(order.created_bar_index),
                "updated_bar_index": max(int(order.created_bar_index), int(bar_index)),
            }
        )
    return rows


def _trade_for_fill(engine: Any, order_id: str) -> str | None:
    for trade in [*engine.open_trades, *engine.closed_trades]:
        if trade.entry_id == order_id or trade.exit_id == order_id:
            return str(trade.id)
    return None


def _fill_rows(engine: Any) -> list[dict[str, Any]]:
    rows = []
    for index, fill in enumerate(engine.fills):
        rows.append(
            {
                "fill_id": f"{fill.order_id}:{fill.bar_index}:{index}",
                "order_id": str(fill.order_id),
                "trade_id": _trade_for_fill(engine, str(fill.order_id)),
                "direction": str(fill.direction).upper(),
                "qty": _decimal(fill.qty),
                "price": _decimal(fill.price),
                "commission": _decimal(fill.commission),
                "bar_index": int(fill.bar_index),
                "occurred_at_utc_ms": int(fill.time),
            }
        )
    return rows


def _open_trade_rows(engine: Any) -> list[dict[str, Any]]:
    return [
        {
            "trade_id": str(trade.id),
            "entry_name": str(trade.entry_id),
            "direction": str(trade.direction).upper(),
            "qty": _decimal(trade.qty),
            "entry_price": _decimal(trade.entry_price),
            "entry_bar_index": int(trade.entry_bar_index),
            "entry_time_utc_ms": int(trade.entry_time),
            "unrealized_pnl": _decimal(trade.profit),
        }
        for trade in engine.open_trades
    ]


def _closed_trade_rows(engine: Any) -> list[dict[str, Any]]:
    rows = []
    for trade in engine.closed_trades:
        if (
            trade.exit_price is None
            or trade.exit_bar_index is None
            or trade.exit_time is None
        ):
            raise ValueError("closed trade is missing exit identity")
        rows.append(
            {
                "trade_id": str(trade.id),
                "entry_name": str(trade.entry_id),
                "direction": str(trade.direction).upper(),
                "qty": _decimal(trade.qty),
                "entry_price": _decimal(trade.entry_price),
                "entry_bar_index": int(trade.entry_bar_index),
                "entry_time_utc_ms": int(trade.entry_time),
                "exit_price": _decimal(trade.exit_price),
                "exit_bar_index": int(trade.exit_bar_index),
                "exit_time_utc_ms": int(trade.exit_time),
                "realized_pnl": _decimal(trade.profit),
                "commission": _decimal(trade.commission_entry + trade.commission_exit),
            }
        )
    return rows


def sealed_broker_projection(
    engine: Any,
    execution_context: Mapping[str, Any],
    *,
    bar: Bar,
    bar_index: int,
    recalc_iteration: int,
) -> dict[str, Any]:
    version, commit = _engine_identity(execution_context)
    direction = str(engine.position.direction).upper()
    if direction not in {"FLAT", "LONG", "SHORT"}:
        raise ValueError("broker position direction is invalid")
    commissions = sum(float(fill.commission) for fill in engine.fills)
    payload = {
        "schema_id": "openpine.broker_projection.v1",
        "schema_version": "1.0.0",
        "producer": "backtest_engine",
        "producer_version": version,
        "producer_commit": commit,
        "stack_id": execution_context["stack_manifest_hash"],
        "created_at_utc_ms": int(bar.time),
        "serializer_id": "openpine.canonical.json.v1",
        "content_hash_alg": "sha256",
        "run_id": execution_context["run_id"],
        "series_id": execution_context["series_id"],
        "instrument_id": execution_context["instrument_id"],
        "bar_index": int(bar_index),
        "bar_open_time_utc_ms": int(bar.time),
        "recalc_iteration": int(recalc_iteration),
        "position": {
            "direction": direction,
            "qty": _decimal(abs(engine.position.size)),
            "avg_price": (
                None if direction == "FLAT" else _decimal(engine.position.avg_price)
            ),
            "entry_name": (
                str(engine.open_trades[0].entry_id) if engine.open_trades else None
            ),
        },
        "orders": _order_rows(engine, bar_index),
        "fills": _fill_rows(engine),
        "open_trades": _open_trade_rows(engine),
        "closed_trades": _closed_trade_rows(engine),
        "realized_pnl": _decimal(engine.state.net_profit),
        "unrealized_pnl": _decimal(engine.state.open_profit),
        "gross_profit": _decimal(engine.state.gross_profit),
        "gross_loss": _decimal(engine.state.gross_loss),
        "commission": _decimal(commissions),
        "winning_trades": int(engine.state.win_trades),
        "losing_trades": int(engine.state.loss_trades),
        "even_trades": int(engine.state.even_trades),
        "cash": _decimal(engine.cash),
        "equity": _decimal(engine.equity),
        "currency": str(engine.config.currency),
    }
    sealed = seal_content_hash(payload, schema_id="openpine.broker_projection.v1")
    validate_payload("openpine.broker_projection.v1", sealed)
    return sealed


def sealed_state_artifact(
    engine: Any,
    strategy: Any,
    execution_context: Mapping[str, Any],
    *,
    bar: Bar,
    bar_index: int,
    recalc_iteration: int,
) -> dict[str, Any]:
    export = getattr(strategy, "export_state", None)
    if not callable(export):
        raise ValueError("strategy must provide export_state for protocol callback")
    version, commit = _engine_identity(execution_context)
    payload = seal_content_hash(
        {
            "schema_id": "openpine.runtime.state.v1",
            "schema_version": "1.0.0",
            "producer": "backtest_engine",
            "producer_version": version,
            "producer_commit": commit,
            "stack_id": execution_context["stack_manifest_hash"],
            "created_at_utc_ms": int(bar.time),
            "serializer_id": "openpine.canonical.json.v1",
            "content_hash_alg": "sha256",
            "run_id": execution_context["run_id"],
            "strategy_id": execution_context["strategy_id"],
            "series_id": execution_context["series_id"],
            "instrument_id": execution_context["instrument_id"],
            "timeframe": execution_context["timeframe"],
            "bar_index": int(bar_index),
            "bar_open_time_utc_ms": int(bar.time),
            "phase": str(engine._current_phase or "score"),
            "recalc_iteration": int(recalc_iteration),
            "strategy_state": export(),
        },
        schema_id="openpine.runtime.state.v1",
    )
    encoded = canonical_dumps(payload).encode("utf-8")
    return {
        "artifact_hash": payload["content_hash"],
        "schema_id": "openpine.runtime.state.v1",
        "codec": "json",
        "size_bytes": len(encoded),
        "bytes": encoded,
    }


def sealed_projection_artifact(projection: Mapping[str, Any]) -> dict[str, Any]:
    encoded = canonical_dumps(projection).encode("utf-8")
    return {
        "artifact_hash": projection["content_hash"],
        "schema_id": "openpine.broker_projection.v1",
        "codec": "json",
        "size_bytes": len(encoded),
        "bytes": encoded,
    }


def prepare_protocol_run(
    engine: Any,
    execution_context: Mapping[str, Any] | None,
    bar_envelopes: Sequence[Mapping[str, Any]] | None,
    series: Any,
) -> None:
    if engine.callbacks.on_protocol_callback is None:
        return
    context, envelopes = validate_protocol_inputs(
        execution_context=execution_context,
        bars=[series.get_bar(index) for index in range(len(series))],
        bar_envelopes=bar_envelopes,
    )
    engine._protocol_execution_context = dict(context)
    engine._protocol_bar_envelopes = list(envelopes)
    engine._protocol_fill_cursor = 0


def _broker_events_since_callback(
    engine: Any, context: Mapping[str, Any], bar: Bar
) -> list[dict[str, Any]]:
    version, commit = _engine_identity(context)
    start = int(getattr(engine, "_protocol_fill_cursor", 0))
    events: list[dict[str, Any]] = []
    for fill in engine.fills[start:]:
        payload = seal_content_hash(
            {
                "schema_id": "openpine.broker.v2",
                "schema_version": "2.2.0",
                "producer": "backtest_engine",
                "producer_version": version,
                "producer_commit": commit,
                "stack_id": context["stack_manifest_hash"],
                "created_at_utc_ms": int(bar.time),
                "serializer_id": "openpine.canonical.json.v1",
                "content_hash_alg": "sha256",
                "kind": "event",
                "body": {
                    "event_kind": "fill",
                    "order_id": str(fill.order_id),
                    "qty": _decimal(fill.qty),
                    "price": _decimal(fill.price),
                },
            },
            schema_id="openpine.broker.v2",
        )
        validate_payload("openpine.broker.v2", payload)
        events.append(payload)
    engine._protocol_fill_cursor = len(engine.fills)
    return events


def emit_protocol_bar_begin(engine: Any, bar: Bar, bar_index: int) -> None:
    if engine.callbacks.on_protocol_callback is None:
        return
    context = engine._protocol_execution_context
    envelopes = engine._protocol_bar_envelopes
    if context is None or envelopes is None:
        raise ValueError("protocol callback identity is not initialized")
    if engine._strategy_callback_recalc_iteration > 0:
        broker_events = _broker_events_since_callback(engine, context, bar)
        projection = sealed_broker_projection(
            engine,
            context,
            bar=bar,
            bar_index=bar_index,
            recalc_iteration=engine._strategy_callback_recalc_iteration,
        )
        engine._cb(
            "on_protocol_callback",
            {
                "kind": "RECALC_REQUEST",
                "run_id": context["run_id"],
                "bar_index": bar_index,
                "bar_open_time_utc_ms": int(bar.time),
                "recalc_iteration": engine._strategy_callback_recalc_iteration,
                "broker_events": broker_events,
                "broker_event_batch_hash": aggregate_batch_hash(
                    broker_events,
                    batch_kind="BROKER_EVENT_BATCH",
                    item_schema_id="openpine.broker.v2",
                ),
                "broker_projection": projection,
                "broker_projection_hash": projection["content_hash"],
            },
        )
        return
    envelope = envelopes[bar_index]
    projection = sealed_broker_projection(
        engine,
        context,
        bar=bar,
        bar_index=bar_index,
        recalc_iteration=engine._strategy_callback_recalc_iteration,
    )
    engine._cb(
        "on_protocol_callback",
        {
            "kind": "BAR_BEGIN",
            "run_id": context["run_id"],
            "strategy_id": context["strategy_id"],
            "series_id": context["series_id"],
            "instrument_id": context["instrument_id"],
            "timeframe": context["timeframe"],
            "bar_index": bar_index,
            "bar_open_time_utc_ms": int(bar.time),
            "phase": str(engine._current_phase or "score"),
            "recalc_iteration": engine._strategy_callback_recalc_iteration,
            "bar_hash": envelope["bar_content_hash"],
            "bar": envelope,
            "broker_projection": projection,
        },
    )


def emit_protocol_bar_commit(
    engine: Any, strategy: Any, bar: Bar, bar_index: int
) -> None:
    if engine.callbacks.on_protocol_callback is None:
        return
    context = engine._protocol_execution_context
    if context is None:
        raise ValueError("protocol callback identity is not initialized")
    projection = sealed_broker_projection(
        engine,
        context,
        bar=bar,
        bar_index=bar_index,
        recalc_iteration=engine._strategy_callback_recalc_iteration,
    )
    state_artifact = sealed_state_artifact(
        engine,
        strategy,
        context,
        bar=bar,
        bar_index=bar_index,
        recalc_iteration=engine._strategy_callback_recalc_iteration,
    )
    projection_artifact = sealed_projection_artifact(projection)
    engine._cb(
        "on_protocol_callback",
        {
            "kind": "BAR_COMMIT",
            "run_id": context["run_id"],
            "strategy_id": context["strategy_id"],
            "series_id": context["series_id"],
            "instrument_id": context["instrument_id"],
            "timeframe": context["timeframe"],
            "bar_index": bar_index,
            "bar_open_time_utc_ms": int(bar.time),
            "phase": str(engine._current_phase or "score"),
            "recalc_iteration": engine._strategy_callback_recalc_iteration,
            "state_hash": state_artifact["artifact_hash"],
            "broker_projection_hash": projection["content_hash"],
            "state_artifact": state_artifact,
            "broker_projection_artifact": projection_artifact,
            "broker_projection": projection,
        },
    )
