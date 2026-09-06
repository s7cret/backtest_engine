"""Explicit exits waiting for an already-created market entry.

Templates belong to the entry order instance: cancellation/replacement cannot
attach them to a later unrelated order which happens to reuse the same ID.
Only market-entry deferral is admitted until continuous price-segment activation
is implemented for limit/stop entries. This is not all-entry exit persistence.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from backtest_engine.context.command_buffer import ExitPayload
from backtest_engine.errors import StrategyRuntimeError
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
    if payload.from_entry is None:
        return False
    entries = [
        order
        for order in engine.orders
        if order.id == payload.from_entry
        and order.kind in {"entry", "order"}
        and not order.reduce_only
        and order.status in {"active", "pending"}
    ]
    if any(order.order_type != "market" for order in entries):
        raise StrategyRuntimeError(
            "deferred strategy.exit for price-based entries requires continuous fill activation; "
            "only pending market entries are supported"
        )
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
        if len(matching) > 1 and (payload.profit is not None or payload.loss is not None):
            raise StrategyRuntimeError(
                "deferred relative exits with repeated entry IDs require per-trade price levels"
            )
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
        # These commands existed before the fill. Preserve their creation clock,
        # so process_orders_on_close cannot delay a previously submitted bracket.
        for order in engine.orders[before:]:
            order.created_bar_index = template["bar_index"]
            order.created_time = template["time"]
