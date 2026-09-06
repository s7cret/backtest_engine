"""Explicit exits waiting for an already-created entry.

Templates belong to the entry order instance: cancellation/replacement cannot
attach them to a later unrelated order which happens to reuse the same ID.
Activation follows the actual parent fill on the historical price path, including
limit/stop/stop-limit entries. Position-lifetime persistence is owned by exit_scope.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from backtest_engine.context.command_buffer import ExitPayload
from backtest_engine.models import Bar, Order


def remove_deferred_exits(engine: Any, exit_id: str | None = None) -> None:
    """Cancel waiting exit instructions without cancelling their parent entry."""
    for entry in engine.orders:
        if exit_id is None:
            entry.pending_exits.clear()
        else:
            entry.pending_exits.pop(exit_id, None)


def defer_exit(engine: Any, payload: ExitPayload, bar: Bar, bar_index: int) -> bool:
    """Capture only pending orders already created by this callback or earlier."""
    entries = [
        order
        for order in engine.orders
        if (payload.from_entry is None or order.id == payload.from_entry)
        and (payload.from_entry is not None or engine.position.direction == "flat"
             or order.direction == engine.position.direction)
        and order.kind in {"entry", "order"}
        and not order.reduce_only
        and order.status in {"active", "pending"}
    ]
    # Redefining the same public exit ID replaces its previous pending binding.
    remove_deferred_exits(engine, payload.id)
    for entry in entries:
        entry.pending_exits[payload.id] = {
            "payload": asdict(payload),
            "bar_index": bar_index,
            "time": bar.time,
        }
    return bool(entries)


def activate_deferred_exits(engine: Any, entry: Order, bar: Bar, bar_index: int) -> None:
    """Materialize exits only after their specific order opens a matching trade."""
    templates = tuple(entry.pending_exits.values())
    entry.pending_exits.clear()
    if not templates or entry.reduce_only or entry.direction != engine.position.direction:
        return
    # Opposite strategy.order can reduce without opening a new trade.
    matching = engine._matching_open_trades(entry.id)
    if not any(
        trade.entry_bar_index == bar_index
        and trade.entry_time == bar.time
        and trade.direction == entry.direction
        for trade in matching
    ):
        return
    from backtest_engine.core.strategy_command_processor import _apply_exit_command, _clean_price

    for template in templates:
        payload = ExitPayload(**template["payload"])
        before = len(engine.orders)
        _apply_exit_command(
            engine,
            payload,
            bar,
            bar_index,
            True,
            _clean_price(payload.limit),
            _clean_price(payload.stop),
            register_pending=False,
        )
        if payload.from_entry is None and payload.id in engine._all_entry_exits:
            engine._all_entry_exits[payload.id]["bar_index"] = template["bar_index"]
            engine._all_entry_exits[payload.id]["time"] = template["time"]
        # These commands existed before the fill. Preserve their creation clock,
        # so process_orders_on_close cannot delay a previously submitted bracket.
        for order in engine.orders[before:]:
            order.created_bar_index = template["bar_index"]
            order.created_time = template["time"]
