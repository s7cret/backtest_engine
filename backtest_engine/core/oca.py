"""OCA order state transitions."""

from __future__ import annotations

from typing import Any

from backtest_engine.models import Bar, Order


def apply_oca(engine: Any, order: Order, bar: Bar, bar_index: int) -> None:
    if not order.oca_name:
        return
    for other in engine.orders:
        if (
            other is not order
            and other.status in ("pending", "active")
            and other.oca_name == order.oca_name
        ):
            if order.oca_type == "cancel":
                other.status = "cancelled"
                engine._cb("on_order_cancelled", other)
                engine._event(
                    "ORDER_CANCELLED",
                    f"OCA cancelled order {other.id}",
                    bar_index,
                    bar.time,
                    other.id,
                )
            elif order.oca_type == "reduce":
                other.qty = max(0.0, other.qty - order.qty)
                if other.qty <= 0:
                    other.status = "cancelled"
                    engine._cb("on_order_cancelled", other)
                    engine._event(
                        "ORDER_CANCELLED",
                        f"OCA reduced order {other.id} to zero",
                        bar_index,
                        bar.time,
                        other.id,
                    )
                else:
                    engine._event(
                        "ORDER_MODIFIED",
                        f"OCA reduced order {other.id}",
                        bar_index,
                        bar.time,
                        other.id,
                    )
