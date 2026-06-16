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

    pricing = _fill_pricing(engine, order, price)
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
    engine._apply_oca(order, bar, bar_index)


def _fill_pricing(engine, order: Order, price: float) -> FillPricing:
    if (
        order.order_type == "stop"
        and engine.config.mintick
        and order.stop_price is not None
        and abs(price - order.stop_price) <= max(1e-12, engine.config.mintick * 1e-9)
    ):
        price = round_to_step(
            price,
            engine.config.mintick,
            _stop_rounding_mode(order.side),
        )
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
    if order.order_type in {"limit", "stop_limit"} and engine.config.mintick:
        rounding_mode = "floor"
    fill_price = round_to_step(price + slip, engine.config.mintick, rounding_mode)
    commission = calculate_commission(
        fill_price,
        order.qty,
        engine.config.commission_type,
        engine.config.commission_value,
    )
    return FillPricing(fill_price, commission, slip)


def _stop_rounding_mode(side: Literal["buy", "sell"]) -> Literal["ceil", "floor"]:
    return "ceil" if side == "buy" else "floor"
