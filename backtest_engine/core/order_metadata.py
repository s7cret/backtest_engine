"""Select per-leg metadata once, when an order is created or amended.

Empty strings are explicit values, not a request to fall back. Fills/trades copy
these values; later ID reuse must not rewrite earlier execution history. This
module does not dispatch external notifications or expand alert templates.
"""

from __future__ import annotations

from typing import Any

EXIT_METADATA_FIELDS = (
    "comment_profit",
    "comment_loss",
    "comment_trailing",
    "alert_profit",
    "alert_loss",
    "alert_trailing",
)
ORDER_METADATA_FIELDS = ("comment", "alert_message", "disable_alert", "exit_leg")


def validate_metadata(payload: Any) -> None:
    for name in ("comment", "alert_message", *EXIT_METADATA_FIELDS):
        value = getattr(payload, name, None)
        if value is not None and type(value) is not str:
            raise ValueError(f"{name} must be a string or None")
    if type(payload.disable_alert) is not bool:
        raise ValueError("disable_alert must be a bool")


def execution_metadata(payload: Any, leg: str | None = None) -> dict[str, Any]:
    if leg not in (None, "profit", "loss", "trailing"):
        raise ValueError("unknown exit leg")
    comment, message = payload.comment, payload.alert_message
    if leg is not None:
        specific_comment = getattr(payload, "comment_" + leg)
        specific_message = getattr(payload, "alert_" + leg)
        if specific_comment is not None:
            comment = specific_comment
        if specific_message is not None:
            message = specific_message
    return dict(
        comment=comment, alert_message=message, disable_alert=payload.disable_alert, exit_leg=leg
    )


def copy_order_metadata(target: Any, source: Any) -> None:
    for name in ORDER_METADATA_FIELDS:
        setattr(target, name, getattr(source, name))


def filled_order_context(order: object, fill_index: int) -> dict:
    """Audit data on the fill event; a suppressed alert never suppresses the fill."""
    return {
        "schema_id": "backtest_engine.order_fill_metadata.v1",
        "fill_index": fill_index,
        "public_order_id": order.parent_exit_id or order.id,
        "comment": order.comment,
        "alert_message": order.alert_message,
        "disable_alert": order.disable_alert,
        "alert_eligible": not order.disable_alert,
        "exit_leg": order.exit_leg,
    }
