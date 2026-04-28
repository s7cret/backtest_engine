from dataclasses import dataclass
from typing import Literal
@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str; message: str; severity: Literal['info','warning','error']; bar_index:int|None=None; bar_time:int|None=None; order_id:str|None=None; trade_id:str|None=None; job_id:str|None=None; context:dict|None=None
