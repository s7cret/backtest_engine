from __future__ import annotations

from dataclasses import dataclass

from backtest_engine.config import BacktestConfig


@dataclass(frozen=True)
class EarlyStopDecision:
    should_stop: bool
    reason: str | None = None


class EarlyStopChecker:
    """Evaluates configured run stop conditions after each completed bar."""

    def __init__(self, config: BacktestConfig):
        self.config = config

    def check(
        self,
        *,
        equity: float,
        drawdown_percent: float,
        bar_index: int,
        last_trade_bar: int | None,
    ) -> EarlyStopDecision:
        if not self.config.early_stop_enabled:
            return EarlyStopDecision(False)
        if (
            self.config.min_equity_stop is not None
            and equity <= self.config.min_equity_stop
        ):
            return EarlyStopDecision(True, "min_equity_stop")
        if (
            self.config.max_drawdown_stop_percent is not None
            and drawdown_percent >= self.config.max_drawdown_stop_percent
        ):
            return EarlyStopDecision(True, "max_drawdown_stop_percent")
        if (
            self.config.max_bars_without_trade is not None
            and last_trade_bar is not None
            and bar_index - last_trade_bar >= self.config.max_bars_without_trade
        ):
            return EarlyStopDecision(True, "max_bars_without_trade")
        return EarlyStopDecision(False)
