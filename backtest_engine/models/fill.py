from dataclasses import dataclass
from typing import Literal


@dataclass(slots=True)
class Fill:
    order_id: str
    bar_index: int
    time: int
    price: float
    qty: float
    direction: Literal["long", "short"]
    side: Literal["buy", "sell"]
    position_effect: Literal["open", "reduce", "close", "reverse"]
    position_direction_before: Literal["long", "short", "flat"]
    position_direction_after: Literal["long", "short", "flat"]
    reason: str
    commission: float
    slippage_value: float
    intrabar_point: str | None = None
