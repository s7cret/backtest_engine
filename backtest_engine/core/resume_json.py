"""Bounded JSON codec for the engine-owned committed resume state.

Foreign runtime object graphs need their owner's codec. No import name supplied
by a checkpoint is resolved, and no live engine is touched during decoding.
"""

from __future__ import annotations

from dataclasses import fields
import json
import math
import re
from types import UnionType
from typing import Any, Literal, NoReturn, Union, cast, get_args, get_origin, get_type_hints

from backtest_engine.context.command_buffer import ExitPayload
from backtest_engine.config import BacktestConfig
from backtest_engine.core.position_accounting import quantity_epsilon
from backtest_engine.core.resume_state import (
    _STRICT_STATISTICS_COUNTS,
    _STRICT_STATISTICS_LISTS,
    _STRICT_STATISTICS_TOTALS,
    _validate_strict_statistics_against_broker,
    _validate_strict_statistics_state,
    bar_prefix_fingerprint,
)
from backtest_engine.core.risk_rules import validate_snapshot_risk
from backtest_engine.core.resume_accounting import (
    native_accounting_inputs,
    read_accounting_inputs,
    validate_broker_ledger,
    validate_broker_values,
)
from backtest_engine.core.score_window import ScoreWindowPlan
from backtest_engine.core.state_snapshot import BrokerSnapshot, JsonStateSerializer
from backtest_engine.errors import ResumeUnsupportedError
from backtest_engine.models import (
    BacktestResumeState,
    BarSeries,
    Diagnostic,
    EquityPoint,
    Fill,
    Order,
    Position,
    Trade,
)

_SCHEMA = "backtest-engine.resume"
_MAX_BYTES = 16 * 1024 * 1024
_MAX_DEPTH = 64
_MAX_ITEMS = 200_000
_MODELS: dict[str, type[Any]] = {
    cls.__name__: cls
    for cls in (
        BacktestResumeState,
        BrokerSnapshot,
        Position,
        Order,
        Fill,
        Trade,
        EquityPoint,
        Diagnostic,
    )
}
_MODEL_NAMES: dict[type[Any], str] = {cls: name for name, cls in _MODELS.items()}
_FIELDS: dict[type[Any], set[str]] = {
    cls: {field.name for field in fields(cls)} for cls in (*_MODELS.values(), ExitPayload)
}
_HINTS: dict[type[Any], dict[str, Any]] = {cls: get_type_hints(cls) for cls in _FIELDS}


def _fail(message: str) -> NoReturn:
    raise ResumeUnsupportedError("typed JSON resume: " + message)


class _Budget:
    def __init__(self, max_bytes: int, max_depth: int, max_items: int) -> None:
        self.max_bytes, self.max_depth, self.max_items = max_bytes, max_depth, max_items
        self.items = self.bytes = 0

    def touch(self, depth: int, value: object = None) -> None:
        self.items += 1
        self.bytes += 32  # Includes container/tag/key overhead before JSON allocation.
        if depth > self.max_depth or self.items > self.max_items:
            _fail("depth/item budget exceeded")
        if isinstance(value, str):
            if len(value) > self.max_bytes:
                _fail("byte budget exceeded")
            try:
                self.bytes += len(value.encode("utf-8"))
            except UnicodeError as error:
                raise ResumeUnsupportedError("typed JSON resume: invalid UTF-8 string") from error
        if self.bytes > self.max_bytes:
            _fail("byte budget exceeded")


def _json_depth(payload: bytes, limit: int) -> None:
    """Bound nesting before json.loads, including escaped/string delimiters."""
    depth = 0
    quoted = escaped = False
    for byte in payload:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > limit:
                _fail("JSON depth budget exceeded")
        elif byte in (93, 125):
            depth -= 1


def _primitive(value: object) -> bool:
    if value is None or type(value) in (bool, str):
        return True
    if type(value) is int:
        if value.bit_length() > 4096:
            _fail("integer outside codec resource domain")
        return True
    if type(value) is float:
        if not math.isfinite(value):
            _fail("non-finite number")
        return True
    return False


