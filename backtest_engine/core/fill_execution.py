"""Execute a triggered order fill while preserving engine event sequencing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backtest_engine.broker.commission import calculate_commission
from backtest_engine.broker.rounding import round_to_step
from backtest_engine.broker.slippage import slippage_value
from backtest_engine.models import Bar, Fill, Order


@dataclass(frozen=True, slots=True)
class FillPricing:
    price: float
    commission: float
    slippage: float


def execute_fill(
    engine, order: Order, bar: Bar, bar_index: int, price: float, point: str
) -> None:
    if order.kind == "exit":
        available = engine._available_exit_qty(order.from_entry, exclude_order=order)
        if available <= 0:
            code = (
                "ORDER_REJECTED_NO_MATCHING_ENTRY"
                if order.from_entry is not None
                else "ORDER_REJECTED_NO_AVAILABLE_POSITION_QTY"
            )
            engine._diag(
                code,
                "reduce order has no matching unreserved qty",
                "warning",
                bar_index,
                bar.time,
                order.id,
            )
            return
        order.qty = min(order.qty, available)

    pricing = _fill_pricing(engine, order, price, point)
    before = engine.position.direction
    engine.cash -= pricing.commission
    engine.position.realized_profit -= pricing.commission
    after = engine._apply_position(
        order,
        pricing.price,
        bar,
        bar_index,
        pricing.commission,
        fill_point=point,
    )
    fill = Fill(
        order.id,
        bar_index,
        bar.time,
        pricing.price,
        order.qty,
        order.direction,
        order.side,
        order.position_effect,
        before,
        after,
        "filled",
        pricing.commission,
        pricing.slippage,
        point,
    )
    engine.fills.append(fill)
    order.status = "filled"
    engine.last_trade_bar = bar_index
    engine._cb("on_fill", fill)
    engine._event(
        "ORDER_FILLED", f"order {order.id} filled", bar_index, bar.time, order.id
    )
    _consume_opposite_reverse_close_component(
        engine, order, before, bar, bar_index
    )
    engine._apply_oca(order, bar, bar_index)


def _consume_opposite_reverse_close_component(
    engine, filled_order: Order, closed_direction: str, bar: Bar, bar_index: int
) -> None:
    if filled_order.position_effect != "close":
        return
    if closed_direction not in {"long", "short"}:
        return
    remaining = float(filled_order.qty)
    if remaining <= 0.0:
        return
    opposite_direction = "short" if closed_direction == "long" else "long"
    qty_step = getattr(getattr(engine, "config", None), "qty_step", None) or 0.0
    eps = max(1e-12, float(qty_step) * 1e-6)
    for order in engine.orders:
        if remaining <= eps:
            return
        if order is filled_order:
            continue
        if order.kind != "entry" or order.status not in {"pending", "active"}:
            continue
        if order.position_effect != "reverse":
            continue
        if order.direction != opposite_direction:
            continue
        consumed = min(order.qty, remaining)
        order.qty -= consumed
        remaining -= consumed
        if order.qty <= eps:
            order.status = "cancelled"
            engine._cb("on_order_cancelled", order)
            engine._event(
                "ORDER_CANCELLED",
                f"opposite reverse entry {order.id} consumed by close order",
                bar_index,
                bar.time,
                order.id,
            )
            continue
        order.position_effect = "open"
        engine._event(
            "ORDER_MODIFIED",
            f"opposite reverse entry {order.id} reduced by close order",
            bar_index,
            bar.time,
            order.id,
        )


def _fill_pricing(engine, order: Order, price: float, point: str) -> FillPricing:
    if (
        order.order_type == "stop"
        and engine.config.mintick
        and order.stop_price is not None
        and abs(price - order.stop_price) <= max(1e-12, engine.config.mintick * 1e-9)
    ):
        mode = (
            _trailing_stop_rounding_mode(order.side)
            if _is_trailing_stop(order)
            else _stop_rounding_mode(order.side)
        )
        price = round_to_step(price, engine.config.mintick, mode)
    slip_raw = (
        0.0 if order.order_type in {"limit", "stop_limit"} else engine.config.slippage
    )
    slip = slippage_value(
        price,
        order.side,
        order.position_effect,
        slip_raw,
        engine.config.slippage_type,
        engine.config.mintick,
    )
    rounding_mode = engine.config.price_rounding
    if (
        order.order_type in {"limit", "stop_limit"}
        and engine.config.mintick
        and not _is_limit_gap_open_fill(order, price, point, engine.config.mintick)
    ):
        rounding_mode = "ceil" if order.side == "sell" else "floor"
    fill_price = round_to_step(price + slip, engine.config.mintick, rounding_mode)
    commission = calculate_commission(
        fill_price,
        order.qty,
        engine.config.commission_type,
        engine.config.commission_value,
    )
    return FillPricing(fill_price, commission, slip)


def _is_trailing_stop(order: Order) -> bool:
    return (
        order.trail_price is not None
        or order.trail_points is not None
        or order.trail_offset is not None
    )


def _is_limit_gap_open_fill(order: Order, price: float, point: str, tick: float) -> bool:
    if order.kind != "exit":
        return False
    if not (point == "open" or point.endswith(".open")):
        return False
    limit = order.limit_price
    if limit is None:
        return False
    eps = max(1e-12, tick * 1e-9)
    if order.side == "sell":
        return price > limit + eps
    return price < limit - eps


def _stop_rounding_mode(side: Literal["buy", "sell"]) -> Literal["ceil", "floor"]:
    return "ceil" if side == "buy" else "floor"


def _trailing_stop_rounding_mode(side: Literal["buy", "sell"]) -> Literal["ceil", "floor"]:
    # TV rounds trailing stop levels in the favorable direction for the
    # position: long trail exits (sell stops) up, short trail exits (buy stops)
    # down. Plain fixed stops still use conservative stop rounding above.
    return "floor" if side == "buy" else "ceil"
