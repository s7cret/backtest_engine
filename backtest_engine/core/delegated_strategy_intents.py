"""Backtest-engine-owned conversion of delegated Pine strategy calls."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from openpine_contracts import (
    content_hash,
    decimal_string,
    seal_content_hash,
    validate_payload,
    verify_content_hash,
)
from pinelib import DelegatedCapabilityDispatcher, DelegatedInvocation, is_na

from backtest_engine.config import BacktestConfig
from backtest_engine.core.order_metadata import EXIT_METADATA_FIELDS
from backtest_engine.core.intent_replay import IntentReplayIdentity
from backtest_engine.core.strategy_capabilities import (
    STRATEGY_COMMANDS,
    STRATEGY_CONSTANTS,
    STRATEGY_STATE_VALUES,
    validate_exit_shape,
)

OWNER = "backtest-engine"
SCHEMA_ID = "openpine.intent.v2"
CAPABILITY_ID = "strategy.orders.v1"
DELEGATION_SCHEMA_ID = "openpine.backtest.engine.v1"
ENTRY_CAPABILITY_ID = "strategy.entry"
CLOSE_CAPABILITY_ID = "strategy.close"
DRAFT_SCHEMA_ID = "backtest_engine.strategy_intent_draft.v1"
PRODUCER = "backtest_engine"
PRODUCER_VERSION = "5.0.0-rc.6"


_DELEGATED_STRATEGY_VALUES = STRATEGY_CONSTANTS
_DELEGATED_STRATEGY_STATE_VALUES = STRATEGY_STATE_VALUES


def build_delegated_strategy_dispatcher(
    handler: "DelegatedStrategyIntentHandler",
    *,
    strategy_values: Mapping[str, object] | None = None,
) -> DelegatedCapabilityDispatcher:
    if not isinstance(handler, DelegatedStrategyIntentHandler):
        raise TypeError("handler must be DelegatedStrategyIntentHandler")
    supplied_values = dict(strategy_values or {})
    unknown_values = set(supplied_values).difference(_DELEGATED_STRATEGY_STATE_VALUES)
    if unknown_values:
        raise ValueError("delegated strategy value target is unsupported")
    values = {
        (OWNER, DELEGATION_SCHEMA_ID, capability_id): capability_id
        for capability_id in _DELEGATED_STRATEGY_VALUES
    }
    values.update(
        {
            (OWNER, DELEGATION_SCHEMA_ID, capability_id): value
            for capability_id, value in supplied_values.items()
        }
    )
    return DelegatedCapabilityDispatcher(
        {(OWNER, DELEGATION_SCHEMA_ID, name): handler for name in STRATEGY_COMMANDS},
        values=values,
    )


def _nonempty_string(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"strategy intent {field} must be a nonempty string")
    return value


def _optional(value: object) -> object | None:
    return None if value is None or is_na(value) else value


def _decimal(value: object, field: str) -> str:
    value = _optional(value)
    if value is None or isinstance(value, bool):
        raise ValueError(f"strategy intent {field} must be numeric")
    try:
        if isinstance(value, float):
            number = Decimal(repr(value))
        else:
            number = Decimal(str(value))
        converted = decimal_string(number)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"strategy intent {field} must be a finite decimal") from error
    return converted


def _optional_string(value: object, field: str) -> str | None:
    value = _optional(value)
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError(f"strategy intent {field} must be a string or na")
    return value


def _oca_type(value: object) -> str | None:
    value = _optional(value)
    if value is None:
        return None
    if type(value) is not str or value.removeprefix("strategy.oca.") not in {
        "cancel",
        "reduce",
        "none",
    }:
        raise ValueError("strategy intent oca_type must be cancel, reduce, none, or na")
    return value.removeprefix("strategy.oca.")


def _direction(value: object) -> str:
    value = _optional(value)
    if type(value) is not str:
        raise ValueError("strategy intent direction must be LONG or SHORT")
    normalized = value.removeprefix("strategy.").strip().upper()
    if normalized not in {"LONG", "SHORT"}:
        raise ValueError("strategy intent direction must be LONG or SHORT")
    return normalized


def _source_span(invocation: DelegatedInvocation) -> dict[str, object]:
    span = invocation.source_span
    return {
        "known": True,
        "source_hash": span.source_hash,
        # Runtime SourceSpan has line/column identity but no source offsets. Zero
        # is the only non-invented contract offset and the invocation id retains
        # the complete host source identity, including file_id.
        "start_offset": 0,
        "end_offset": 0,
        "start_line": span.start_line,
        "start_col": span.start_column,
        "end_line": span.end_line,
        "end_col": span.end_column,
    }


class DelegatedStrategyIntentHandler:
    """Prepare delegated strategy calls and seal only committed Intent v2 events."""

    def __init__(
        self,
        *,
        identity: IntentReplayIdentity,
        producer_commit: str,
        bar_open_time_utc_ms: Mapping[int, int],
        config: BacktestConfig,
        bar_close: Mapping[int, object] | None = None,
        bar_equity: Mapping[int, object] | None = None,
        recalc_iteration: int | None = None,
        pine_version: int = 6,
        open_entry_ids: Iterable[str] | None = None,
    ) -> None:
        if recalc_iteration is not None and (
            type(recalc_iteration) is not int or recalc_iteration < 0
        ):
            raise ValueError("recalc iteration must be a nonnegative integer")
        self.recalc_iteration = recalc_iteration
        if type(pine_version) is not int or not 1 <= pine_version <= 6:
            raise ValueError("strategy host requires an exact Pine version 1..6")
        self.pine_version = pine_version
        self.open_entry_ids = None if open_entry_ids is None else frozenset(open_entry_ids)
        if not isinstance(identity, IntentReplayIdentity):
            raise TypeError("identity must be IntentReplayIdentity")
        if (
            type(producer_commit) is not str
            or len(producer_commit) != 40
            or any(char not in "0123456789abcdef" for char in producer_commit)
        ):
            raise ValueError("producer_commit must be a lowercase git sha")
        copied_times = dict(bar_open_time_utc_ms)
        if any(
            type(index) is not int or index < 0 or type(value) is not int or value < 0
            for index, value in copied_times.items()
        ):
            raise ValueError("bar_open_time_utc_ms must map nonnegative integers")
        if not isinstance(config, BacktestConfig):
            raise TypeError("config must be BacktestConfig")
        if config.default_qty_type not in {"fixed", "cash", "percent_of_equity"}:
            raise ValueError("config default_qty_type is unsupported")
        copied_close = {
            index: _decimal(value, "bar_close") for index, value in dict(bar_close or {}).items()
        }
        copied_equity = {
            index: _decimal(value, "bar_equity") for index, value in dict(bar_equity or {}).items()
        }
        if any(type(index) is not int or index < 0 for index in copied_close | copied_equity):
            raise ValueError("bar_close and bar_equity must map nonnegative integers")
        self.identity = identity
        self.producer_commit = producer_commit
        self.bar_open_time_utc_ms = MappingProxyType(copied_times)
        self.default_qty_type = config.default_qty_type
        self.default_qty_value = _decimal(config.default_qty_value, "default_qty_value")
        self.commission_type = config.commission_type
        self.commission_value = _decimal(config.commission_value, "commission_value")
        self.bar_close = MappingProxyType(copied_close)
        self.bar_equity = MappingProxyType(copied_equity)

    def _default_qty(self, bar_index: int) -> str:
        value = Decimal(self.default_qty_value)
        if self.default_qty_type == "fixed":
            return self.default_qty_value
        close_text = self.bar_close.get(bar_index)
        if close_text is None or Decimal(close_text) <= 0:
            raise ValueError("delegated strategy default quantity requires positive bar_close")
        denominator = Decimal(close_text)
        if self.default_qty_type == "cash":
            return decimal_string(value / denominator)
        equity_text = self.bar_equity.get(bar_index)
        if equity_text is None:
            raise ValueError("delegated percent_of_equity quantity requires bar_equity")
        if self.commission_type == "percent":
            denominator *= Decimal(1) + Decimal(self.commission_value) / Decimal(100)
        return decimal_string(Decimal(equity_text) * value / Decimal(100) / denominator)

    def __call__(self, invocation: DelegatedInvocation) -> Mapping[str, object]:
        spec = STRATEGY_COMMANDS.get(invocation.capability_id)
        if (
            spec is None
            or invocation.owner != OWNER
            or invocation.schema_id != DELEGATION_SCHEMA_ID
            or (invocation.symbol_id, invocation.overload_id) != (spec.symbol_id, spec.overload_id)
        ):
            raise ValueError(
                "unsupported delegated strategy symbol or overload; capability, symbol, or overload binding mismatch"
            )
        if not isinstance(invocation.arguments, Mapping) or set(invocation.arguments) != {
            "positional",
            "named",
        }:
            raise ValueError("delegated strategy arguments must contain positional and named")
        arguments = spec.bind(
            invocation.arguments["positional"], invocation.arguments["named"], self.pine_version
        )
        when = arguments.pop("when", True)
        if self.pine_version < 6 and _optional(when) is None:
            when = False
        if self.pine_version < 6 and type(when) in (int, float):
            when = bool(when)
        if type(when) is not bool:
            raise ValueError("strategy intent when must be a bool or historical numeric/na")
        if not when:
            return MappingProxyType({"draft_schema_id": DRAFT_SCHEMA_ID, "payload": None})
        kind = (
            "risk"
            if spec.name.startswith("strategy.risk.")
            else spec.name.removeprefix("strategy.")
        )
        if kind == "exit":
            validate_exit_shape(
                {key for key, value in arguments.items() if _optional(value) is not None}
            )
        bar_time = self.bar_open_time_utc_ms.get(invocation.bar_index)
        if bar_time is None:
            raise ValueError("delegated strategy bar identity is unavailable")
        command_id = _nonempty_string(arguments["id"], "id") if "id" in arguments else kind
        if kind == "close":
            command_id = "close:" + command_id
        if kind == "exit":
            entry_id = (
                None
                if arguments.get("from_entry", "") == ""
                else _nonempty_string(arguments["from_entry"], "from_entry")
            )
            # Entry/exit commands in one callback are committed together. The
            # broker owns binding to open trades or already-created market orders;
            # a pre-callback position snapshot cannot decide whether an exit is valid.
            if not any(
                _optional(arguments.get(key)) is not None
                for key in ("limit", "stop", "profit", "loss", "trail_price", "trail_points")
            ):
                raise ValueError("strategy.exit requires a supported active price leg")
        payload: dict[str, Any] = {
            "schema_id": SCHEMA_ID,
            "schema_version": "2.2.0",
            "producer": PRODUCER,
            "producer_version": PRODUCER_VERSION,
            "producer_commit": self.producer_commit,
            "stack_id": self.identity.stack_id,
            "created_at_utc_ms": bar_time,
            "serializer_id": "openpine.canonical.json.v1",
            "content_hash_alg": "sha256",
            "command_id": command_id,
            "kind": kind,
            "run_id": self.identity.run_id,
            "strategy_id": self.identity.strategy_id,
            "series_id": self.identity.series_id,
            "instrument_id": self.identity.instrument_id,
            "timeframe": self.identity.timeframe,
            "bar_index": invocation.bar_index,
            "bar_open_time_utc_ms": bar_time,
            "phase": invocation.phase,
            "recalc_iteration": invocation.tick_index
            if self.recalc_iteration is None
            else self.recalc_iteration,
            "semantic_profile": self.identity.semantic_profile,
            "source_span": MappingProxyType(_source_span(invocation)),
            "idempotency_key": f"intent-delivery:{invocation.invocation_id}",
        }
        if kind == "risk":
            rule = spec.name.removeprefix("strategy.risk.")
            if rule == "allow_entry_in":
                direction = arguments["value"]
                if type(direction) is not str or direction not in {
                    "strategy.direction.long",
                    "strategy.direction.short",
                    "strategy.direction.all",
                }:
                    raise ValueError("risk direction requires a strategy.direction constant")
                value, unit = "0", direction.removeprefix("strategy.direction.")
            else:
                from backtest_engine.core.risk_rules import validate_position_limit

                validate_position_limit(arguments["contracts"])
                value, unit = _decimal(arguments["contracts"], "contracts"), "fixed"
                number = Decimal(value)
                validate_position_limit(float(number))
                if number != 0 and float(number) == 0:
                    raise ValueError("risk limit is outside the runtime range")
            payload.update(
                command_id=spec.name,
                risk_rule=rule,
                risk_value=value,
                risk_unit=unit,
                risk_scope="strategy",
            )
        elif kind in {"entry", "order"}:
            payload.update(
                {
                    "order_id": command_id,
                    "direction": _direction(arguments["direction"]),
                    "qty": (
                        self._default_qty(invocation.bar_index)
                        if _optional(arguments.get("qty")) is None
                        else _decimal(arguments["qty"], "qty")
                    ),
                    "stop": (
                        None
                        if _optional(arguments.get("stop")) is None
                        else _decimal(arguments["stop"], "stop")
                    ),
                    "limit": (
                        None
                        if _optional(arguments.get("limit")) is None
                        else _decimal(arguments["limit"], "limit")
                    ),
                    "oca_name": _optional_string(arguments.get("oca_name"), "oca_name"),
                    "oca_type": _oca_type(arguments.get("oca_type")),
                    "comment": _optional_string(arguments.get("comment"), "comment"),
                }
            )
        elif kind == "exit":
            payload.update(
                order_id=command_id,
                comment=_optional_string(arguments.get("comment"), "comment"),
                oca_name=_optional_string(arguments.get("oca_name"), "oca_name"),
            )
            if entry_id is None:
                payload.update(schema_version="2.3.0", exit_scope="all_entries")
            else:
                payload["from_entry"] = entry_id
            for field in (
                "qty",
                "qty_percent",
                "profit",
                "limit",
                "loss",
                "stop",
                "trail_price",
                "trail_points",
                "trail_offset",
            ):
                value = _optional(arguments.get(field))
                if value is not None:
                    payload[field] = _decimal(value, field)
            if self.pine_version == 6 and any(
                relative in payload and absolute in payload
                for relative, absolute in (("profit", "limit"), ("loss", "stop"))
            ):
                payload.update(schema_version="2.4.0", price_pair_policy="first_trigger")
            trailing = set(payload).intersection({"trail_price", "trail_points", "trail_offset"})
            if trailing:
                if "trail_offset" not in trailing or not trailing.intersection(
                    {"trail_price", "trail_points"}
                ):
                    raise ValueError("trailing exit requires active trail_offset and activation")
                if Decimal(payload["trail_offset"]) < 0:
                    raise ValueError("trail_offset must be nonnegative")
                payload.update(
                    schema_version="2.5.0",
                    price_pair_policy=(
                        "first_trigger" if self.pine_version == 6 else "absolute_first"
                    ),
                )

        elif kind in {"close", "close_all"}:
            immediate = _optional(arguments.get("immediately"))
            if immediate is not None and type(immediate) is not bool:
                raise ValueError("strategy intent immediately must be a bool")
            payload.update(
                comment=_optional_string(arguments.get("comment"), "comment"),
                immediately=False if immediate is None else immediate,
            )
            if kind == "close":
                payload["from_entry"] = _nonempty_string(arguments["id"], "id")
                for field in ("qty", "qty_percent"):
                    value = _optional(arguments.get(field))
                    if value is not None:
                        payload[field] = _decimal(value, field)
        elif kind == "cancel":
            payload["order_id"] = command_id
        if _optional(arguments.get("alert_message")) is not None:
            payload["alert_message"] = _optional_string(arguments["alert_message"], "alert_message")
        if _optional(arguments.get("disable_alert")) is not None:
            if type(arguments["disable_alert"]) is not bool:
                raise ValueError("strategy intent disable_alert must be a bool")
            payload["disable_alert"] = arguments["disable_alert"]
        if kind == "exit":
            metadata = {
                name: _optional_string(arguments[name], name)
                for name in EXIT_METADATA_FIELDS
                if _optional(arguments.get(name)) is not None
            }
            if metadata:
                payload.update(
                    schema_version="2.6.0",
                    price_pair_policy="first_trigger"
                    if self.pine_version == 6
                    else "absolute_first",
                    **metadata,
                )
        return MappingProxyType(
            {
                "draft_schema_id": DRAFT_SCHEMA_ID,
                "payload": MappingProxyType(payload),
            }
        )

    def seal_committed(
        self,
        drafts: Iterable[object],
        *,
        start_sequence: int = 0,
    ) -> tuple[dict[str, Any], ...]:
        """Seal committed drafts with caller-owned contiguous intent sequencing."""

        if type(start_sequence) is not int or start_sequence < 0:
            raise ValueError("start_sequence must be a nonnegative integer")
        sealed_intents: list[dict[str, Any]] = []
        for draft in drafts:
            if (
                not isinstance(draft, Mapping)
                or set(draft) != {"draft_schema_id", "payload"}
                or draft.get("draft_schema_id") != DRAFT_SCHEMA_ID
                or (draft.get("payload") is not None and not isinstance(draft["payload"], Mapping))
            ):
                raise ValueError("committed delegated strategy draft is invalid")
            if draft["payload"] is None:
                continue
            payload = dict(draft["payload"])
            source_span = payload.get("source_span")
            if not isinstance(source_span, Mapping):
                raise ValueError("committed delegated strategy source span is invalid")
            payload["source_span"] = dict(source_span)
            payload["sequence"] = start_sequence + len(sealed_intents)
            payload["event_id"] = f"intent-event:{content_hash(payload, schema_id=SCHEMA_ID)}"
            sealed = seal_content_hash(payload, schema_id=SCHEMA_ID)
            validate_payload(SCHEMA_ID, sealed)
            if not verify_content_hash(sealed, schema_id=SCHEMA_ID):
                raise ValueError("sealed strategy intent content hash is invalid")
            sealed_intents.append(sealed)
        return tuple(sealed_intents)