def _encode(value: Any, budget: _Budget, depth: int, ancestors: set[int]) -> Any:
    budget.touch(depth, value)
    if _primitive(value):
        return value
    if id(value) in ancestors:
        _fail("cyclic object graph requires an owner codec")
    ancestors.add(id(value))
    try:
        cls = type(value)
        if cls in _MODEL_NAMES:
            return {
                "type": _MODEL_NAMES[cls],
                "fields": {
                    field.name: _encode(getattr(value, field.name), budget, depth + 1, ancestors)
                    for field in fields(cls)
                },
            }
        if cls is list:
            return [_encode(item, budget, depth + 1, ancestors) for item in value]
        if cls is tuple:
            return {
                "type": "tuple",
                "items": [_encode(item, budget, depth + 1, ancestors) for item in value],
            }
        if cls is dict:
            items = {}
            for key, item in value.items():
                if type(key) is not str:
                    _fail("mapping keys must be strings")
                budget.touch(depth + 1, key)
                items[key] = _encode(item, budget, depth + 1, ancestors)
            return {"type": "mapping", "items": items}
        _fail("unregistered object type; foreign state requires an owner codec")
    finally:
        ancestors.remove(id(value))


def _check_type(value: Any, hint: Any, path: str) -> None:
    origin, args = get_origin(hint), get_args(hint)
    if hint in (Any, object):
        return  # Every descendant has already passed the closed decode registry.
    if origin in (Union, UnionType):
        for alternative in args:
            try:
                _check_type(value, alternative, path)
                return
            except ResumeUnsupportedError:
                pass
        _fail(path + " has the wrong optional/union type")
    elif origin is Literal:
        if not any(type(value) is type(item) and value == item for item in args):
            _fail(path + " has an unknown enum value")
    elif hint is float:
        try:
            valid = type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid:
            _fail(path + " must be a finite number, not bool")
    elif origin in (list, dict) or hint in (list, dict):
        kind = origin or hint
        if type(value) is not kind:
            _fail(path + " has the wrong container type")
        if args and kind is list:
            for item in value:
                _check_type(item, args[0], path + "[]")
        if args and kind is dict:
            for key, item in value.items():
                _check_type(key, args[0], path + ".key")
                _check_type(item, args[1], path + "[]")
    elif type(value) is not hint:
        _fail(path + " has the wrong scalar/model type")


def _check_fields(cls: type, values: dict, path: str) -> None:
    if set(values) != _FIELDS[cls]:
        _fail(path + " has missing or unknown fields")
    for name, value in values.items():
        _check_type(value, _HINTS[cls][name], path + "." + name)


def _decode(value: Any, budget: _Budget, depth: int) -> Any:
    budget.touch(depth, value)
    if _primitive(value):
        return value
    if type(value) is list:
        return [_decode(item, budget, depth + 1) for item in value]
    if type(value) is not dict or type(value.get("type")) is not str:
        _fail("untyped object or missing registered type")
    name = value["type"]
    if name == "tuple":
        if set(value) != {"type", "items"} or type(value["items"]) is not list:
            _fail("invalid tuple fields")
        return tuple(_decode(item, budget, depth + 1) for item in value["items"])
    if name == "mapping":
        if set(value) != {"type", "items"} or type(value["items"]) is not dict:
            _fail("invalid mapping fields")
        result = {}
        for key, item in value["items"].items():
            budget.touch(depth + 1, key)
            result[key] = _decode(item, budget, depth + 1)
        return result
    if name not in _MODELS or set(value) != {"type", "fields"}:
        _fail("unknown registered type or object fields")
    if type(value["fields"]) is not dict:
        _fail("model fields must be an object")
    cls = _MODELS[name]
    if set(value["fields"]) != _FIELDS[cls]:
        _fail(name + " has missing or unknown fields")
    values = {key: _decode(item, budget, depth + 1) for key, item in value["fields"].items()}
    _check_fields(cls, values, name)
    if cls is Trade and values["entry_qty"] is None:
        _fail("trade entry_qty cannot be null in a complete checkpoint")
    return cls(**values)


