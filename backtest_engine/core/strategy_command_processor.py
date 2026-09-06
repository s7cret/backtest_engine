"""Apply buffered strategy commands to BacktestEngine order state."""

from __future__ import annotations

from typing import Any, Literal, cast

from backtest_engine.context import (
    CancelPayload,
    ClosePayload,
    EntryOrderPayload,
    ExitPayload,
    StrategyContext,
)
from backtest_engine.models import Bar, Order, Trade
from backtest_engine.core.order_metadata import execution_metadata, copy_order_metadata

EntryCommandKind = Literal["entry", "order"]
OrderDirection = Literal["long", "short"]
OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop", "stop_limit"]
PositionEffect = Literal["open", "reduce", "close", "reverse"]
OcaType = Literal["cancel", "reduce", "none"]


def flush_strategy_commands(
    engine: Any,
    ctx: StrategyContext,
    bar: Bar,
    bar_index: int,
    *,
    recalc_after_fill: bool = False,
) -> None:
    from backtest_engine.core.deferred_exits import remove_deferred_exits

    engine._apply_risk_rules(ctx)
    for command in ctx.buffer.drain():
        kind = command.name
        payload = command.payload
        if kind == "cancel_all":
            engine._all_entry_exits.clear()
            remove_deferred_exits(engine)
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
            engine._all_entry_exits.pop(payload.id, None)
            remove_deferred_exits(engine, payload.id)
            for order in engine.orders:
                if (order.id == payload.id or order.parent_exit_id == payload.id) and order.status in ("pending", "active"):
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
            _apply_close_command(
                engine, kind, payload, bar, bar_index, recalc_after_fill
            )
            continue

        assert isinstance(payload, EntryOrderPayload | ExitPayload)
        limit = _clean_price(payload.limit)
        stop = _clean_price(payload.stop)
        order_type: OrderType = (
            "market"
            if limit is None and stop is None
            else "limit" if stop is None else "stop" if limit is None else "stop_limit"
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
            cast(EntryCommandKind, kind),
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


def _qty_args(
    qty: float | None, qty_percent: float | None = None
) -> dict[str, float | None]:
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
    available = sum(trade.qty for trade in engine._matching_open_trades(from_entry))
    if available <= 0:
        engine._diag(
            "ORDER_REJECTED_NO_MATCHING_ENTRY", "close has no matching entry id", "warning",
            bar_index, bar.time, from_entry,
        )
        return
    if kind == "close_all" or (payload.qty is None and payload.qty_percent is None):
        qty = available
    else:
        qty = engine._qty_from_args(
            _qty_args(payload.qty, payload.qty_percent), available, bar.close,
        )
    qty = min(qty, available)
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
            active_from_bar_index=(
                bar_index
                if (
                    payload.immediately
                    or engine.config.process_orders_on_close
                    or recalc_after_fill
                )
                else bar_index + 1
            ),
            position_direction=engine.position.direction,
            reduce_only=True,
            from_entry=from_entry,
            **execution_metadata(payload),
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
    *,
    register_pending: bool = True,
    target_trade: Trade | None = None,
) -> None:
    from backtest_engine.core.deferred_exits import defer_exit

    from backtest_engine.core.exit_scope import apply_scoped_exit, matching_trades, reconcile_exit_scope

    if register_pending:
        reconcile_exit_scope(engine, payload, bar, bar_index)
    if register_pending and payload.from_entry is not None:
        engine._all_entry_exits.pop(payload.id, None)
    waiting = defer_exit(engine, payload, bar, bar_index) if register_pending else False
    if target_trade is None and (payload.from_entry is None or payload.profit is not None or payload.loss is not None or any(
        v is not None for v in (payload.trail_price, payload.trail_points, payload.trail_offset))):
        apply_scoped_exit(engine, payload, bar, bar_index, recalc_after_fill, limit, stop)
        return
    if waiting and not engine._matching_open_trades(payload.from_entry):
        return
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
    side: OrderSide = "sell" if direction == "long" else "buy"
    from_entry = payload.from_entry
    lot = None if target_trade is None else target_trade.entry_fill_index
    if _exit_id_already_filled_for_open_entry(engine, payload.id, from_entry, lot):
        return
    existing_exit = next(
        (
            order
            for order in engine.orders
            if order.kind == "exit"
            and order.status in ("pending", "active")
            and (order.parent_exit_id or order.id) == payload.id
            and order.entry_fill_index == lot
        ),
        None,
    )
    available = engine._available_exit_qty(from_entry, exclude_order=existing_exit,
        **({"entry_fill_index": lot} if lot is not None else {}))
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
    if payload.qty is None and payload.qty_percent is None:
        qty = available
    else:
        qty = engine._qty_from_args(
            _qty_args(payload.qty, payload.qty_percent),
            sum(trade.entry_qty for trade in matching_trades(engine, from_entry, lot)),
            bar.close,
        )
    qty = min(qty, available)
    base = engine._exit_base_price(from_entry) if target_trade is None else target_trade.entry_price
    # Stable labels keep the existing first bracket IDs. Additional simultaneous
    # lots carry a suffix; the scope fields, not this label, control execution.
    prefix = payload.id
    if existing_exit is not None:
        prefix = existing_exit.id.rsplit(":", 1)[0]
    elif target_trade is not None and any(
        o.kind == "exit" and o.status in {"pending", "active"}
        and (o.parent_exit_id or o.id) == payload.id
        for o in engine.orders
    ):
        prefix = f"{payload.id}:entry:{lot}"
    tick = getattr(engine, "_effective_mintick", 1.0) or 1.0
    from backtest_engine.core.exit_prices import resolve_exit_prices

    limit, stop = resolve_exit_prices(
        direction=direction, entry_price=base, mintick=tick,
        limit=limit, stop=stop, profit=payload.profit, loss=payload.loss,
        policy=payload.price_pair_policy,
    )
    from backtest_engine.core.exit_prices import resolve_trailing_prices
    trailing = resolve_trailing_prices(
        direction=direction, entry_price=base, mintick=tick,
        trail_price=payload.trail_price, trail_points=payload.trail_points,
        trail_offset=payload.trail_offset, policy=payload.price_pair_policy,
    )
    has_trail = trailing is not None
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
        _add_or_modify_exit_order(
            engine,
            Order(
                id=prefix + ":L",
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
                entry_fill_index=lot,
                oca_explicit=payload.oca_name is not None,
                **execution_metadata(payload, "profit"),
            ),
            bar,
            bar_index,
        )
    if stop is not None:
        _add_or_modify_exit_order(
            engine,
            Order(
                id=prefix + ":S",
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
                entry_fill_index=lot,
                oca_explicit=payload.oca_name is not None,
                **execution_metadata(payload, "loss"),
            ),
            bar,
            bar_index,
        )
    if trailing is not None:
        activation, offset = trailing
        points_price = None  # Already resolved at this specific entry fill.
        _add_or_modify_exit_order(
            engine,
            Order(
                id=prefix + ":T",
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
                entry_fill_index=lot,
                oca_explicit=payload.oca_name is not None,
                **execution_metadata(payload, "trailing"),
                trail_price=activation,
                trail_points=points_price,
                trail_offset=offset,
            ),
            bar,
            bar_index,
        )


