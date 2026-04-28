from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Any
from .bar import Bar
from .order import Order
from .fill import Fill
from .trade import Trade
from .equity import EquityPoint
from .diagnostic import Diagnostic


@dataclass
class BacktestCallbacks:
    on_bar_start: Callable[[Bar, int], None] | None = None
    on_bar_end: Callable[[Bar, int, object], None] | None = None
    on_order_created: Callable[[Order], None] | None = None
    on_order_activated: Callable[[Order], None] | None = None
    on_order_cancelled: Callable[[Order], None] | None = None
    on_fill: Callable[[Fill], None] | None = None
    on_trade_open: Callable[[Trade], None] | None = None
    on_trade_update: Callable[[Trade], None] | None = None
    on_trade_close: Callable[[Trade], None] | None = None
    on_equity: Callable[[EquityPoint], None] | None = None
    on_diagnostic: Callable[[Diagnostic], None] | None = None
    # Reserved extension point for external collectors without changing constructor compatibility.
    extra: dict[str, Callable[..., Any]] | None = None
