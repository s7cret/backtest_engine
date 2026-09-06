"""Scan bar price paths and trigger active orders."""

from __future__ import annotations

from typing import Any, Literal

from backtest_engine.broker.fill_simulator import limit_reached, stop_reached
from backtest_engine.context import StrategyContext
from backtest_engine.errors import StrategyRuntimeError
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
    tick_phase: Literal["non_final", "final"] | None = None,
) -> None:
    if tick_phase == "non_final" and engine.config.fill_model == "close_only":
        return
    if not engine.config.collect_order_lifecycle and len(engine.orders) > 32:
        engine.orders = [
            order for order in engine.orders if order.status in ("pending", "active")
        ]
    from backtest_engine.core.price_events import next_price_event

    recalc = 0
    path = engine._price_path(bar)
    previous_price = None
    for path_index, (destination, destination_point) in enumerate(path):
        is_open = destination_point == "open" or destination_point.endswith(".open")
        chart_open = is_open and path_index == 0
        if ((open_only and not chart_open) or (skip_open and chart_open)
                or (close_activation_only and path_index != len(path) - 1)):
            previous_price = destination
            continue
        # A gap between bars (also magnifier bars) has no intermediate prices.
        # Observed ticks and close-only executions must not invent a price path.
        interpolate = (previous_price is not None and not is_open and not open_only
                       and not close_activation_only and tick_phase is None
                       and not getattr(engine, "_realtime_tick_execution", False))
        cursor = previous_price if interpolate else destination
        while True:
            price = (next_price_event(engine, cursor, destination, bar_index)
                     if interpolate else destination)
            at_endpoint = price == destination
            point = destination_point if at_endpoint else destination_point + ".cross"
            activation_ids: set[int] = set()
            while True:
                known_orders = {id(order) for order in engine.orders if order.status == "active"}
                restart, recalc, _ = _scan_orders_at_path_point(
                    engine, strategy, ctx, bar, bar_index, price, point,
                    is_open and at_endpoint, path_index, recalc, False,
                    close_activation_only, skip_trailing or not at_endpoint,
                    trailing_only, tick_phase, activation_ids,
                )
                if not restart:
                    break
                # A newly attached/created marketable price order starts here,
                # not at an earlier price on the segment. It can get improvement.
                activation_ids.update(id(order) for order in engine.orders
                                      if id(order) not in known_orders)
            cursor = price
            if at_endpoint:
                break
        previous_price = destination


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
    tick_phase: Literal["non_final", "final"] | None,
    activation_ids: set[int] | None = None,
) -> tuple[bool, int, bool]:
    for order in _orders_for_path_point(engine, price, bar, path_is_open):
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
        if current_bar_close_activation and tick_phase == "non_final":
            continue
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
            continue
        fill_price = _fill_price_for_order(
            engine,
            order,
            bar,
            bar_index,
            price,
            point,
            path_is_open or (activation_ids is not None and id(order) in activation_ids),
        )
        if fill_price is None:
            continue
        had_deferred_exits = bool(order.pending_exits)
        engine._fill(order, bar, bar_index, fill_price, point)
        if order.status != "filled":
            continue
        filled = True
        # A close-activated fill may trigger one same-close recalculation.  Do
        # not recursively rescan further orders created by that recalculation
        # as fresh close recalculations on the identical price point.
        if engine.config.calc_on_order_fills and (
            not current_bar_close_activation or recalc == 0
        ):
            recalc = _recalculate_after_fill(
                engine, strategy, ctx, bar, bar_index, fill_price, recalc
            )
            return True, recalc, filled
        if had_deferred_exits:
            # Their commands predate this entry fill. Newly materialized brackets
            # must be considered at this same price point, even when script fill
            # recalculation is disabled (e.g. entry opens beyond an absolute TP).
            # The filled parent is terminal, so it cannot restart the scan twice.
            return True, recalc, filled
    if engine._maybe_margin_call(price, bar, bar_index, point):
        filled = True
        if engine.config.calc_on_order_fills:
            recalc = _recalculate_after_fill(
                engine, strategy, ctx, bar, bar_index, price, recalc
            )
            return True, recalc, filled
    return False, recalc, filled


def _orders_for_path_point(engine: Any, price: float, bar: Bar, path_is_open: bool) -> list[Order]:
    orders = list(engine.orders)
    if not path_is_open:
        return orders

    mintick = engine.config.mintick
    assumption_ticks = engine.config.backtest_fill_limits_assumption_ticks

    def key(item: tuple[int, Order]) -> tuple[int, float, int]:
        idx, order = item
        if (
            order.status == "active"
            and order.kind == "exit"
            and order.order_type == "limit"
            and order.limit_price is not None
            and limit_reached(order, price, bar, mintick, assumption_ticks)
        ):
            # At an open gap beyond multiple exit limits, TV lists/fills the
            # more favorable target first. Normal intrabar path crossings keep
            # creation order because price reaches nearer targets first.
            favored_price = -order.limit_price if order.side == "sell" else order.limit_price
            return (0, favored_price, idx)
        return (1, 0.0, idx)

    return [order for _, order in sorted(enumerate(orders), key=key)]


def _fill_price_for_order(
    engine: Any,
    order: Order,
    bar: Bar,
    bar_index: int,
    price: float,
    point: str,
    path_is_open: bool,
) -> float | None:
    update_trailing_order(order, price)
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
            (engine.config.calc_on_order_fills or engine.config.calc_on_every_tick)
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
        newly_activated = not order.stop_limit_activated
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
        return engine._limit_fill_price(order, price, is_open_point or newly_activated)
    return None


def _stop_fill_price(
    engine: Any, order: Order, price: float, is_open_point: bool, bar_index: int
) -> float:
    if engine.config.stop_gap_fill_policy == "stop_price":
        return order.stop_price or price
    if not is_open_point and not engine.config.fill_worse_stop_at_path_price:
        if order.stop_price is not None and not (
            (engine.config.calc_on_order_fills or engine.config.calc_on_every_tick)
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
    if getattr(engine, "_recalc_budget_bar", None) != bar_index:
        engine._recalc_budget_bar, engine._recalc_budget_used = bar_index, 0
    engine._recalc_budget_used += 1
    if engine._recalc_budget_used > engine.config.max_recalc_depth:
        engine._diag(
            "MAX_RECALC_DEPTH_REACHED",
            "max recalculation budget reached for the chart bar",
            "error",
            bar_index,
            bar.time,
        )
        raise StrategyRuntimeError("MAX_RECALC_DEPTH_REACHED: required fill recalculation was not executed")
    prepare = getattr(engine, "_prepare_realtime_strategy_invocation", None)
    if getattr(engine, "_realtime_tick_execution", False) and callable(prepare):
        bar = prepare(strategy)
    engine._call_strategy(strategy, bar, bar_index, fill_cause=True)
    engine._flush(ctx, bar, bar_index, recalc_after_fill=True)
    return recalc