def _exit_id_already_filled_for_open_entry(
    engine: Any, exit_id: str, from_entry: str | None, lot: int | None = None
) -> bool:
    cache = getattr(engine, "_filled_exit_entry_keys", None)
    if cache is None:
        cache = {
            (
                trade.exit_parent_id or trade.exit_id.split(":", 1)[0],
                trade.entry_id,
                trade.entry_time,
                trade.entry_bar_index,
                trade.entry_fill_index,
            )
            for trade in engine.closed_trades
            if trade.exit_id is not None
        }
        engine._filled_exit_entry_keys = cache
    from backtest_engine.core.exit_scope import matching_trades
    for trade in matching_trades(engine, from_entry, lot):
        if (exit_id, trade.entry_id, trade.entry_time, trade.entry_bar_index, trade.entry_fill_index) in cache:
            return True
    return False


def _add_or_modify_exit_order(
    engine: Any, new: Order, bar: Bar, bar_index: int
) -> None:
    existing = next(
        (
            order
            for order in engine.orders
            if order.id == new.id
            and (new.parent_exit_id is None or order.parent_exit_id == new.parent_exit_id)
            and order.entry_fill_index == new.entry_fill_index
            and order.kind == "exit"
            and order.status in ("pending", "active")
        ),
        None,
    )
    if existing is None:
        engine._add_order(new, bar, bar_index)
        return
    if new.qty <= 0:
        engine._diag(
            "ORDER_REJECTED_ZERO_QTY",
            "order qty is zero",
            "warning",
            bar_index,
            bar.time,
            new.id,
        )
        return
    if not engine._risk_allows_order(new, bar, bar_index, existing):
        return
    was_trailing = existing.trail_offset is not None
    is_trailing = new.trail_offset is not None
    if was_trailing and is_trailing:
        new.trail_activated = existing.trail_activated
        new.trail_best_price = existing.trail_best_price
        if new.trail_best_price is None and existing.stop_price is not None:
            new.trail_best_price = (existing.stop_price + existing.trail_offset
                                   if existing.direction == "long"
                                   else existing.stop_price - existing.trail_offset)
        if new.trail_activated and new.trail_best_price is not None:
            new.stop_price = (new.trail_best_price - new.trail_offset
                              if new.direction == "long"
                              else new.trail_best_price + new.trail_offset)
    existing.trail_activated = new.trail_activated
    existing.trail_best_price = new.trail_best_price
    existing.qty = new.qty
    existing.reserved_qty = new.reserved_qty
    existing.limit_price = new.limit_price
    existing.stop_price = new.stop_price
    existing.trail_price = new.trail_price
    existing.trail_points = new.trail_points
    existing.trail_offset = new.trail_offset
    existing.order_type = new.order_type
    existing.direction = new.direction
    existing.side = new.side
    existing.position_direction = new.position_direction
    existing.from_entry = new.from_entry
    existing.oca_name = new.oca_name
    existing.oca_explicit = new.oca_explicit
    existing.entry_fill_index = new.entry_fill_index
    existing.oca_type = new.oca_type
    existing.parent_exit_id = new.parent_exit_id
    copy_order_metadata(existing, new)
    existing.created_bar_index = new.created_bar_index
    existing.created_time = new.created_time
    existing.active_from_bar_index = new.active_from_bar_index
    existing.status = "active" if new.active_from_bar_index <= bar_index else "pending"
    engine._event(
        "ORDER_MODIFIED",
        f"exit order {existing.id} modified",
        bar_index,
        bar.time,
        existing.id,
    )


