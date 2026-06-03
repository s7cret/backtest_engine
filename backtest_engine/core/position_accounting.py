"""Position and trade accounting for filled orders."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from backtest_engine.models import Bar, Order, Position, Trade


def apply_position(
    engine: Any,
    order: Order,
    price: float,
    bar: Bar,
    bar_index: int,
    commission: float,
    *,
    fill_point: str = "",
) -> str:
    signed = order.qty if order.side == "buy" else -order.qty
    if engine.position.direction == "flat" or (engine.position.size == 0):
        return _open_position(engine, order, signed, price, bar, bar_index, commission)
    current_sign = 1 if engine.position.direction == "long" else -1
    if signed * current_sign > 0:
        return _add_to_position(engine, order, signed, price, bar, bar_index, commission)
    return _reduce_or_reverse_position(
        engine,
        order,
        signed,
        current_sign,
        price,
        bar,
        bar_index,
        commission,
        fill_point=fill_point,
    )


def _open_position(
    engine: Any,
    order: Order,
    signed: float,
    price: float,
    bar: Bar,
    bar_index: int,
    commission: float,
) -> str:
    engine.position.size = signed
    engine.position.direction = "long" if signed > 0 else "short"
    engine.position.avg_price = price
    trade = Trade(
        order.id,
        order.id,
        None,
        engine.position.direction,
        bar.time,
        bar_index,
        price,
        None,
        None,
        None,
        abs(signed),
        commission,
        0.0,
        -commission,
        0.0,
        is_open=True,
    )
    engine.open_trades.append(trade)
    engine._cb("on_trade_open", trade)
    return engine.position.direction


def _add_to_position(
    engine: Any,
    order: Order,
    signed: float,
    price: float,
    bar: Bar,
    bar_index: int,
    commission: float,
) -> str:
    new_abs = abs(engine.position.size) + abs(signed)
    engine.position.avg_price = (
        engine.position.avg_price * abs(engine.position.size) + price * abs(signed)
    ) / new_abs
    engine.position.size += signed
    trade = Trade(
        order.id,
        order.id,
        None,
        engine.position.direction,
        bar.time,
        bar_index,
        price,
        None,
        None,
        None,
        abs(signed),
        commission,
        0.0,
        -commission,
        0.0,
        is_open=True,
    )
    engine.open_trades.append(trade)
    engine._cb("on_trade_open", trade)
    return engine.position.direction


def _reduce_or_reverse_position(
    engine: Any,
    order: Order,
    signed: float,
    current_sign: int,
    price: float,
    bar: Bar,
    bar_index: int,
    commission: float,
    *,
    fill_point: str = "",
) -> str:
    qty_close = min(abs(signed), abs(engine.position.size))
    targets = [
        trade
        for trade in engine.open_trades
        if order.from_entry is None or trade.entry_id == order.from_entry
    ]
    if not targets:
        engine._diag(
            "ORDER_REJECTED_NO_MATCHING_ENTRY",
            "reduce order has no matching from_entry",
            "warning",
            bar_index,
            bar.time,
            order.id,
        )
        return engine.position.direction
    # A market close/reversal command must be able to flatten the current
    # position even when reduce-only strategy.exit orders are reserving it.
    reserved = (
        {}
        if order.position_effect in {"close", "reverse"}
        else engine._reserved_qty_by_entry(exclude_order=order)
    )
    target_caps = {
        id(trade): max(
            0.0,
            trade.qty - (reserved.get(trade.entry_id, 0.0) if order.from_entry is None else 0.0),
        )
        for trade in targets
    }
    targets = [trade for trade in targets if target_caps[id(trade)] > 0]
    if not targets:
        engine._diag(
            "ORDER_REJECTED_NO_AVAILABLE_POSITION_QTY",
            "reduce order has no matching unreserved qty",
            "warning",
            bar_index,
            bar.time,
            order.id,
        )
        return engine.position.direction
    qty_close = min(qty_close, sum(target_caps[id(trade)] for trade in targets))
    gross = _gross_close_profit(engine, targets, target_caps, qty_close, price)
    exit_commission_total = commission * (qty_close / order.qty) if order.qty else commission
    opening_commission = max(0.0, commission - exit_commission_total)
    engine.cash += gross
    engine.position.realized_profit += gross
    _close_target_trades(
        engine,
        order,
        targets,
        target_caps,
        qty_close,
        exit_commission_total,
        price,
        bar,
        bar_index,
        fill_point=fill_point,
    )
    _cancel_orphaned_exit_orders(engine, order, bar, bar_index)
    engine.position.size += signed
    if abs(engine.position.size) < 1e-12:
        engine.position = Position(realized_profit=engine.position.realized_profit)
        return "flat"
    same_direction = [
        trade for trade in engine.open_trades if trade.direction == engine.position.direction
    ]
    if same_direction:
        qty = sum(trade.qty for trade in same_direction)
        engine.position.avg_price = (
            sum(trade.entry_price * trade.qty for trade in same_direction) / qty
        )
    if engine.position.size * current_sign < 0:
        engine.position.direction = "long" if engine.position.size > 0 else "short"
        engine.position.avg_price = price
        _record_reversal_entry(engine, order, price, bar, bar_index, opening_commission)
    return engine.position.direction


def _cancel_orphaned_exit_orders(engine: Any, filled_order: Order, bar: Bar, bar_index: int) -> None:
    for order in engine.orders:
        if order is filled_order:
            continue
        if order.kind != "exit" or order.status not in {"pending", "active"}:
            continue
        if order.from_entry is None or engine._matching_open_trades(order.from_entry):
            continue
        order.status = "cancelled"
        engine._cb("on_order_cancelled", order)
        engine._event(
            "ORDER_CANCELLED",
            f"orphaned exit order {order.id} cancelled",
            bar_index,
            bar.time,
            order.id,
        )


def _gross_close_profit(
    engine: Any,
    targets: list[Trade],
    target_caps: dict[int, float],
    qty_close: float,
    price: float,
) -> float:
    gross = 0.0
    remaining = qty_close
    for trade in targets:
        if remaining <= 0:
            break
        qty = min(target_caps[id(trade)], remaining)
        gross += engine.instrument.pnl(trade.entry_price, price, qty, trade.direction)
        remaining -= qty
    return gross


def _close_target_trades(
    engine: Any,
    order: Order,
    targets: list[Trade],
    target_caps: dict[int, float],
    qty_close: float,
    exit_commission_total: float,
    price: float,
    bar: Bar,
    bar_index: int,
    *,
    fill_point: str = "",
) -> None:
    remaining = qty_close
    for trade in list(targets):
        if remaining <= 0:
            break
        qty = min(target_caps[id(trade)], remaining)
        exit_commission = exit_commission_total * (qty / qty_close) if qty_close else 0.0
        entry_commission = trade.commission_entry * (qty / trade.qty) if trade.qty else 0.0
        profit = (
            engine.instrument.pnl(trade.entry_price, price, qty, trade.direction)
            - exit_commission
            - entry_commission
        )
        excursion_bar = _excursion_bar_for_close_fill(bar, price, fill_point)
        mfe, mae, max_runup, max_drawdown = engine._trade_excursion_values(
            trade,
            excursion_bar,
        )
        closed = replace(
            trade,
            exit_id=order.id,
            exit_time=bar.time,
            exit_bar_index=bar_index,
            exit_price=price,
            qty=qty,
            commission_entry=entry_commission,
            commission_exit=exit_commission,
            profit=profit,
            profit_percent=(
                profit / (trade.entry_price * qty) * 100 if trade.entry_price * qty else 0.0
            ),
            mfe=mfe,
            mae=mae,
            max_runup=max_runup,
            max_drawdown=max_drawdown,
            exit_reason=order.id,
            bars_held=bar_index - trade.entry_bar_index,
            is_open=False,
        )
        engine.closed_trades.append(closed)
        engine._cb("on_trade_close", closed)
        trade.qty -= qty
        trade.commission_entry -= entry_commission
        remaining -= qty
        if trade.qty <= 1e-12:
            engine.open_trades.remove(trade)


def _excursion_bar_for_close_fill(bar: Bar, price: float, fill_point: str) -> Bar:
    """Bound final trade excursion to the portion of the bar where the trade lived."""

    if fill_point != "open":
        return bar
    return Bar(
        time=bar.time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=bar.volume,
        time_close=bar.time_close,
    )


def _record_reversal_entry(
    engine: Any,
    order: Order,
    price: float,
    bar: Bar,
    bar_index: int,
    opening_commission: float,
) -> None:
    trade = Trade(
        order.id,
        order.id,
        None,
        engine.position.direction,
        bar.time,
        bar_index,
        price,
        None,
        None,
        None,
        abs(engine.position.size),
        opening_commission,
        0.0,
        -opening_commission,
        0.0,
        is_open=True,
    )
    engine.open_trades.append(trade)
    engine._cb("on_trade_open", trade)
