from __future__ import annotations

from typing import Any

from backtest_engine.context import StrategyContext
from backtest_engine.errors import UnsupportedRiskRuleError
from backtest_engine.models import Order


def apply_risk_rules(engine: Any, ctx: StrategyContext) -> None:
    for rule in ctx.drain_risk_rules():
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
            engine._max_position_size = float(rule.value or 0.0)
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


def projected_position_size(
    *,
    current_size: float,
    orders: list[Order],
    order: Order,
    exclude_order: Order | None = None,
) -> float:
    signed_qty = order.qty if order.direction == "long" else -order.qty
    return (
        current_size
        + pending_entry_position_delta(orders, exclude_order=exclude_order)
        + signed_qty
    )


def max_position_size_allows(
    *,
    max_position_size: float | None,
    current_size: float,
    orders: list[Order],
    order: Order,
    exclude_order: Order | None = None,
) -> bool:
    if max_position_size is None:
        return True
    if order.kind not in {"entry", "order"} or order.direction not in {"long", "short"}:
        return True
    projected = projected_position_size(
        current_size=current_size,
        orders=orders,
        order=order,
        exclude_order=exclude_order,
    )
    return abs(projected) <= float(max_position_size)
