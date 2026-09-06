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
    stop_price: float | None = None
    take_profit_price: float | None = None
    phase: str | None = None
    # Entry size is stable when qty becomes the remaining quantity after partial exits.
    entry_qty: float | None = None
    # Stable fill identity distinguishes pyramided lots, even on the same bar/ID.
    entry_fill_index: int | None = None
    exit_parent_id: str | None = None

    def __post_init__(self) -> None:
        if self.entry_qty is None:
            self.entry_qty = self.qty
