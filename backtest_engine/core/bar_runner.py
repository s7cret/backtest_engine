from __future__ import annotations

from dataclasses import dataclass, field

from backtest_engine.models import Bar, Order


@dataclass
class BarRunState:
    """Per-bar execution bookkeeping shared by engine helpers and tests."""

    bar: Bar
    bar_index: int
    activated_order_ids: list[str] = field(default_factory=list)
    filled_order_ids: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


def activate_orders_for_bar(orders: list[Order], bar_index: int) -> list[Order]:
    """Move pending orders whose activation index has arrived to active."""
    activated: list[Order] = []
    for order in orders:
        if order.status == 'pending' and order.active_from_bar_index <= bar_index:
            order.status = 'active'
            activated.append(order)
    return activated
