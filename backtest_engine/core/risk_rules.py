from __future__ import annotations

from typing import Any
import math
from decimal import Decimal, ROUND_FLOOR

from backtest_engine.context import StrategyContext
from backtest_engine.errors import UnsupportedRiskRuleError
from backtest_engine.models import Order


def apply_risk_rules(engine: Any, ctx: StrategyContext) -> None:
    rules = ctx.drain_risk_rules()
    # Validate the entire entry-control batch before mutating broker policy.
    for rule in rules:
        if rule.name not in {"allow_entry_in", "max_position_size", "max_drawdown"}:
            raise UnsupportedRiskRuleError(f"unsupported risk rule: {rule.name}")
        if rule.name == "max_drawdown" and rule.value_type not in {"percent_of_equity", "cash"}:
            raise UnsupportedRiskRuleError("unsupported drawdown unit")
        if rule.name == "max_position_size" and rule.value_type != "fixed":
            raise UnsupportedRiskRuleError("unsupported position-size unit")
        if rule.name == "max_position_size":
            validate_position_limit(rule.value)
        if rule.name == "allow_entry_in" and rule.direction not in {"long", "short", "all"}:
            raise UnsupportedRiskRuleError("invalid allow_entry_in direction")
    for rule in rules:
        if rule.name == "allow_entry_in":
            if rule.direction == "long":
                engine._allow_long = True
                engine._allow_short = False
                continue
            if rule.direction == "short":
                engine._allow_long = False
                engine._allow_short = True
                continue
            if rule.direction == "all":
                engine._allow_long = True
                engine._allow_short = True
                continue
        elif rule.name == "max_drawdown":
            engine._early_stop_enabled = True
            if rule.value_type == "percent_of_equity":
                engine._max_drawdown_stop_percent = float(rule.value or 0.0)
                continue
            if rule.value_type == "cash":
                engine._max_drawdown_stop_cash = float(rule.value or 0.0)
                continue
        elif rule.name == "max_position_size" and rule.value_type == "fixed":
            value = validate_position_limit(rule.value)
            # Multiple active maximum-size rules combine by their strictest limit.
            engine._max_position_size = (
                value
                if engine._max_position_size is None
                else min(engine._max_position_size, value)
            )
            continue
        raise UnsupportedRiskRuleError(f"unsupported risk rule: {rule.name}")


def pending_entry_position_delta(
    orders: list[Order],
    *,
    exclude_order: Order | None = None,
) -> float:
    total = 0.0
    for order in orders:
        if order is exclude_order:
            continue
        if (
            order.kind in {"entry", "order"}
            and not order.reduce_only
            and order.direction in {"long", "short"}
            and order.status in {"pending", "active"}
        ):
            total += order.qty if order.direction == "long" else -order.qty
    return total


