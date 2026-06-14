"""Scan bar price paths and trigger active orders."""

from __future__ import annotations

from typing import Any

from backtest_engine.broker.fill_simulator import limit_reached, stop_reached
from backtest_engine.context import StrategyContext
from backtest_engine.models import Bar, Order


def update_trailing_order(order: Order, price: float) -> None:
    if (
        order.trail_price is None
        and order.trail_offset is None
        and order.trail_points is None
    ):
        return
    offset = float(order.trail_offset or 0.0)
    if order.direction == "long":
        if not order.trail_activated and (
            order.trail_price is None or price >= order.trail_price
        ):
            order.trail_activated = True
        if order.trail_activated:
            order.stop_price = max(
                order.stop_price if order.stop_price is not None else float("-inf"),
                price - offset,
            )
    else:
        if not order.trail_activated and (
            order.trail_price is None or price <= order.trail_price
        ):
            order.trail_activated = True
        if order.trail_activated:
            order.stop_price = min(
                order.stop_price if order.stop_price is not None else float("inf"),
                price + offset,
            )


def process_bar_fills(
    engine: Any,
    strategy: Any,
    ctx: StrategyContext,
    bar: Bar,
    bar_index: int,
    open_only: bool = False,
    skip_open: bool = False,
    close_activation_only: bool = False,
    skip_trailing: bool = False,
    trailing_only: bool = False,
) -> None:
    if not engine.config.collect_order_lifecycle and len(engine.orders) > 32:
        engine.orders = [
            order for order in engine.orders if order.status in ("pending", "active")
        ]
    recalc = 0
    path = engine._price_path(bar)
    path_cursor = 0
    while True:
        filled = False
        restart_after_recalc = False
        for path_index, (price, point) in enumerate(
            path[path_cursor:], start=path_cursor
        ):
            path_is_open = point == "open" or point.endswith(".open")
            if open_only and not path_is_open:
                continue
            if skip_open and path_is_open:
                continue
            restart_after_recalc, recalc, filled = _scan_orders_at_path_point(
                engine,
                strategy,
                ctx,
                bar,
                bar_index,
                price,
                point,
                path_is_open,
                path_index,
                recalc,
                filled,
                close_activation_only,
                skip_trailing,
                trailing_only,
            )
            if restart_after_recalc:
                path_cursor = path_index
                break
        if restart_after_recalc and path_cursor < len(path):
            continue
        if not (engine.config.calc_on_order_fills and filled):
            break
        break


def _scan_orders_at_path_point(
    engine: Any,
    strategy: Any,
    ctx: StrategyContext,
    bar: Bar,
    bar_index: int,
    price: float,
    point: str,
    path_is_open: bool,
    path_index: int,
    recalc: int,
    filled: bool,
    close_activation_only: bool,
    skip_trailing: bool,
    trailing_only: bool,
) -> tuple[bool, int, bool]:
    for order in list(engine.orders):
        is_trailing = (
            order.trail_price is not None
            or order.trail_offset is not None
            or order.trail_points is not None
        )
        if skip_trailing and is_trailing:
            continue
        if trailing_only and not is_trailing:
            continue
        current_bar_close_activation = (
            engine.config.process_orders_on_close
            and order.created_bar_index == bar_index
        )
        same_bar_close_order = order.created_bar_index == bar_index and (
            engine.config.process_orders_on_close or order.immediately
        )
        if close_activation_only and not same_bar_close_order:
            continue
        if current_bar_close_activation and not (
            point == "close" or point.endswith(".close")
        ):
            continue
        is_close_point = point == "close" or point.endswith(".close")
        if close_activation_only and same_bar_close_order and not is_close_point:
            continue
        if order.status != "active":
            if not (
                order.status == "pending"
                and order.created_bar_index == bar_index
                and order.trail_price is not None
            ):
                continue
            update_trailing_order(order, price)
            continue
        fill_price = _fill_price_for_order(
            engine,
            order,
            bar,
            bar_index,
            price,
            point,
            path_is_open,
        )
        if fill_price is None:
            continue
        engine._fill(order, bar, bar_index, fill_price, point)
        filled = True
        if engine.config.calc_on_order_fills and not current_bar_close_activation:
            recalc = _recalculate_after_fill(
                engine, strategy, ctx, bar, bar_index, fill_price, recalc
            )
            return True, recalc, filled
    if engine._maybe_margin_call(price, bar, bar_index, point):
        filled = True
        if engine.config.calc_on_order_fills:
            recalc = _recalculate_after_fill(
                engine, strategy, ctx, bar, bar_index, price, recalc
            )
            return True, recalc, filled
    return False, recalc, filled


