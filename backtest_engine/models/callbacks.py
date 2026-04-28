from dataclasses import dataclass
from typing import Callable
from .bar import Bar
from .order import Order
from .fill import Fill
from .diagnostic import Diagnostic
@dataclass
class BacktestCallbacks:
    on_bar_start:Callable[[Bar,int],None]|None=None; on_bar_end:Callable[[Bar,int,object],None]|None=None; on_order_created:Callable[[Order],None]|None=None; on_order_activated:Callable[[Order],None]|None=None; on_order_cancelled:Callable[[Order],None]|None=None; on_fill:Callable[[Fill],None]|None=None; on_diagnostic:Callable[[Diagnostic],None]|None=None
