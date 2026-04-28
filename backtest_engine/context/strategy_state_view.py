from dataclasses import dataclass
@dataclass
class StrategyStateView:
    position_size:float=0.0; position_avg_price:float|None=None; position_direction:str='flat'; equity:float=0.0; initial_capital:float=0.0; cash:float=0.0; open_profit:float=0.0; net_profit:float=0.0; gross_profit:float=0.0; gross_loss:float=0.0; closed_trades:int=0; open_trades:int=0
