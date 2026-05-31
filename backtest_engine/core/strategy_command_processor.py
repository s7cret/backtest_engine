"""Apply buffered strategy commands to BacktestEngine order state."""

from __future__ import annotations

from typing import Any

from backtest_engine.context import StrategyContext
from backtest_engine.models import Bar, Order


def flush_strategy_commands(
    engine: Any,
    ctx: StrategyContext,
    bar: Bar,
    bar_index: int,
    *,
    recalc_after_fill: bool = False,
) -> None:
    engine._apply_risk_rules(ctx)
    for command in ctx.buffer.drain():
        kind = command.name
        kw = command.kwargs
        if kind == "cancel_all":
            for order in engine.orders:
                if order.status in ("pending", "active"):
                    order.status = "cancelled"
                    engine._cb("on_order_cancelled", order)
                    engine._event(
                        "ORDER_CANCELLED",
                        f"order {order.id} cancelled",
                        bar_index,
                        bar.time,
                        order.id,
                    )
            continue
        if kind == "cancel":
            for order in engine.orders:
                if order.id == kw["id"] and order.status in ("pending", "active"):
                    order.status = "cancelled"
                    engine._cb("on_order_cancelled", order)
                    engine._event(
                        "ORDER_CANCELLED",
                        f"order {order.id} cancelled",
                        bar_index,
                        bar.time,
                        order.id,
                    )
            continue
        if kind in ("close", "close_all"):
            _apply_close_command(engine, kind, kw, bar, bar_index, recalc_after_fill)
            continue

        limit = kw.get("limit")
        stop = kw.get("stop")
        if limit != limit:
            limit = None
        if stop != stop:
            stop = None
        order_type = (
            "market"
            if limit is None and stop is None
            else "limit"
            if stop is None
            else "stop"
            if limit is None
            else "stop_limit"
        )
        if kind == "exit":
            _apply_exit_command(
                engine,
                kw,
                bar,
                bar_index,
                recalc_after_fill,
                limit,
                stop,
            )
            continue

        _apply_entry_or_order_command(
            engine,
            kind,
            kw,
            bar,
            bar_index,
            recalc_after_fill,
            limit,
            stop,
            order_type,
        )


def _apply_close_command(
    engine: Any,
    kind: str,
    kw: dict[str, Any],
    bar: Bar,
    bar_index: int,
    recalc_after_fill: bool,
) -> None:
    if engine.position.direction == "flat":
        return
    from_entry = kw.get("id") if kind == "close" else None
    if kind == "close_all":
        qty = abs(engine.position.size)
    elif kw.get("qty") is None and kw.get("qty_percent") is None and from_entry:
        qty = sum(trade.qty for trade in engine._matching_open_trades(from_entry))
        if qty <= 0:
            engine._diag(
                "ORDER_REJECTED_NO_MATCHING_ENTRY",
                "close has no matching entry id",
                "warning",
                bar_index,
                bar.time,
                from_entry,
            )
            return
    else:
        qty = engine._qty_from_args(kw, engine.position.size, bar.close)
    engine._add_order(
        Order(
            id=kw.get("id", "close_all"),
            kind="close",
            direction=engine.position.direction,
            side="sell" if engine.position.direction == "long" else "buy",
            position_effect="close",
            order_type="market",
            qty=qty,
            created_bar_index=bar_index,
            created_time=bar.time,
            active_from_bar_index=bar_index
            if (kw.get("immediately") or engine.config.process_orders_on_close or recalc_after_fill)
            else bar_index + 1,
            position_direction=engine.position.direction,
            reduce_only=True,
            from_entry=from_entry,
            comment=kw.get("comment"),
            immediately=kw.get("immediately", False),
        ),
        bar,
        bar_index,
    )


