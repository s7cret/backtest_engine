"""Detached broker/ledger projection for interactive strategy workers."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any


def build_strategy_ledger_projection(engine: Any) -> dict[str, Any]:
    """Return the canonical JSON-safe StrategyLedgerView payload."""

    open_trade_log = [_project_trade(engine, trade, is_open=True) for trade in engine.open_trades]
    closed_trade_log = [
        _project_trade(engine, trade, is_open=False) for trade in engine.closed_trades
    ]
    position_entry_name = str(engine.open_trades[0].entry_id) if engine.open_trades else None
    return {
        "cash": float(engine.cash),
        "equity": float(engine.equity),
        "netprofit": float(engine.state.net_profit),
        "openprofit": float(engine.state.open_profit),
        "grossprofit": float(engine.state.gross_profit),
        "grossloss": float(engine.state.gross_loss),
        "position_size": float(engine.position.size),
        "position_avg_price": float(engine.position.avg_price),
        "position_entry_name": position_entry_name,
        "position_direction": str(engine.position.direction),
        "opentrades": len(open_trade_log),
        "closedtrades": len(closed_trade_log),
        "wintrades": int(engine.state.win_trades),
        "losstrades": int(engine.state.loss_trades),
        "eventrades": int(engine.state.even_trades),
        "max_drawdown": float(engine.max_drawdown),
        "max_runup": float(engine.max_runup),
        "orders": [asdict(order) for order in engine.orders],
        "fills": [asdict(fill) for fill in engine.fills],
        "open_trade_log": open_trade_log,
        "closed_trade_log": closed_trade_log,
    }


def compact_broker_projection(projection: dict[str, Any]) -> dict[str, Any]:
    """Derive the legacy compact broker view from the canonical payload."""

    return {
        "cash": projection["cash"],
        "equity": projection["equity"],
        "position": {
            "size": projection["position_size"],
            "avg_price": projection["position_avg_price"],
            "direction": projection["position_direction"],
            "open_profit": projection["openprofit"],
            "realized_profit": projection["netprofit"],
        },
        "max_drawdown": projection["max_drawdown"],
        "max_runup": projection["max_runup"],
    }


def compact_ledger_projection(projection: dict[str, Any]) -> dict[str, Any]:
    """Derive the legacy compact ledger view from the canonical payload."""

    return {
        "orders": [dict(order) for order in projection["orders"]],
        "fills": [dict(fill) for fill in projection["fills"]],
        "open_trades": [dict(trade) for trade in projection["open_trade_log"]],
        "closed_trades": [dict(trade) for trade in projection["closed_trade_log"]],
    }


def _project_trade(engine: Any, trade: Any, *, is_open: bool) -> dict[str, Any]:
    payload = asdict(trade)
    payload.update(
        {
            "commission": float(trade.commission_entry + trade.commission_exit),
            "side": str(trade.direction),
            "size": float(trade.qty if trade.direction == "long" else -trade.qty),
            "entry_comment": _trade_order_comment(
                engine.orders,
                order_id=trade.entry_id,
                at_bar_index=trade.entry_bar_index,
                is_entry=True,
            ),
            "exit_comment": (
                None
                if is_open or trade.exit_id is None or trade.exit_bar_index is None
                else _trade_order_comment(
                    engine.orders,
                    order_id=trade.exit_id,
                    at_bar_index=trade.exit_bar_index,
                    is_entry=False,
                )
            ),
            "max_runup": float(trade.max_runup or 0.0),
            "max_drawdown": float(trade.max_drawdown or 0.0),
        }
    )
    return payload


def _trade_order_comment(
    orders: list[Any],
    *,
    order_id: str,
    at_bar_index: int,
    is_entry: bool,
) -> str | None:
    position_effects = {"open", "reverse"} if is_entry else {"reduce", "close", "reverse"}
    candidates = [
        (index, order)
        for index, order in enumerate(orders)
        if order.id == order_id
        and order.position_effect in position_effects
        and order.created_bar_index <= at_bar_index
    ]
    if not candidates:
        return None
    _, order = max(
        candidates,
        key=lambda item: (
            item[1].created_bar_index,
            item[1].created_time,
            item[0],
        ),
    )
    return None if order.comment is None else str(order.comment)
