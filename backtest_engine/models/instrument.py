from dataclasses import dataclass
from typing import Literal
from backtest_engine.errors import UnsupportedInstrumentModelError


@dataclass(frozen=True)
class InstrumentModel:
    mode: Literal["spot", "linear_futures", "inverse_futures"] = "linear_futures"
    contract_size: float = 1.0
    base_currency: str | None = None
    quote_currency: str = "USDT"
    settlement_currency: str = "USDT"

    def pnl(
        self, entry_price: float, exit_price: float, qty: float, direction: Literal["long", "short"]
    ) -> float:
        side = 1 if direction == "long" else -1
        if self.mode in ("spot", "linear_futures"):
            return (exit_price - entry_price) * qty * self.contract_size * side
        if self.mode == "inverse_futures":
            return (1 / entry_price - 1 / exit_price) * qty * self.contract_size * side
        raise UnsupportedInstrumentModelError(self.mode)
