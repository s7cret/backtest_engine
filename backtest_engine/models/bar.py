from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True, slots=True)
class Bar:
    time: int; open: float; high: float; low: float; close: float; volume: float | None = None
