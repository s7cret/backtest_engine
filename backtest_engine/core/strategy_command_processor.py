"""Apply buffered strategy commands to BacktestEngine order state."""

from __future__ import annotations

from typing import Any

from backtest_engine.context import (
    CancelPayload,
    ClosePayload,
    EntryOrderPayload,
    ExitPayload,
    StrategyContext,
)
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
        payload = command.payload
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
            assert isinstance(payload, CancelPayload)
            for order in engine.orders:
                if order.id == payload.id and order.status in ("pending", "active"):
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
            assert isinstance(payload, ClosePayload)
            _apply_close_command(engine, kind, payload, bar, bar_index, recalc_after_fill)
            continue

        assert isinstance(payload, EntryOrderPayload | ExitPayload)
        limit = _clean_price(payload.limit)
        stop = _clean_price(payload.stop)
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
            assert isinstance(payload, ExitPayload)
            _apply_exit_command(
                engine,
                payload,
                bar,
                bar_index,
                recalc_after_fill,
                limit,
                stop,
            )
            continue

        assert isinstance(payload, EntryOrderPayload)
        _apply_entry_or_order_command(
            engine,
            kind,
            payload,
            bar,
            bar_index,
            recalc_after_fill,
            limit,
            stop,
            order_type,
        )


def _clean_price(value: float | None) -> float | None:
    if value != value:
        return None
    return value


def _qty_args(qty: float | None, qty_percent: float | None = None) -> dict[str, float | None]:
    return {"qty": qty, "qty_percent": qty_percent}


def _apply_close_command(
    engine: Any,
    kind: str,
    payload: ClosePayload,
    bar: Bar,
    bar_index: int,
    recalc_after_fill: bool,
) -> None:
    if engine.position.direction == "flat":
        return
    from_entry = payload.id if kind == "close" else None
    if kind == "close_all":
        qty = abs(engine.position.size)
    elif payload.qty is None and payload.qty_percent is None and from_entry:
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
        qty = engine._qty_from_args(
            _qty_args(payload.qty, payload.qty_percent),
            engine.position.size,
            bar.close,
        )
    engine._add_order(
        Order(
            id=payload.id or "close_all",
            kind="close",
            direction=engine.position.direction,
            side="sell" if engine.position.direction == "long" else "buy",
            position_effect="close",
            order_type="market",
            qty=qty,
            created_bar_index=bar_index,
            created_time=bar.time,
            active_from_bar_index=bar_index
            if (payload.immediately or engine.config.process_orders_on_close or recalc_after_fill)
            else bar_index + 1,
            position_direction=engine.position.direction,
            reduce_only=True,
            from_entry=from_entry,
            comment=payload.comment,
            immediately=payload.immediately,
        ),
        bar,
        bar_index,
    )


def _apply_exit_command(
    engine: Any,
    payload: ExitPayload,
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
            payload.id,
        )
        return
    direction = engine.position.direction
    side = "sell" if direction == "long" else "buy"
    qty = engine._qty_from_args(
        _qty_args(payload.qty, payload.qty_percent),
        engine.position.size,
        bar.close,
    )
    from_entry = payload.from_entry
    available = engine._available_exit_qty(from_entry)
    if available <= 0:
        engine._diag(
            "ORDER_REJECTED_NO_AVAILABLE_POSITION_QTY",
            "exit has no matching unreserved position qty",
            "warning",
            bar_index,
            bar.time,
            payload.id,
        )
        return
    qty = min(qty, available)
    base = engine._exit_base_price(from_entry)
    if payload.profit is not None and limit is None:
        limit = (
            base + float(payload.profit) if direction == "long" else base - float(payload.profit)
        )
    if payload.loss is not None and stop is None:
        stop = base - float(payload.loss) if direction == "long" else base + float(payload.loss)
    has_trail = (
        payload.trail_price is not None
        or payload.trail_points is not None
        or payload.trail_offset is not None
    )
    if limit is None and stop is None and not has_trail:
        engine._diag(
            "ORDER_REJECTED_EMPTY_EXIT",
            "exit has no active legs",
            "warning",
            bar_index,
            bar.time,
            payload.id,
        )
        return
    oca = payload.oca_name or payload.id
    if limit is not None:
        engine._add_order(
            Order(
                id=payload.id + ":L",
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
                parent_exit_id=payload.id,
                comment=payload.comment,
            ),
            bar,
            bar_index,
        )
    if stop is not None:
        engine._add_order(
            Order(
                id=payload.id + ":S",
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
                parent_exit_id=payload.id,
                comment=payload.comment,
            ),
            bar,
            bar_index,
        )
    if has_trail:
        points = payload.trail_points
        activation = payload.trail_price
        tick = engine._effective_mintick or 1.0
        points_price = float(points) * tick if points is not None else None
        if activation is None and points_price is not None:
            activation = base + points_price if direction == "long" else base - points_price
        offset = (
            float(
                payload.trail_offset
                if payload.trail_offset is not None
                else (points if points is not None else 0.0)
            )
            * tick
        )
        engine._add_order(
            Order(
                id=payload.id + ":T",
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
                parent_exit_id=payload.id,
                comment=payload.comment,
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
    payload: EntryOrderPayload,
    bar: Bar,
    bar_index: int,
    recalc_after_fill: bool,
    limit: float | None,
    stop: float | None,
    order_type: str,
) -> None:
    direction = payload.direction
    side = "buy" if direction == "long" else "sell"
    uses_default_qty = payload.qty is None
    qty = engine._qty_from_args(_qty_args(payload.qty), None, bar.close)
    if kind == "entry" and not engine._entry_direction_allowed(direction):
        if engine.position.direction != "flat" and engine.position.direction != direction:
            close_direction = engine.position.direction
            close_side = "sell" if close_direction == "long" else "buy"
            engine._add_order(
                Order(
                    id=payload.id,
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
                    comment=payload.comment,
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
            payload.id,
        )
        return
    effect = "open"
    if (
        kind == "entry"
        and engine.position.direction != "flat"
        and engine.position.direction != direction
        and engine.config.reverse_on_opposite_entry
    ):
        if _pending_full_close_for_current_position(engine):
            effect = "open"
        else:
            effect = "reverse"
            qty = abs(engine.position.size) + qty
    if kind == "entry" and not engine._entry_allowed(direction):
        engine._diag(
            "ORDER_REJECTED_PYRAMIDING",
            "pyramiding limit reached",
            "warning",
            bar_index,
            bar.time,
            payload.id,
        )
        return
    existing = next(
        (
            order
            for order in engine.orders
            if order.id == payload.id
            and order.kind == kind
            and order.status in ("pending", "active")
        ),
        None,
    )
    new = Order(
        payload.id,
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
        payload.oca_name,
        payload.oca_type or "none",
        comment=payload.comment,
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


def _pending_full_close_for_current_position(engine: Any) -> bool:
    if engine.position.direction == "flat":
        return False
    pending_qty = sum(
        order.qty
        for order in engine.orders
        if order.kind == "close"
        and order.status in {"pending", "active"}
        and order.position_direction == engine.position.direction
    )
    return pending_qty >= abs(engine.position.size) - 1e-12
