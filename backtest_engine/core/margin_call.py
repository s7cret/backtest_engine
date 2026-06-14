"""Margin-call liquidation helper."""

from __future__ import annotations

from typing import Any

from backtest_engine.broker.rounding import round_to_step
from backtest_engine.models import Bar, Order


def maybe_margin_call(
    engine: Any, price: float, bar: Bar, bar_index: int, point: str
) -> bool:
    if engine.position.direction == "flat" or engine.position.avg_price is None:
        return False
    margin_percent = (
        engine.config.margin_long
        if engine.position.direction == "long"
        else engine.config.margin_short
    )
    if margin_percent >= 100.0:
        return False
    margin_ratio = margin_percent / 100.0
    qty_abs = abs(engine.position.size)
    if qty_abs <= 0.0 or price <= 0.0 or margin_ratio <= 0.0:
        return False
    engine._update_open_profit(price)
    margin_required = price * qty_abs * margin_ratio
    available_funds = engine.equity - margin_required
    if available_funds > 1e-12:
        return False
    cover_raw = (-available_funds / margin_ratio) / price
    liquidation_qty = 1.0 if cover_raw < 1.0 else float(int(cover_raw) * 4)
    if engine.config.qty_step:
        liquidation_qty = round_to_step(
            liquidation_qty, engine.config.qty_step, "floor"
        )
    liquidation_qty = min(qty_abs, liquidation_qty)
    if liquidation_qty <= 0.0:
        return False
    order = Order(
        "Margin call",
        "close",
        engine.position.direction,
        "sell" if engine.position.direction == "long" else "buy",
        "close",
        "market",
        liquidation_qty,
        bar_index,
        bar.time,
        bar_index,
        engine.position.direction,
        True,
        immediately=True,
    )
    engine._fill(order, bar, bar_index, price, point)
    engine._event(
        "MARGIN_CALL",
        f"margin call liquidated {liquidation_qty}",
        bar_index,
        bar.time,
        order.id,
    )
    return True
