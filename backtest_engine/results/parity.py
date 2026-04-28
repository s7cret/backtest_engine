from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParityTolerance:
    price: float = 0.0
    qty: float = 0.0

    def price_equal(self, actual: float, expected: float) -> bool:
        return abs(actual - expected) <= self.price

    def qty_equal(self, actual: float, expected: float) -> bool:
        return abs(actual - expected) <= self.qty