def _apply_entry_or_order_command(
    engine: Any,
    kind: EntryCommandKind,
    payload: EntryOrderPayload,
    bar: Bar,
    bar_index: int,
    recalc_after_fill: bool,
    limit: float | None,
    stop: float | None,
    order_type: OrderType,
) -> None:
    direction = cast(OrderDirection, payload.direction)
    side: OrderSide = "buy" if direction == "long" else "sell"
    uses_default_qty = payload.qty is None
    qty = engine._qty_from_args(_qty_args(payload.qty), None, bar.close)
    if kind == "entry" and not engine._entry_direction_allowed(direction):
        if (
            engine.position.direction != "flat"
            and engine.position.direction != direction
        ):
            close_direction = cast(OrderDirection, engine.position.direction)
            close_side: OrderSide = "sell" if close_direction == "long" else "buy"
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
                    active_from_bar_index=(
                        bar_index
                        if (engine.config.process_orders_on_close or recalc_after_fill)
                        else bar_index + 1
                    ),
                    position_direction=close_direction,
                    reduce_only=True,
                    limit_price=limit,
                    stop_price=stop,
                    **execution_metadata(payload),
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
    effect: PositionEffect = "open"
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
        (
            bar_index
            if (engine.config.process_orders_on_close or recalc_after_fill)
            else bar_index + 1
        ),
        direction,
        False,
        limit,
        stop,
        None,
        payload.oca_name,
        cast(OcaType, payload.oca_type or "none"),
        **execution_metadata(payload),
    )
    new.qty_is_default = uses_default_qty
    if existing:
        if new.qty <= 0:
            # Pine semantic for strategy.entry(...qty=0) replacement:
            # cancel the existing order only while it is still pending AND
            # its fill window is in the future. Once a MARKET order has
            # reached its activation bar (active_from_bar_index <= bar_index)
            # it has entered its fill window and Pine leaves it alone — the
            # only way to abort an already-queued market entry is an explicit
            # strategy.cancel call. This guards the SOL/ADA daily parity bug
            # where orders created on the signal bar were cancelled on the
            # next bar when the strategy repeated strategy.entry with a
            # conditional qty=lot?0:0.
            #
            # STOP orders are different: they may sit unfilled for many bars
            # waiting for price to reach the stop level. A qty=0 replacement
            # cancels them even on the activation bar, matching TradingView
            # semantics where strategy.entry(id, dir, 0, stop=X) replaces the
            # pending stop order with nothing.
            is_unfilled_stop = existing.order_type in ("stop", "stop_limit")
            if existing.active_from_bar_index > bar_index or is_unfilled_stop:
                existing.status = "cancelled"
                engine._event(
                    "ORDER_CANCELLED",
                    f"order {existing.id} cancelled by zero-qty replacement",
                    bar_index,
                    bar.time,
                    existing.id,
                )
                return
            # Order already in its fill window: keep it so it can fill.
            return
        if not engine._risk_allows_order(new, bar, bar_index, existing):
            return
        existing.qty = new.qty
        existing.limit_price = new.limit_price
        existing.stop_price = new.stop_price
        existing.order_type = new.order_type
        existing.direction = new.direction
        existing.side = new.side
        existing.position_effect = new.position_effect
        existing.position_direction = new.position_direction
        existing.oca_name = new.oca_name
        existing.oca_type = new.oca_type
        copy_order_metadata(existing, new)
        existing.qty_is_default = new.qty_is_default
        existing.created_bar_index = new.created_bar_index
        existing.created_time = new.created_time
        existing.active_from_bar_index = new.active_from_bar_index
        existing.status = (
            "active" if new.active_from_bar_index <= bar_index else "pending"
        )
        engine._event(
            "ORDER_MODIFIED",
            f"order {existing.id} modified",
            bar_index,
            bar.time,
            existing.id,
        )
        return
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
