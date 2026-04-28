from __future__ import annotations
from .command_buffer import CommandBuffer
from .strategy_state_view import StrategyStateView

class StrategyContext:
    def __init__(self, config:object, state:StrategyStateView|None=None):
        self.config=config; self.state=state or StrategyStateView(); self.buffer=CommandBuffer()
    def entry(self,id:str,direction:str,qty:float|None=None,limit:float|None=None,stop:float|None=None,oca_name:str|None=None,oca_type:str|None=None,comment:str|None=None)->None:
        self.buffer.add('entry', id=id,direction=direction,qty=qty,limit=limit,stop=stop,oca_name=oca_name,oca_type=oca_type,comment=comment)
    def order(self,id:str,direction:str,qty:float|None=None,limit:float|None=None,stop:float|None=None,oca_name:str|None=None,oca_type:str|None=None,comment:str|None=None)->None:
        self.buffer.add('order', id=id,direction=direction,qty=qty,limit=limit,stop=stop,oca_name=oca_name,oca_type=oca_type,comment=comment)
    def exit(self,id:str,from_entry:str|None=None,qty:float|None=None,qty_percent:float|None=None,limit:float|None=None,stop:float|None=None,profit:float|None=None,loss:float|None=None,trail_price:float|None=None,trail_points:float|None=None,trail_offset:float|None=None,oca_name:str|None=None,comment:str|None=None)->None:
        self.buffer.add('exit', id=id,from_entry=from_entry,qty=qty,qty_percent=qty_percent,limit=limit,stop=stop,profit=profit,loss=loss,trail_price=trail_price,trail_points=trail_points,trail_offset=trail_offset,oca_name=oca_name,comment=comment)
    def close(self,id:str,qty:float|None=None,qty_percent:float|None=None,immediately:bool=False,comment:str|None=None)->None:
        self.buffer.add('close', id=id,qty=qty,qty_percent=qty_percent,immediately=immediately,comment=comment)
    def close_all(self,immediately:bool=False,comment:str|None=None)->None:
        self.buffer.add('close_all', immediately=immediately,comment=comment)
    def cancel(self,id:str)->None: self.buffer.add('cancel', id=id)
    def cancel_all(self)->None: self.buffer.add('cancel_all')
