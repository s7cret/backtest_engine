from dataclasses import dataclass
from typing import Literal
@dataclass(slots=True)
class Position:
    size:float=0.0; avg_price:float=0.0; direction:Literal['long','short','flat']='flat'; open_profit:float=0.0; realized_profit:float=0.0