def _apply_exit_command(
    engine: Any,
    kw: dict[str, Any],
    bar: Bar,
    bar_index: int,
    recalc_after_fill: bool,
    limit: float | None,
    stop: float | None,
) -> None:
    if engine.position.direction == "flat":
        engine._diag(
            "ORDER_REJECTED_NO_AVAILABLE_POSITION_QTY",
            "exit without position",
            "warning",
            bar_index,
            bar.time,
            kw["id"],
        )
        return
    direction = engine.position.direction
    side = "sell" if direction == "long" else "buy"
    qty = engine._qty_from_args(kw, engine.position.size, bar.close)
    from_entry = kw.get("from_entry")
    available = engine._available_exit_qty(from_entry)
    if available <= 0:
        engine._diag(
            "ORDER_REJECTED_NO_AVAILABLE_POSITION_QTY",
            "exit has no matching unreserved position qty",
            "warning",
            bar_index,
            bar.time,
            kw["id"],
        )
        return
    qty = min(qty, available)
    base = engine._exit_base_price(from_entry)
    if kw.get("profit") is not None and limit is None:
        limit = base + float(kw["profit"]) if direction == "long" else base - float(kw["profit"])
    if kw.get("loss") is not None and stop is None:
        stop = base - float(kw["loss"]) if direction == "long" else base + float(kw["loss"])
    has_trail = (
        kw.get("trail_price") is not None
        or kw.get("trail_points") is not None
        or kw.get("trail_offset") is not None
    )
    if limit is None and stop is None and not has_trail:
        engine._diag(
            "ORDER_REJECTED_EMPTY_EXIT",
            "exit has no active legs",
            "warning",
            bar_index,
            bar.time,
            kw["id"],
        )
        return
    oca = kw.get("oca_name") or kw["id"]
    if limit is not None:
        engine._add_order(
            Order(
                id=kw["id"] + ":L",
                kind="exit",
                direction=direction,
                side=side,
                position_effect="reduce",
                order_type="limit",
                qty=qty,
                created_bar_index=bar_index,
                created_time=bar.time,
                active_from_bar_index=bar_index if recalc_after_fill else bar_index + 1,
                position_direction=direction,
                reduce_only=True,
                limit_price=limit,
                from_entry=from_entry,
                oca_name=oca,
                oca_type="reduce",
                reserved_qty=qty,
                parent_exit_id=kw["id"],
                comment=kw.get("comment"),
            ),
            bar,
            bar_index,
        )
    if stop is not None:
        engine._add_order(
            Order(
                id=kw["id"] + ":S",
                kind="exit",
                direction=direction,
                side=side,
                position_effect="reduce",
                order_type="stop",
                qty=qty,
                created_bar_index=bar_index,
                created_time=bar.time,
                active_from_bar_index=bar_index if recalc_after_fill else bar_index + 1,
                position_direction=direction,
                reduce_only=True,
                stop_price=stop,
                from_entry=from_entry,
                oca_name=oca,
                oca_type="reduce",
                reserved_qty=qty,
                parent_exit_id=kw["id"],
                comment=kw.get("comment"),
            ),
            bar,
            bar_index,
        )
    if has_trail:
        points = kw.get("trail_points")
        activation = kw.get("trail_price")
        tick = engine._effective_mintick or 1.0
        points_price = float(points) * tick if points is not None else None
        if activation is None and points_price is not None:
            activation = base + points_price if direction == "long" else base - points_price
        offset = (
            float(
                kw.get("trail_offset")
                if kw.get("trail_offset") is not None
                else (points if points is not None else 0.0)
            )
            * tick
        )
        engine._add_order(
            Order(
                id=kw["id"] + ":T",
                kind="exit",
                direction=direction,
                side=side,
                position_effect="reduce",
                order_type="stop",
                qty=qty,
                created_bar_index=bar_index,
                created_time=bar.time,
                active_from_bar_index=bar_index if recalc_after_fill else bar_index + 1,
                position_direction=direction,
                reduce_only=True,
                stop_price=None,
                from_entry=from_entry,
                oca_name=oca,
                oca_type="reduce",
                reserved_qty=qty,
                parent_exit_id=kw["id"],
                comment=kw.get("comment"),
                trail_price=activation,
                trail_points=points_price,
                trail_offset=offset,
            ),
            bar,
            bar_index,
        )


def _apply_entry_or_order_command(
    engine: Any,
    kind: str,
    kw: dict[str, Any],
    bar: Bar,
    bar_index: int,
    recalc_after_fill: bool,
    limit: float | None,
    stop: float | None,
    order_type: str,
) -> None:
    direction = kw["direction"]
    side = "buy" if direction == "long" else "sell"
    uses_default_qty = kw.get("qty") is None and kw.get("qty_percent") is None
    qty = engine._qty_from_args(kw, None, bar.close)
    if kind == "entry" and not engine._entry_direction_allowed(direction):
        if engine.position.direction != "flat" and engine.position.direction != direction:
            close_direction = engine.position.direction
            close_side = "sell" if close_direction == "long" else "buy"
            engine._add_order(
                Order(
                    id=kw["id"],
                    kind="close",
                    direction=close_direction,
                    side=close_side,
                    position_effect="close",
                    order_type=order_type,
                    qty=min(qty, abs(engine.position.size)),
                    created_bar_index=bar_index,
                    created_time=bar.time,
                    active_from_bar_index=bar_index
                    if (engine.config.process_orders_on_close or recalc_after_fill)
                    else bar_index + 1,
                    position_direction=close_direction,
                    reduce_only=True,
                    limit_price=limit,
                    stop_price=stop,
                    comment=kw.get("comment"),
                ),
                bar,
                bar_index,
            )
            return
        engine._diag(
            "ORDER_REJECTED_RISK_ALLOW_ENTRY_IN",
            "entry rejected by risk.allow_entry_in",
            "warning",
            bar_index,
            bar.time,
            kw["id"],
        )
        return
    effect = "open"
    if (
        kind == "entry"
        and engine.position.direction != "flat"
        and engine.position.direction != direction
        and engine.config.reverse_on_opposite_entry
    ):
        effect = "reverse"
        qty = abs(engine.position.size) + qty
    if kind == "entry" and not engine._entry_allowed(direction):
        engine._diag(
            "ORDER_REJECTED_PYRAMIDING",
            "pyramiding limit reached",
            "warning",
            bar_index,
            bar.time,
            kw["id"],
        )
        return
    existing = next(
        (
            order
            for order in engine.orders
            if order.id == kw["id"] and order.kind == kind and order.status in ("pending", "active")
        ),
        None,
    )
    new = Order(
        kw["id"],
        kind,
        direction,
        side,
        effect,
        order_type,
        qty,
        bar_index,
        bar.time,
        bar_index
        if (engine.config.process_orders_on_close or recalc_after_fill)
        else bar_index + 1,
        direction,
        False,
        limit,
        stop,
        None,
        kw.get("oca_name"),
        kw.get("oca_type") or "none",
        comment=kw.get("comment"),
    )
    new.qty_is_default = uses_default_qty
    if existing:
        if not engine._risk_allows_order(new, bar, bar_index, existing):
            return
        existing.qty = new.qty
        existing.limit_price = new.limit_price
        existing.stop_price = new.stop_price
        existing.order_type = new.order_type
        engine._event(
            "ORDER_MODIFIED",
            f"order {existing.id} modified",
            bar_index,
            bar.time,
            existing.id,
        )
    else:
        engine._add_order(new, bar, bar_index)
