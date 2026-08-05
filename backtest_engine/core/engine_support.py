from __future__ import annotations

from typing import Any, Literal

from backtest_engine.config import BacktestConfig
from backtest_engine.context import StrategyContext, StrategyStateView
from backtest_engine.core.resume_state import export_resume_state, restore_resume_state
from backtest_engine.core.result_builder import build_backtest_result
from backtest_engine.models import (
    BacktestCallbacks,
    BacktestResumeState,
    BarSeries,
    Diagnostic,
    EquityPoint,
    Position,
    Trade,
)
from backtest_engine.results import BacktestResult


class EngineSupportMixin:
    config: BacktestConfig
    callbacks: BacktestCallbacks
    position: Position
    state: StrategyStateView
    cash: float
    equity: float
    max_drawdown: float
    max_drawdown_percent: float
    max_runup: float
    max_runup_percent: float
    closed_trades: list[Trade]
    open_trades: list[Trade]
    events: list[Diagnostic]
    warnings: list[Diagnostic]
    errors: list[Diagnostic]
    _callbacks_disabled: bool
    _closed_trade_stats_count: int
    _gross_profit_total: float
    _gross_loss_total: float
    _win_trades_total: int
    _loss_trades_total: int
    _even_trades_total: int

    def _update_state(self) -> None:
        if len(self.closed_trades) < self._closed_trade_stats_count:
            self._closed_trade_stats_count = 0
            self._gross_profit_total = 0.0
            self._gross_loss_total = 0.0
            self._win_trades_total = 0
            self._loss_trades_total = 0
            self._even_trades_total = 0
        while self._closed_trade_stats_count < len(self.closed_trades):
            trade = self.closed_trades[self._closed_trade_stats_count]
            profit = trade.profit
            if profit > 0:
                self._gross_profit_total += profit
                self._win_trades_total += 1
            elif profit < 0:
                self._gross_loss_total += abs(profit)
                self._loss_trades_total += 1
            else:
                self._even_trades_total += 1
            self._closed_trade_stats_count += 1
        self.state.position_size = self.position.size
        self.state.position_avg_price = (
            None if self.position.direction == "flat" else self.position.avg_price
        )
        self.state.position_direction = self.position.direction
        self.state.cash = self.cash
        self.state.equity = self.equity
        self.state.open_profit = self.position.open_profit
        self.state.net_profit = self.position.realized_profit
        self.state.gross_profit = self._gross_profit_total
        self.state.gross_loss = self._gross_loss_total
        self.state.max_drawdown = self.max_drawdown
        self.state.max_drawdown_percent = self.max_drawdown_percent
        self.state.max_runup = self.max_runup
        self.state.max_runup_percent = self.max_runup_percent
        self.state.closed_trades = len(self.closed_trades)
        self.state.open_trades = len(self.open_trades)
        self.state.win_trades = self._win_trades_total
        self.state.loss_trades = self._loss_trades_total
        self.state.even_trades = self._even_trades_total

    def _want(self, name: str) -> bool:
        return name in self.config.required_outputs

    def _event(self, code, msg, i=None, t=None, oid=None) -> None:
        if self.config.collect_events:
            self.events.append(Diagnostic(code, msg, "info", i, t, oid))

    def _diag(self, code, msg, severity, i=None, t=None, oid=None) -> None:
        d = Diagnostic(code, msg, severity, i, t, oid)
        (self.errors if severity == "error" else self.warnings).append(d)
        self._cb("on_diagnostic", d)

    def _cb(self, name: str, *args: Any) -> None:
        fn = getattr(self.callbacks, name, None)
        if fn and not self._callbacks_disabled:
            try:
                fn(*args)
            except Exception as e:
                if self.config.callback_error_policy == "raise":
                    raise
                self.warnings.append(Diagnostic("CALLBACK_ERROR", str(e), "warning"))
                if self.config.callback_error_policy == "disable_callbacks":
                    self._callbacks_disabled = True

    def _restore_resume_state(
        self,
        resume_state: BacktestResumeState,
        strategy: Any,
        runtime: Any,
        ctx: StrategyContext,
        series: BarSeries,
    ) -> int:
        return restore_resume_state(self, resume_state, strategy, runtime, ctx, series)

    def _export_resume_state(
        self,
        bar_index: int,
        strategy: Any | None = None,
        runtime: Any | None = None,
        series: BarSeries | None = None,
    ) -> BacktestResumeState:
        return export_resume_state(self, bar_index, strategy, runtime, series)

    def _result(
        self,
        series: BarSeries,
        equity_curve: list[EquityPoint] | None,
        status: Literal["completed", "failed", "early_stopped"],
        reason: str | None,
        ms: float,
        strategy: Any | None = None,
        runtime: Any | None = None,
    ) -> BacktestResult:
        return build_backtest_result(
            self,
            series,
            equity_curve,
            status,
            reason,
            ms,
            strategy,
            runtime,
        )
