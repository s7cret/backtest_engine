from dataclasses import dataclass
from typing import Literal


@dataclass(slots=True)
class Trade:
    id: str
    entry_id: str
    exit_id: str | None
    direction: Literal["long", "short"]
    entry_time: int
    entry_bar_index: int
    entry_price: float
    exit_time: int | None
    exit_bar_index: int | None
    exit_price: float | None
    qty: float
    commission_entry: float
    commission_exit: float
    profit: float
    profit_percent: float
    mfe: float | None = None
    mae: float | None = None
    max_runup: float | None = None
    max_drawdown: float | None = None
    exit_reason: str | None = None
    bars_held: int | None = None
    is_open: bool = False
