"""Exit targeting and position-lifetime policies in the existing broker.

A public entry ID is not a lot identity. Relative and all-entry exits are
materialized per actual opening fill. Absolute named exits retain their existing
aggregate quantity behavior; complete FIFO/report attribution is a separate gate.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

from backtest_engine.context.command_buffer import ExitPayload
from backtest_engine.models import Bar, Order, Trade


def matching_trades(engine: Any, from_entry: str | None, lot: int | None = None) -> list[Trade]:
    return [
        trade
        for trade in engine._matching_open_trades(from_entry)
        if lot is None or trade.entry_fill_index == lot
    ]


def reservation_key(order: Order) -> tuple[str, int | None]:
    return (order.parent_exit_id or order.id, order.entry_fill_index)


def reserved_by_trade(engine: Any, exclude_order: Order | None = None) -> dict[int, float]:
    """Reserve each TP/SL bracket once and distribute pooled reservations once.

    Object identities are temporary keys in this one calculation only, never
    persisted or used as checkpoint/run identities.
    """
    groups = {}
    excluded = reservation_key(exclude_order) if exclude_order is not None else None
    for order in engine.orders:
        key = reservation_key(order)
        if order.kind != "exit" or order.status not in {"pending", "active"} or key == excluded:
            continue
        amount = min(order.reserved_qty or order.qty, order.qty)
        if key not in groups or amount > groups[key][1]:
            groups[key] = (order, amount)
    result: dict[int, float] = {}
    # Preserve named-before-unqualified legacy allocation; new all-entry orders
    # have concrete named lot targets and are allocated in command creation order.
    for order, amount in sorted(groups.values(), key=lambda item: item[0].from_entry is None):
        for trade in matching_trades(engine, order.from_entry, order.entry_fill_index):
            key = id(trade)
            used = min(amount, max(0.0, trade.qty - result.get(key, 0.0)))
            result[key] = result.get(key, 0.0) + used
            amount -= used
            if amount <= 0:
                break
    return result


def expire_all_entry_exits(engine: Any) -> None:
    """A flat/reversed position ends persistence, including still-pending entries."""
    engine._all_entry_exits.clear()
    for entry in engine.orders:
        for key, template in tuple(entry.pending_exits.items()):
            if template["payload"]["from_entry"] is None:
                entry.pending_exits.pop(key)


def apply_scoped_exit(
    engine: Any,
    payload: ExitPayload,
    bar: Bar,
    bar_index: int,
    recalc_after_fill: bool,
    limit: float | None,
    stop: float | None,
) -> None:
    from backtest_engine.core.strategy_command_processor import _apply_exit_command

    trades = matching_trades(engine, payload.from_entry)
    if not trades:
        return  # Flat without an existing entry is not a subscription to future positions.
    if payload.from_entry is None:
        engine._all_entry_exits[payload.id] = {
            "payload": asdict(payload),
            "bar_index": bar_index,
            "time": bar.time,
            "direction": engine.position.direction,
        }
    for trade in trades:
        if trade.entry_fill_index is None:
            # Legacy/manually constructed state has no opening fill identity.
            # Deterministic negative IDs cannot collide with native fill indexes.
            used = [
                t.entry_fill_index
                for t in (*engine.open_trades, *engine.closed_trades)
                if t.entry_fill_index is not None
            ]
            trade.entry_fill_index = min([0, *used]) - 1
        _apply_exit_command(
            engine,
            replace(payload, from_entry=trade.entry_id),
            bar,
            bar_index,
            recalc_after_fill,
            limit,
            stop,
            register_pending=False,
            target_trade=trade,
        )


def activate_persistent_exits(engine: Any, entry: Order, bar: Bar, bar_index: int) -> None:
    from backtest_engine.core.strategy_command_processor import _apply_exit_command, _clean_price

    if entry.reduce_only or entry.direction != engine.position.direction:
        return
    new = matching_trades(engine, entry.id, len(engine.fills) - 1)
    for template in tuple(engine._all_entry_exits.values()):
        if template["direction"] != engine.position.direction:
            continue
        payload = ExitPayload(**template["payload"])
        for trade in new:
            if any(
                o.kind == "exit"
                and o.parent_exit_id == payload.id
                and o.entry_fill_index == trade.entry_fill_index
                and o.status in {"pending", "active"}
                for o in engine.orders
            ):
                continue
            before = len(engine.orders)
            _apply_exit_command(
                engine,
                replace(payload, from_entry=entry.id),
                bar,
                bar_index,
                True,
                _clean_price(payload.limit),
                _clean_price(payload.stop),
                register_pending=False,
                target_trade=trade,
            )
            for order in engine.orders[before:]:
                order.created_bar_index = template["bar_index"]
                order.created_time = template["time"]


def reconcile_exit_scope(engine: Any, payload: ExitPayload, bar: Bar, bar_index: int) -> None:
    """A replacement public ID cannot leave an obsolete target or price leg live."""
    scoped = payload.from_entry is None or payload.profit is not None or payload.loss is not None
    for order in engine.orders:
        if (
            order.kind != "exit"
            or order.parent_exit_id != payload.id
            or order.status not in {"pending", "active"}
        ):
            continue
        wrong_mode = scoped != (order.entry_fill_index is not None)
        wrong_entry = payload.from_entry is not None and order.from_entry != payload.from_entry
        trailing = (
            order.trail_price is not None
            or order.trail_points is not None
            or order.trail_offset is not None
        )
        removed_leg = (
            (order.order_type == "limit" and payload.limit is None and payload.profit is None)
            or (
                order.order_type == "stop"
                and not trailing
                and payload.stop is None
                and payload.loss is None
            )
            or (
                trailing
                and payload.trail_price is None
                and payload.trail_points is None
                and payload.trail_offset is None
            )
        )
        if wrong_mode or wrong_entry or removed_leg:
            order.status = "cancelled"
            engine._cb("on_order_cancelled", order)
            engine._event(
                "ORDER_CANCELLED", "exit scope or leg replaced", bar_index, bar.time, order.id
            )
