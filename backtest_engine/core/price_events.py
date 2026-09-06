"""Next executable boundary on one monotone historical price-path segment.

Open gaps and observed realtime ticks are discrete points, not interpolations.
Recompute after every event: stop-limit activation, OCA and attached exits can
change the next boundary. This helper never mutates an order or executes a fill.
"""

from __future__ import annotations

from typing import Any


def next_price_event(engine: Any, start: float, end: float, bar_index: int) -> float:
    """Choose the nearest strictly-forward plain-order trigger, or the endpoint."""
    if start == end:
        return end
    ascending = end > start
    nearest = end
    for order in engine.orders:
        if order.status != "active":
            continue
        if engine.config.process_orders_on_close and order.created_bar_index == bar_index:
            continue  # Closing-tick orders cannot see earlier path crossings.
        if any(
            value is not None
            for value in (order.trail_price, order.trail_points, order.trail_offset)
        ):
            continue  # Trailing evolution remains owned by the existing scanner.
        if (
            order.kind == "exit"
            and order.from_entry is not None
            and not engine._matching_open_trades(order.from_entry)
        ):
            continue
        is_limit = order.order_type == "limit" or (
            order.order_type == "stop_limit" and order.stop_limit_activated
        )
        is_stop = order.order_type == "stop" or (
            order.order_type == "stop_limit" and not order.stop_limit_activated
        )
        trigger = None
        if is_limit and order.limit_price is not None:
            # Verification penetration changes the trigger, not the execution price.
            penetration = (
                engine.config.mintick or 0.0
            ) * engine.config.backtest_fill_limits_assumption_ticks
            trigger = order.limit_price + (penetration if order.side == "sell" else -penetration)
            if ascending != (order.side == "sell"):
                continue
        elif is_stop and order.stop_price is not None:
            trigger = order.stop_price
            if ascending != (order.side == "buy"):
                continue
        if trigger is not None:
            if ascending and start < trigger < nearest:
                nearest = trigger
            elif not ascending and nearest < trigger < start:
                nearest = trigger
    return nearest