def validate_position_limit(value: object) -> float:
    """Zero blocks new exposure; bool, NA and float underflow are not sizes."""
    if type(value) not in (int, float):
        raise ValueError("max_position_size requires a finite nonnegative number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError("max_position_size is outside the runtime range") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError("max_position_size requires a finite nonnegative number")
    if value != 0 and number == 0:
        raise ValueError("max_position_size is outside the runtime range")
    return number


def cap_entry_quantity(engine: Any, order: Order, *, at_fill: bool = False) -> float:
    """Compute a transaction size from actual exposure, never reserve pending orders.

    Only strategy.entry is constrained. A reversal's closing component does not
    consume the new direction's cap. Called at submission AND each real fill so
    simultaneous price orders cannot independently exceed the final position limit.
    """
    limit = engine._max_position_size
    if limit is None or order.kind != "entry" or order.reduce_only:
        return order.qty
    limit = validate_position_limit(limit)
    current = abs(engine.position.size)
    opposite = engine.position.direction not in ("flat", order.direction)
    requested = order.entry_open_qty
    if requested is None:
        requested = (
            max(0.0, order.qty - current) if order.position_effect == "reverse" else order.qty
        )
    closing = current if opposite and order.position_effect == "reverse" else 0.0
    desired = requested + closing
    # Without reversal, the ordinary opposite quantity can still reduce exposure.
    capacity = limit + current if opposite else max(0.0, limit - current)
    # A previously clipped or OCA-reduced order must never grow at its fill.
    quantity = min(order.qty if at_fill else desired, capacity)
    step = engine.config.qty_step
    if step is not None and step > 0:
        quantum = Decimal(str(step))
        quantity = float(
            (Decimal(str(quantity)) / quantum).to_integral_value(rounding=ROUND_FLOOR) * quantum
        )
    minimum = engine.config.min_qty
    if minimum is not None and quantity < minimum:
        quantity = 0.0
    return max(0.0, quantity)


def enforce_entry_fill_risk(engine: Any, order: Order, bar: Any, index: int) -> bool:
    """Recheck immediately before commission/position mutation, not after a fill."""
    if order.kind != "entry":
        return True
    if not engine._entry_direction_allowed(order.direction):
        order.status = "cancelled"
        order.pending_exits.clear()
        engine._event(
            "ORDER_CANCELLED",
            "entry direction prohibited by active risk rule",
            index,
            bar.time,
            order.id,
        )
        engine._cb("on_order_cancelled", order)
        return False
    before = order.qty
    order.qty = cap_entry_quantity(engine, order, at_fill=True)
    if order.qty <= 0:
        order.status = "cancelled"
        order.pending_exits.clear()
        engine._diag(
            "ORDER_REJECTED_RISK_MAX_POSITION_SIZE",
            "no entry capacity at execution",
            "warning",
            index,
            bar.time,
            order.id,
        )
        engine._event("ORDER_CANCELLED", "entry capacity exhausted", index, bar.time, order.id)
        engine._cb("on_order_cancelled", order)
        return False
    if order.qty != before:
        engine._event(
            "ORDER_MODIFIED", "entry quantity limited at execution", index, bar.time, order.id
        )
    return True


_RISK_FIELDS = (
    "_allow_long",
    "_allow_short",
    "_max_position_size",
    "_early_stop_enabled",
    "_min_equity_stop",
    "_max_drawdown_stop_percent",
    "_max_drawdown_stop_cash",
    "_max_bars_without_trade",
)


def capture_risk_state(engine: Any) -> dict[str, object]:
    return {key: getattr(engine, key) for key in _RISK_FIELDS}


def validate_risk_state(state: object) -> dict[str, object]:
    if not isinstance(state, dict) or set(state) != set(_RISK_FIELDS):
        raise ValueError("broker risk state is incomplete")
    for key, value in state.items():
        if key in {"_allow_long", "_allow_short", "_early_stop_enabled"}:
            if type(value) is not bool:
                raise ValueError("broker risk flags must be bool")
        elif value is not None:
            try:
                finite = type(value) in (int, float) and math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite or (key != "_min_equity_stop" and value < 0):
                raise ValueError("broker risk limits are outside their finite domain")
            if key == "_max_bars_without_trade" and type(value) is not int:
                raise ValueError("broker no-trade limit must be an integer")
    return dict(state)


def restore_risk_state(engine: Any, state: object) -> None:
    for key, value in validate_risk_state(state).items():
        setattr(engine, key, value)


def reconcile_pending_entry_directions(
    engine: Any, bar: Any, index: int, *, recalc: bool = False
) -> None:
    """Native rule changes cannot leave a prohibited entry armed at an old price.

    Global compiled rules are fixed per run; this also makes the native callback
    API deterministic when a direction control is applied after order submission.
    """
    for order in engine.orders:
        if order.kind != "entry" or order.status not in {"active", "pending"}:
            continue
        if engine._entry_direction_allowed(order.direction):
            continue
        order.pending_exits.clear()
        if engine.position.direction in {"flat", order.direction}:
            order.status = "cancelled"
            engine._event(
                "ORDER_CANCELLED",
                "pending entry direction is prohibited",
                index,
                bar.time,
                order.id,
            )
            engine._cb("on_order_cancelled", order)
            continue
        order.kind = "close"
        order.direction = engine.position.direction
        order.side = "sell" if order.direction == "long" else "buy"
        order.position_direction = engine.position.direction
        order.position_effect = "close"
        order.reduce_only = True
        order.qty = abs(engine.position.size)
        order.order_type = "market"
        order.limit_price = order.stop_price = None
        order.stop_limit_activated = False
        order.created_bar_index, order.created_time = index, bar.time
        order.active_from_bar_index = (
            index if recalc or engine.config.process_orders_on_close else index + 1
        )
        order.status = "active" if order.active_from_bar_index == index else "pending"
        engine._event(
            "ORDER_MODIFIED", "prohibited reversal becomes market close", index, bar.time, order.id
        )


def validate_snapshot_risk(snapshot: Any) -> None:
    version = snapshot.risk_state_version
    if type(version) is not int or version not in (0, 1):
        raise ValueError("unknown broker risk snapshot version")
    if version == 1 or snapshot.risk_state is not None:
        validate_risk_state(snapshot.risk_state)
