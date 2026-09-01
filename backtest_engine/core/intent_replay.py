"""Strict replay of canonical ``openpine.intent.v2`` event tapes."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any, Mapping, Sequence

from openpine_contracts import validate_payload, verify_content_hash
from openpine_contracts.errors import ContractError

INTENT_SCHEMA_ID = "openpine.intent.v2"
_IDENTITY_FIELDS = (
    "run_id",
    "strategy_id",
    "stack_id",
    "semantic_profile",
    "series_id",
    "instrument_id",
    "timeframe",
)
_SUPPORTED_RISK_RULES = frozenset(
    {
        "allow_entry_in",
        "max_drawdown",
        "max_position_size",
    }
)


class IntentReplayError(ValueError):
    """An intent tape cannot be applied without guessing."""

    code = "INTENT_REPLAY_ERROR"

    def __init__(self, message: str, *, details: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class IntentTapeValidationError(IntentReplayError):
    """An intent tape does not satisfy its schema or ordering invariants."""

    code = "INTENT_TAPE_VALIDATION_ERROR"


class IntentIdentityError(IntentReplayError):
    """An intent belongs to a different run, strategy, stack, or bar."""

    code = "INTENT_IDENTITY_ERROR"


class UnsupportedIntentError(IntentReplayError):
    """The engine cannot replay an otherwise valid intent kind."""

    code = "UNSUPPORTED_INTENT"


class UnsupportedRiskIntentError(UnsupportedIntentError):
    """The engine cannot replay a risk rule exactly."""

    code = "UNSUPPORTED_RISK_INTENT"


class IntentDecimalConversionError(IntentReplayError):
    """A contract decimal cannot cross the deterministic engine boundary."""

    code = "INTENT_DECIMAL_CONVERSION_ERROR"


@dataclass(frozen=True, slots=True)
class IntentReplayIdentity:
    """Execution identity which every event in a replay tape must match."""

    run_id: str
    strategy_id: str
    stack_id: str
    semantic_profile: str
    series_id: str
    instrument_id: str
    timeframe: str

    @classmethod
    def from_event(cls, event: Mapping[str, Any]) -> IntentReplayIdentity:
        return cls(**{field: str(event[field]) for field in _IDENTITY_FIELDS})


@dataclass(frozen=True, slots=True)
class ValidatedIntentTape:
    """Detached events validated as one ordered replay unit."""

    events: tuple[Mapping[str, Any], ...]
    identity: IntentReplayIdentity


def _schema_validate(event: Mapping[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise IntentTapeValidationError(
            f"intent at index {index} is not a mapping", details={"index": index}
        )
    detached = dict(event)
    try:
        validate_payload(INTENT_SCHEMA_ID, detached)
    except ContractError as exc:
        raise IntentTapeValidationError(
            f"intent schema validation failed at index {index}: {exc}",
            details={"index": index, "contract_error": exc.to_dict()},
        ) from exc
    if not verify_content_hash(detached, schema_id=INTENT_SCHEMA_ID):
        raise IntentTapeValidationError(
            f"intent content hash mismatch at index {index}", details={"index": index}
        )
    _validate_supported_semantics(detached, index)
    return detached


def _validate_supported_semantics(event: Mapping[str, Any], index: int) -> None:
    kind = str(event["kind"])
    if kind in {"entry", "order"}:
        _normalize_direction(event["direction"])
    if kind != "risk":
        return
    rule = str(event["risk_rule"])
    if rule not in _SUPPORTED_RISK_RULES:
        raise UnsupportedRiskIntentError(
            f"unsupported risk intent {rule!r}",
            details={"index": index, "risk_rule": rule},
        )
    if rule == "allow_entry_in":
        direction = str(event["risk_unit"]).lower()
        if direction not in {"long", "short", "all"}:
            raise UnsupportedRiskIntentError(
                f"unsupported allow_entry_in direction {event['risk_unit']!r}",
                details={"index": index, "risk_rule": rule},
            )
    elif rule == "max_drawdown":
        unit = str(event["risk_unit"]).lower()
        if unit not in {"percent", "percent_of_equity", "cash"}:
            raise UnsupportedRiskIntentError(
                f"unsupported max_drawdown unit {event['risk_unit']!r}",
                details={"index": index, "risk_rule": rule},
            )
    elif rule == "max_position_size":
        unit = str(event["risk_unit"]).lower()
        if unit not in {"fixed", "contracts", "shares"}:
            raise UnsupportedRiskIntentError(
                f"unsupported max_position_size unit {event['risk_unit']!r}",
                details={"index": index, "risk_rule": rule},
            )


def validate_intent_tape(
    events: Sequence[Mapping[str, Any]],
    *,
    expected_identity: IntentReplayIdentity | None = None,
    sequence_origin: int = 0,
) -> ValidatedIntentTape:
    """Validate schema, seals, identity, and total ordering before any replay."""

    rows = list(events)
    if not rows:
        raise IntentTapeValidationError("live pinelib tape is empty")

    validated: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    previous_bar_index = -1
    previous_recalc = -1
    identity = expected_identity
    bar_times: dict[int, int] = {}

    for index, raw in enumerate(rows):
        event = _schema_validate(raw, index)
        sequence = int(event["sequence"])
        if sequence != index + sequence_origin:
            raise IntentTapeValidationError(
                f"intent sequence must be contiguous from zero: expected {index + sequence_origin}, got {sequence}",
                details={"index": index, "sequence": sequence},
            )

        event_id = str(event["event_id"])
        if event_id in seen_event_ids:
            raise IntentTapeValidationError(
                f"event_id conflict: {event_id}", details={"index": index, "event_id": event_id}
            )
        seen_event_ids.add(event_id)

        actual_identity = IntentReplayIdentity.from_event(event)
        if identity is None:
            identity = actual_identity
        for field in _IDENTITY_FIELDS:
            expected_value = getattr(identity, field)
            actual_value = getattr(actual_identity, field)
            if actual_value != expected_value:
                raise IntentIdentityError(
                    f"intent {field} mismatch: expected {expected_value!r}, got {actual_value!r}",
                    details={"index": index, "field": field},
                )

        bar_index = int(event["bar_index"])
        recalc = int(event["recalc_iteration"])
        if bar_index < previous_bar_index:
            raise IntentTapeValidationError(
                "intent bar order must be nondecreasing",
                details={"index": index, "bar_index": bar_index},
            )
        if bar_index == previous_bar_index and recalc < previous_recalc:
            raise IntentTapeValidationError(
                "intent recalc ordering must be nondecreasing within a bar",
                details={"index": index, "recalc_iteration": recalc},
            )
        if bar_index != previous_bar_index:
            previous_recalc = -1
        previous_bar_index = bar_index
        previous_recalc = recalc

        bar_time = int(event["bar_open_time_utc_ms"])
        known_bar_time = bar_times.setdefault(bar_index, bar_time)
        if known_bar_time != bar_time:
            raise IntentIdentityError(
                "bar_open_time_utc_ms conflicts within one bar_index",
                details={"index": index, "bar_index": bar_index},
            )
        validated.append(event)

    assert identity is not None
    return ValidatedIntentTape(tuple(validated), identity)


def require_live_tape(
    events: Sequence[Mapping[str, Any]],
    *,
    expected_identity: IntentReplayIdentity | None = None,
    sequence_origin: int = 0,
) -> ValidatedIntentTape:
    """Return an immutable replay unit only after strict contract validation."""

    return validate_intent_tape(
        events,
        expected_identity=expected_identity,
        sequence_origin=sequence_origin,
    )


def _normalize_direction(value: object) -> str:
    direction = str(value).lower()
    if direction not in {"long", "short"}:
        raise UnsupportedIntentError(f"unsupported direct intent direction {value!r}")
    return direction


def _rounding_mode(name: object) -> str:
    mode = str(name)
    if mode not in {"nearest", "floor", "ceil"}:
        raise IntentDecimalConversionError(f"unsupported engine rounding mode {mode!r}")
    return mode


def _step_for_field(ctx: Any, field: str) -> tuple[Decimal | None, str]:
    config = getattr(ctx, "config", None)
    if field == "qty":
        raw_step = getattr(config, "qty_step", None)
        mode = _rounding_mode(getattr(config, "qty_rounding", "floor"))
    elif field in {"price", "limit", "stop"}:
        raw_step = getattr(config, "mintick", None)
        mode = _rounding_mode(getattr(config, "price_rounding", "nearest"))
    else:
        return None, "nearest"
    if raw_step is None:
        return None, mode
    try:
        step = Decimal(str(raw_step))
    except InvalidOperation as exc:
        raise IntentDecimalConversionError(f"invalid {field} rounding step") from exc
    if not step.is_finite() or step <= 0:
        raise IntentDecimalConversionError(f"{field} rounding step must be positive and finite")
    return step, mode


def decimal_to_engine_number(value: object, *, field: str, ctx: Any) -> float | None:
    """Convert a contract decimal string at one explicit, deterministic boundary."""

    if value is None:
        return None
    if type(value) is not str:
        raise IntentDecimalConversionError(f"intent {field} must be a decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise IntentDecimalConversionError(f"invalid intent {field} decimal") from exc
    if not number.is_finite():
        raise IntentDecimalConversionError(f"intent {field} must be finite")

    step, mode = _step_for_field(ctx, field)
    if step is not None:
        rounding = {
            "nearest": ROUND_HALF_UP,
            "floor": ROUND_FLOOR,
            "ceil": ROUND_CEILING,
        }[mode]
        number = (number / step).to_integral_value(rounding=rounding) * step
    result = float(number)
    if not math.isfinite(result):
        raise IntentDecimalConversionError(f"intent {field} is outside engine float range")
    return result


def _apply_risk_intent(ctx: Any, event: Mapping[str, Any]) -> None:
    rule = str(event["risk_rule"])
    unit = str(event["risk_unit"])
    value = decimal_to_engine_number(event["risk_value"], field="risk_value", ctx=ctx)
    assert value is not None
    if rule == "allow_entry_in":
        ctx.risk_allow_entry_in(unit)
        return
    if rule == "max_drawdown":
        ctx.risk_max_drawdown(value, unit)
        return
    if rule == "max_position_size":
        ctx.risk_max_position_size(value, unit)
        return
    raise UnsupportedRiskIntentError(f"unsupported risk intent {rule!r}")


def _apply_validated_intent(ctx: Any, event: Mapping[str, Any]) -> None:
    kind = str(event["kind"])
    command_id = str(event["command_id"])
    order_id = str(event["order_id"]) if "order_id" in event else command_id
    qty = decimal_to_engine_number(event.get("qty"), field="qty", ctx=ctx)
    qty_percent = decimal_to_engine_number(
        event.get("qty_percent"), field="qty_percent", ctx=ctx
    )
    limit = decimal_to_engine_number(event.get("limit"), field="limit", ctx=ctx)
    stop = decimal_to_engine_number(event.get("stop"), field="stop", ctx=ctx)

    if kind in {"entry", "order"}:
        getattr(ctx, kind)(
            order_id,
            _normalize_direction(event["direction"]),
            qty=qty,
            limit=limit,
            stop=stop,
            oca_name=event.get("oca_name"),
            oca_type=event.get("oca_type"),
            comment=event.get("comment"),
        )
        return
    if kind == "exit":
        ctx.exit(
            order_id,
            from_entry=event["from_entry"],
            qty=qty,
            qty_percent=qty_percent,
            limit=limit,
            stop=stop,
            oca_name=event.get("oca_name"),
            oca_type=event.get("oca_type"),
            comment=event.get("comment"),
            profit=decimal_to_engine_number(event.get("profit"), field="profit", ctx=ctx),
            loss=decimal_to_engine_number(event.get("loss"), field="loss", ctx=ctx),
            trail_price=decimal_to_engine_number(
                event.get("trail_price"), field="trail_price", ctx=ctx
            ),
            trail_points=decimal_to_engine_number(
                event.get("trail_points"), field="trail_points", ctx=ctx
            ),
            trail_offset=decimal_to_engine_number(
                event.get("trail_offset"), field="trail_offset", ctx=ctx
            ),
        )
        return
    if kind == "close":
        ctx.close(
            event["from_entry"],
            qty=qty,
            qty_percent=qty_percent,
            immediately=bool(event.get("immediately", False)),
            comment=event.get("comment"),
        )
        return
    if kind == "close_all":
        ctx.close_all(
            immediately=bool(event.get("immediately", False)),
            comment=event.get("comment"),
        )
        return
    if kind == "cancel":
        ctx.cancel(order_id)
        return
    if kind == "cancel_all":
        ctx.cancel_all()
        return
    if kind == "risk":
        _apply_risk_intent(ctx, event)
        return
    raise UnsupportedIntentError(f"unknown intent kind {kind!r}")


def apply_intent(ctx: Any, event: Mapping[str, Any]) -> None:
    """Strictly validate and apply one standalone sequence-zero event."""

    tape = validate_intent_tape([event])
    _apply_validated_intent(ctx, tape.events[0])


def apply_intents_for_bar(
    ctx: Any,
    events: Sequence[Mapping[str, Any]] | ValidatedIntentTape,
    bar_index: int,
    *,
    bar_open_time_utc_ms: int | None = None,
    expected_identity: IntentReplayIdentity | None = None,
) -> int:
    """Validate the whole tape, then apply this bar's events in sequence order."""

    if isinstance(events, ValidatedIntentTape):
        tape = events
        if expected_identity is not None and tape.identity != expected_identity:
            raise IntentIdentityError("validated tape identity does not match expected identity")
    else:
        tape = validate_intent_tape(events, expected_identity=expected_identity)

    selected = [event for event in tape.events if int(event["bar_index"]) == bar_index]
    if bar_open_time_utc_ms is not None:
        for event in selected:
            actual = int(event["bar_open_time_utc_ms"])
            if actual != bar_open_time_utc_ms:
                raise IntentIdentityError(
                    "bar_open_time_utc_ms mismatch: "
                    f"expected {bar_open_time_utc_ms}, got {actual}",
                    details={"bar_index": bar_index, "event_id": event["event_id"]},
                )
    for event in selected:
        _apply_validated_intent(ctx, event)
    return len(selected)


def apply_live_intents_for_bar(
    ctx: Any,
    events: Sequence[Mapping[str, Any]] | ValidatedIntentTape,
    bar_index: int,
    *,
    bar_open_time_utc_ms: int | None = None,
    expected_identity: IntentReplayIdentity | None = None,
) -> int:
    """Compatibility alias for the strict canonical replay path."""

    return apply_intents_for_bar(
        ctx,
        events,
        bar_index,
        bar_open_time_utc_ms=bar_open_time_utc_ms,
        expected_identity=expected_identity,
    )
