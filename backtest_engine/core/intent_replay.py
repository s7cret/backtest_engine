"""Replay canonical intent.v2 events into engine StrategyContext."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping


class IntentReplayError(ValueError):
    """Tape cannot be applied without guessing."""


def _qty(value: object) -> float | None:
    if value is None:
        return None
    return float(Decimal(str(value)))


def _command_id(event: Mapping[str, Any]) -> str:
    key = str(event.get("idempotency_key") or "")
    parts = key.split(":")
    if len(parts) < 2:
        raise IntentReplayError("idempotency_key must end with :command_id:bar_index")
    return parts[-2]


def _direction(event: Mapping[str, Any]) -> str:
    origin = str(event.get("origin_command_kind") or "")
    if "." in origin:
        candidate = origin.rsplit(".", 1)[-1]
        if candidate in {"long", "short"}:
            return candidate
    raise IntentReplayError("entry/order requires origin_command_kind with direction")


def apply_intent(ctx: Any, event: Mapping[str, Any]) -> None:
    kind = str(event["kind"])
    command_id = _command_id(event)
    if kind == "entry":
        ctx.entry(
            command_id,
            _direction(event),
            qty=_qty(event.get("qty")),
            limit=_qty(event.get("limit")),
            stop=_qty(event.get("stop")),
            oca_name=event.get("oca_name"),
            oca_type=event.get("oca_type"),
            comment=event.get("comment"),
        )
        return
    if kind == "order":
        ctx.order(
            command_id,
            _direction(event),
            qty=_qty(event.get("qty")),
            limit=_qty(event.get("limit")),
            stop=_qty(event.get("stop")),
            oca_name=event.get("oca_name"),
            oca_type=event.get("oca_type"),
            comment=event.get("comment"),
        )
        return
    if kind == "exit":
        ctx.exit(
            command_id,
            from_entry=event.get("from_entry"),
            qty=_qty(event.get("qty")),
            limit=_qty(event.get("limit")),
            stop=_qty(event.get("stop")),
        )
        return
    if kind == "close":
        ctx.close(event.get("from_entry") or command_id, qty=_qty(event.get("qty")))
        return
    if kind == "cancel":
        ctx.cancel(command_id)
        return
    if kind == "cancel_all":
        ctx.cancel_all()
        return
    if kind == "risk":
        return
    raise IntentReplayError(f"unknown intent kind {kind!r}")


def apply_intents_for_bar(
    ctx: Any, events: list[Mapping[str, Any]], bar_index: int
) -> int:
    applied = 0
    for event in events:
        if int(event.get("bar_index", -1)) != bar_index:
            continue
        apply_intent(ctx, event)
        applied += 1
    return applied
