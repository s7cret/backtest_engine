from dataclasses import dataclass, field
from typing import Any
@dataclass(frozen=True)
class StrategyCommand:
    name:str; kwargs:dict[str,Any]
@dataclass
class CommandBuffer:
    commands:list[StrategyCommand]=field(default_factory=list)
    def add(self,name:str,**kwargs:Any)->None: self.commands.append(StrategyCommand(name,kwargs))
    def drain(self)->list[StrategyCommand]:
        out=self.commands; self.commands=[]; return out