def _fill_price_for_order(
    engine: Any,
    order: Order,
    bar: Bar,
    bar_index: int,
    price: float,
    point: str,
    path_is_open: bool,
) -> float | None:
    was_trail_activated = order.trail_activated
    update_trailing_order(order, price)
    if (
        path_is_open
        and order.trail_price is not None
        and not was_trail_activated
        and order.trail_activated
    ):
        order.stop_price = price
    if (
        order.kind == "exit"
        and order.from_entry is not None
        and not engine._matching_open_trades(order.from_entry)
    ):
        return None
    if order.order_type == "stop" and order.stop_price is None:
        return None
    is_open_point = path_is_open
    is_close_point = point == "close" or point.endswith(".close")
    fill_price = price
    if order.order_type == "market" and (
        (is_open_point and order.created_bar_index < bar_index)
        or (
            engine.config.calc_on_order_fills
            and order.created_bar_index == bar_index
            and order.active_from_bar_index <= bar_index
        )
        or (
            is_close_point
            and (engine.config.process_orders_on_close or order.immediately)
        )
    ):
        return fill_price
    if order.order_type == "limit" and limit_reached(
        order,
        price,
        bar,
        engine.config.mintick,
        engine.config.backtest_fill_limits_assumption_ticks,
    ):
        return engine._limit_fill_price(order, price, is_open_point)
    if order.order_type == "stop" and stop_reached(order, price):
        return _stop_fill_price(engine, order, price, is_open_point, bar_index)
    if order.order_type == "stop_limit":
        if not order.stop_limit_activated and stop_reached(order, price):
            order.stop_limit_activated = True
            engine._event(
                "STOP_LIMIT_ACTIVATED",
                f"stop-limit {order.id} activated",
                bar_index,
                bar.time,
                order.id,
            )
        if not (
            order.stop_limit_activated
            and limit_reached(
                order,
                price,
                bar,
                engine.config.mintick,
                engine.config.backtest_fill_limits_assumption_ticks,
            )
        ):
            return None
        return engine._limit_fill_price(order, price, is_open_point)
    return None


def _stop_fill_price(
    engine: Any, order: Order, price: float, is_open_point: bool, bar_index: int
) -> float:
    if engine.config.stop_gap_fill_policy == "stop_price":
        return order.stop_price or price
    if not is_open_point and not engine.config.fill_worse_stop_at_path_price:
        if order.stop_price is not None and not (
            engine.config.calc_on_order_fills
            and order.created_bar_index == bar_index
            and order.active_from_bar_index <= bar_index
        ):
            return order.stop_price
    return price


def _recalculate_after_fill(
    engine: Any,
    strategy: Any,
    ctx: StrategyContext,
    bar: Bar,
    bar_index: int,
    price: float,
    recalc: int,
) -> int:
    engine._update_open_profit(price)
    engine._update_state()
    recalc += 1
    if recalc > engine.config.max_recalc_depth:
        engine._diag(
            "MAX_RECALC_DEPTH_REACHED",
            "max recalc depth reached",
            "warning",
            bar_index,
            bar.time,
        )
        return recalc
    engine._call_strategy(strategy, bar, bar_index)
    engine._flush(ctx, bar, bar_index, recalc_after_fill=True)
    return recalc