def _index(value: int | None, cursor: int, path: str, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if type(value) is not int or not 0 <= value <= cursor:
        _fail(path + " is outside processed bar indices")


def _exit_template(key: str, row: dict, cursor: int, *, persistent: bool) -> None:
    keys = {"payload", "bar_index", "time"} | ({"direction"} if persistent else set())
    if set(row) != keys or type(row["payload"]) is not dict:
        _fail("exit template is incomplete or has unknown fields")
    _check_fields(ExitPayload, row["payload"], "exit payload")
    ExitPayload(**row["payload"])  # Reuse the owner's pure metadata/trailing admission.
    if row["payload"]["id"] != key:
        _fail("exit template identity does not match its key")
    _index(row["bar_index"], cursor, "exit template bar")
    if type(row["time"]) is not int:
        _fail("exit template time must be int")
    if persistent and row["direction"] not in ("long", "short"):
        _fail("invalid persistent exit direction")


def _validate_state(state: BacktestResumeState) -> None:
    """Validate the complete engine projection without touching a live owner."""
    cursor = state.bar_index
    if type(cursor) is not int or cursor < -1:
        _fail("bar_index must be int >= -1")
    if re.fullmatch(r"[0-9a-f]{64}", state.config_snapshot_hash) is None:
        _fail("invalid config identity")
    if type(state.broker_state) is not BrokerSnapshot:
        _fail("broker_state must be a typed BrokerSnapshot")
    if state.order_book_state is not None:
        _fail("foreign order_book_state needs an owner codec")
    if state.metadata.get("resume_contract") != "engine-broker-snapshot-v1":
        _fail("unknown engine resume contract")
    for key in ("bar_prefix_fingerprint", "realtime_tick_schedule_fingerprint"):
        if key in state.metadata and (
            type(state.metadata[key]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", state.metadata[key]) is None
        ):
            _fail("invalid " + key)
    broker = cast(BrokerSnapshot, state.broker_state)
    validate_snapshot_risk(broker)
    position = broker.position
    if (
        (position.direction == "flat" and position.size != 0)
        or (position.direction == "long" and position.size <= 0)
        or (position.direction == "short" and position.size >= 0)
    ):
        _fail("position direction/size mismatch")
    for key in ("max_drawdown", "max_drawdown_percent", "max_runup", "max_runup_percent"):
        if getattr(broker, key) < 0:
            _fail("negative broker " + key)
    _index(broker.last_trade_bar, cursor, "last_trade_bar", optional=True)
    for order in broker.orders:
        _index(order.created_bar_index, cursor, "order creation")
        if not order.created_bar_index <= order.active_from_bar_index <= cursor + 1:
            _fail("invalid order activation index")
        if order.qty < 0 or order.reserved_qty < 0 or order.reserved_qty > order.qty:
            _fail("invalid order quantities/reservation")
        if order.entry_fill_index is not None:
            if not 0 <= order.entry_fill_index < len(broker.fills):
                _fail("invalid order entry fill link")
            if (
                order.from_entry is not None
                and broker.fills[order.entry_fill_index].order_id != order.from_entry
            ):
                _fail("order entry fill identity mismatch")
        for key, row in order.pending_exits.items():
            _exit_template(key, row, cursor, persistent=False)
    for fill in broker.fills:
        _index(fill.bar_index, cursor, "fill")
        if fill.qty <= 0 or fill.commission < 0:
            _fail("invalid fill quantity/commission")
    for open_state, trades in ((True, broker.open_trades), (False, broker.closed_trades)):
        for trade in trades:
            _index(trade.entry_bar_index, cursor, "trade entry")
            _index(trade.exit_bar_index, cursor, "trade exit", optional=open_state)
            if (
                trade.is_open is not open_state
                or trade.qty <= 0
                or trade.entry_qty is None
                or trade.entry_qty < trade.qty
            ):
                _fail("invalid trade state/quantity")
            index = trade.entry_fill_index
            if index is None or not 0 <= index < len(broker.fills):
                _fail("invalid trade entry fill link")
            fill = broker.fills[index]
            if (fill.order_id, fill.bar_index, fill.time, fill.price, fill.direction) != (
                trade.entry_id,
                trade.entry_bar_index,
                trade.entry_time,
                trade.entry_price,
                trade.direction,
            ):
                _fail("trade entry fill identity mismatch")
            if open_state and any(
                v is not None
                for v in (trade.exit_bar_index, trade.exit_time, trade.exit_price, trade.exit_id)
            ):
                _fail("open trade contains a completed exit")
            if not open_state and (trade.exit_time is None or trade.exit_price is None):
                _fail("closed trade is incomplete")
    for key, row in broker.all_entry_exits.items():
        _exit_template(key, row, cursor, persistent=True)
    accounting = (
        read_accounting_inputs(state.metadata["native_accounting"])
        if "native_accounting" in state.metadata
        else None
    )
    validate_broker_ledger(broker, qty_epsilon=accounting.qty_epsilon if accounting else None)
    statistics = _validate_strict_statistics_state(state, label="typed JSON resume")
    if set(statistics) != {
        *_STRICT_STATISTICS_LISTS,
        *_STRICT_STATISTICS_COUNTS,
        *_STRICT_STATISTICS_TOTALS,
    }:
        _fail("statistics_state has unknown fields")
    for name in ("equity_curve", "score_equity_points"):
        points = statistics[name]
        if any(type(point) is not EquityPoint for point in points):
            _fail("statistics points must be typed EquityPoint values")
        indices = [point.bar_index for point in points]
        if any(type(index) is not int or not 0 <= index <= cursor for index in indices):
            _fail("statistics point index outside checkpoint")
        if indices != sorted(set(indices)):
            _fail("statistics point indices must be increasing and unique")
        if name == "equity_curve" and points:
            if len(points) != cursor + 1 or any(
                point.bar_index != expected for expected, point in enumerate(points)
            ):
                _fail("partial equity history")
    for name in ("events", "warnings", "errors"):
        if any(type(item) is not Diagnostic for item in statistics[name]):
            _fail("statistics diagnostics must be typed Diagnostic values")
        for item in statistics[name]:
            _index(item.bar_index, cursor, "diagnostic", optional=True)
    if statistics["closed_trade_stats_count"] > len(broker.closed_trades):
        _fail("invalid scored closed trade count")
    # Score window is contextual; retain its count for the existing strict owner.
    # Reuse its complete engine totals validation with the all-bars count here.
    all_bars = {**statistics, "closed_trade_stats_count": len(broker.closed_trades)}
    _validate_strict_statistics_against_broker(all_bars, broker, score_start_index=0)
    if accounting:
        validate_broker_values(
            broker,
            accounting,
            mark_price=accounting.mark_price,
            mark_tick=accounting.mintick,
            equity_points=statistics["equity_curve"],
            point_prices={cursor: accounting.mark_price}
            if accounting.mark_price is not None
            else None,
        )


def admit_resume_input(
    state: BacktestResumeState,
    config: BacktestConfig,
    config_hash: str,
    series: BarSeries,
    plan: ScoreWindowPlan,
    mark_tick: float | None = None,
) -> None:
    """Check contextual admission before replacing any live engine state."""
    if state.bar_index >= len(series):
        _fail("bar_index must reference an available input bar")
    if "native_accounting" not in state.metadata:
        legacy_statistics = cast(dict[str, Any], state.statistics_state)
        validate_broker_ledger(
            cast(BrokerSnapshot, state.broker_state), qty_epsilon=quantity_epsilon(config)
        )
        validate_broker_values(
            cast(BrokerSnapshot, state.broker_state),
            config,
            mark_price=series.close[state.bar_index] if state.bar_index >= 0 else None,
            mark_tick=mark_tick,
            equity_points=legacy_statistics["equity_curve"],
        )
    if config.resume_validation_policy != "strict":
        return  # The existing consuming owner emits lenient mismatch diagnostics.
    if state.config_snapshot_hash != config_hash:
        _fail("config hash does not match current config snapshot")
    if "native_accounting" in state.metadata:
        accounting = read_accounting_inputs(state.metadata["native_accounting"])
        expected = read_accounting_inputs(
            native_accounting_inputs(
                config,
                mark_tick,
                series.close[state.bar_index] if state.bar_index >= 0 else None,
            )
        )
        if accounting != expected:
            _fail("accounting context does not match config/input identity")
    fingerprint = state.metadata.get("bar_prefix_fingerprint")
    if fingerprint is None:
        _fail("strict resume state is missing bar prefix fingerprint")
    statistics = _validate_strict_statistics_state(state, label="typed JSON resume")
    collect_equity = "equity_curve" in config.required_outputs or config.collect_equity_curve
    if collect_equity and len(statistics["equity_curve"]) != state.bar_index + 1:
        _fail("partial equity history")
    indices = [point.bar_index for point in statistics["score_equity_points"]]
    score_start = max(0, plan.score_start_index)
    expected_count = (
        max(0, state.bar_index - score_start + 1) if plan.score_mode and collect_equity else 0
    )
    if len(indices) != expected_count or any(
        index != score_start + ordinal for ordinal, index in enumerate(indices)
    ):
        _fail("score equity history does not match the processed score window")
    if fingerprint != bar_prefix_fingerprint(series, state.bar_index + 1):
        _fail("bar prefix fingerprint does not match processed bars")
    if "native_accounting" in state.metadata:
        validate_broker_values(
            cast(BrokerSnapshot, state.broker_state),
            accounting,
            mark_price=accounting.mark_price,
            mark_tick=accounting.mintick,
            equity_points=statistics["equity_curve"],
            point_prices={
                point.bar_index: series.close[point.bar_index]
                for point in statistics["equity_curve"]
            },
        )
    _validate_strict_statistics_against_broker(
        statistics,
        cast(BrokerSnapshot, state.broker_state),
        score_start_index=max(0, plan.score_start_index),
    )


class JsonResumeStateSerializer:
    """Versioned closed-registry StateSerializer for engine-owned resume data."""

    serializer_id = "json-resume-v1"

    def __init__(
        self,
        *,
        max_bytes: int = _MAX_BYTES,
        max_depth: int = _MAX_DEPTH,
        max_items: int = _MAX_ITEMS,
    ) -> None:
        for value, ceiling in (
            (max_bytes, _MAX_BYTES),
            (max_depth, _MAX_DEPTH),
            (max_items, _MAX_ITEMS),
        ):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError("codec limits must be positive integers within the hard bounds")
        self.max_bytes, self.max_depth, self.max_items = max_bytes, max_depth, max_items

    def dumps(self, state: object) -> bytes:
        if type(state) is not BacktestResumeState:
            _fail("root must be BacktestResumeState")
        budget = _Budget(self.max_bytes, self.max_depth, self.max_items)
        encoded = _encode(state, budget, 0, set())
        # Validate a detached projection, including inputs mutated after export.
        decoded = _decode(encoded, _Budget(self.max_bytes, self.max_depth, self.max_items), 0)
        try:
            _validate_state(decoded)
        except (ValueError, TypeError, ArithmeticError) as error:
            raise ResumeUnsupportedError(
                "typed JSON resume: invalid state: " + str(error)
            ) from error
        payload = json.dumps(
            {"schema": _SCHEMA, "version": 1, "state": encoded},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if len(payload) > self.max_bytes:
            _fail("byte budget exceeded")
        _json_depth(payload, self.max_depth)
        return payload

    def loads(self, payload: bytes) -> BacktestResumeState:
        if type(payload) is not bytes or len(payload) > self.max_bytes:
            _fail("need bytes within byte budget")
        _json_depth(payload, self.max_depth)
        try:
            envelope = JsonStateSerializer().loads(payload)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise ResumeUnsupportedError(
                "typed JSON resume: invalid strict JSON: " + str(error)
            ) from error
        if type(envelope) is not dict or set(envelope) != {"schema", "version", "state"}:
            _fail("missing or unknown envelope fields")
        if (
            envelope["schema"] != _SCHEMA
            or type(envelope["version"]) is not int
            or envelope["version"] != 1
        ):
            _fail("unknown schema/version")
        state = _decode(
            envelope["state"], _Budget(self.max_bytes, self.max_depth, self.max_items), 0
        )
        if type(state) is not BacktestResumeState:
            _fail("root must be BacktestResumeState")
        try:
            _validate_state(state)
        except (ValueError, TypeError, ArithmeticError) as error:
            raise ResumeUnsupportedError(
                "typed JSON resume: invalid state: " + str(error)
            ) from error
        return state
