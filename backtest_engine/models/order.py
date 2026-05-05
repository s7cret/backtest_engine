from dataclasses import dataclass
from typing import Literal


@dataclass(slots=True)
class Order:
    id: str
    kind: Literal["entry", "order", "exit", "close"]
    direction: Literal["long", "short"]
    side: Literal["buy", "sell"]
    position_effect: Literal["open", "reduce", "close", "reverse"]
    order_type: Literal["market", "limit", "stop", "stop_limit"]
    qty: float
    created_bar_index: int
    created_time: int
    active_from_bar_index: int
    position_direction: Literal["long", "short", "flat"] | None = None
    reduce_only: bool = False
    limit_price: float | None = None
    stop_price: float | None = None
    from_entry: str | None = None
    oca_name: str | None = None
    oca_type: Literal["cancel", "reduce", "none"] = "none"
    reserved_qty: float = 0.0
    parent_exit_id: str | None = None
    status: Literal["pending", "active", "filled", "cancelled", "expired", "rejected"] = "pending"
    comment: str | None = None
    immediately: bool = False
    stop_limit_activated: bool = False
    trail_price: float | None = None
    trail_points: float | None = None
    trail_offset: float | None = None
    trail_activated: bool = False
    qty_is_default: bool = False
