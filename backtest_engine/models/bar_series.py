from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence, Iterable, Mapping, Any
from .bar import Bar
@dataclass(frozen=True)
class BarSeries:
    time: Sequence[int]; open: Sequence[float]; high: Sequence[float]; low: Sequence[float]; close: Sequence[float]; volume: Sequence[float | None] | None = None
    def __post_init__(self) -> None:
        n=len(self.time)
        if not all(len(x)==n for x in (self.open,self.high,self.low,self.close)): raise ValueError('BarSeries arrays must have equal length')
        if self.volume is not None and len(self.volume)!=n: raise ValueError('BarSeries volume length must match')
    def __len__(self)->int: return len(self.time)
    def get_bar(self,index:int)->Bar: return Bar(int(self.time[index]),float(self.open[index]),float(self.high[index]),float(self.low[index]),float(self.close[index]),None if self.volume is None else self.volume[index])
    @classmethod
    def from_bars(cls,bars:Iterable[Bar])->'BarSeries':
        b=list(bars); return cls([x.time for x in b],[x.open for x in b],[x.high for x in b],[x.low for x in b],[x.close for x in b],[x.volume for x in b])
    @classmethod
    def from_records(cls,rows:Iterable[Mapping[str,Any]])->'BarSeries':
        return cls.from_bars(Bar(int(r['time']),float(r['open']),float(r['high']),float(r['low']),float(r['close']),None if r.get('volume') is None else float(r['volume'])) for r in rows)
