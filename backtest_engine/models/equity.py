from dataclasses import dataclass
@dataclass(slots=True)
class EquityPoint:
    bar_index:int; time:int; equity:float; cash:float; position_size:float; position_avg_price:float|None; open_profit:float; realized_profit:float; drawdown:float; drawdown_percent:float
