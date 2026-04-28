from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StrategyStateView:
    position_size: float = 0.0
    position_avg_price: float | None = None
    position_direction: str = 'flat'
    equity: float = 0.0
    initial_capital: float = 0.0
    cash: float = 0.0
    open_profit: float = 0.0
    net_profit: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_percent: float = 0.0
    closed_trades: int = 0
    open_trades: int = 0
    _open_trades_ref: list[Any] = field(default_factory=list, repr=False)
    _closed_trades_ref: list[Any] = field(default_factory=list, repr=False)

    @property
    def open_trades_count(self) -> int:
        return self.open_trades

    @property
    def closed_trades_count(self) -> int:
        return self.closed_trades

    def _open(self, index: int) -> Any:
        return self._open_trades_ref[index]

    def _closed(self, index: int) -> Any:
        return self._closed_trades_ref[index]

    def opentrades_entry_id(self, index: int) -> str:
        return self._open(index).entry_id

    def opentrades_entry_price(self, index: int) -> float:
        return self._open(index).entry_price

    def opentrades_size(self, index: int) -> float:
        return self._open(index).qty

    def opentrades_profit(self, index: int) -> float:
        return self._open(index).profit

    def closedtrades_entry_id(self, index: int) -> str:
        return self._closed(index).entry_id

    def closedtrades_exit_id(self, index: int) -> str | None:
        return self._closed(index).exit_id

    def closedtrades_profit(self, index: int) -> float:
        return self._closed(index).profit

    def closedtrades_entry_bar_index(self, index: int) -> int:
        return self._closed(index).entry_bar_index

    def closedtrades_exit_bar_index(self, index: int) -> int | None:
        return self._closed(index).exit_bar_index
