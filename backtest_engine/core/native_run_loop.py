"""Native strategy bar-loop orchestration for BacktestEngine."""

from __future__ import annotations

import time
from typing import Any, Protocol

from backtest_engine.config import BacktestConfig
from backtest_engine.context import StrategyContext
from backtest_engine.context import StrategyStateView
from backtest_engine.models import (
    BacktestResumeState,
    Bar,
    BarSeries,
    EquityPoint,
    Order,
    Position,
)
from backtest_engine.results import BacktestResult, EquityExtremes


class NativeRunEngine(Protocol):
    config: BacktestConfig
    state: StrategyStateView
    orders: list[Order]
    position: Position
    cash: float
    equity: float
    _score_mode: bool
    _score_start_index: int
    _score_equity_points: list[EquityPoint]
    _early_stop_enabled: bool
    _min_equity_stop: float | None
    _max_drawdown_stop_percent: float | None
    _max_drawdown_stop_cash: float | None
    _max_bars_without_trade: int | None
    last_trade_bar: int | None

    def _want(self, name: str) -> bool: ...
    def _restore_resume_state(
        self,
        resume_state: BacktestResumeState,
        strategy: Any,
        runtime: Any,
        ctx: StrategyContext,
    ) -> int: ...
    def _cb(self, name: str, *args: Any) -> None: ...
    def _event(
        self,
        code: str,
        message: str,
        bar_index: int | None = None,
        time: int | None = None,
        order_id: str | None = None,
    ) -> None: ...
    def _process_bar_fills(
        self,
        strategy: Any,
        ctx: StrategyContext,
        bar: Bar,
        i: int,
        *,
        open_only: bool = False,
        skip_open: bool = False,
        close_activation_only: bool = False,
        skip_trailing: bool = False,
        trailing_only: bool = False,
    ) -> None: ...
    def _update_open_profit(self, price: float) -> None: ...
    def _update_state(self) -> None: ...
    def _call_strategy(self, strategy: Any, bar: Bar, i: int) -> None: ...
    def _flush(
        self,
        ctx: StrategyContext,
        bar: Bar,
        i: int,
        *,
        recalc_after_fill: bool = False,
    ) -> None: ...
    def _update_intrabar_drawdown(self, bar: Bar) -> None: ...
    def _update_trade_excursions(self, bar: Bar) -> None: ...
    def _update_equity_extremes(self, equity: float) -> EquityExtremes: ...
    def _force_close(self, bar: Bar, bar_index: int) -> None: ...
    def _result(
        self,
        series: BarSeries,
        equity_curve: list[EquityPoint] | None,
        status: str,
        early_reason: str | None,
        duration_ms: float,
        strategy: Any | None = None,
        runtime: Any | None = None,
    ) -> BacktestResult: ...


class NoopRuntime:
    def begin_bar(self, bar: Bar, bar_index: int) -> None:
        pass

    def end_bar(self) -> None:
        pass


def run_native_strategy(
    engine: NativeRunEngine,
    strategy_class: type,
    params: dict[str, Any],
    series: BarSeries,
    t0: float,
    resume_state: BacktestResumeState | None,
) -> BacktestResult:
    ctx = StrategyContext(engine.config, engine.state)
    runtime = engine.config.runtime or NoopRuntime()
    try:
        strategy = strategy_class(params=params, runtime=runtime, ctx=ctx)
    except TypeError:
        strategy = strategy_class(params, runtime)
        strategy.ctx = ctx

    start_index = 0
    if resume_state is not None:
        start_index = engine._restore_resume_state(resume_state, strategy, runtime, ctx)

    equity_curve = (
        []
        if engine._want("equity_curve") or engine.config.collect_equity_curve
        else None
    )
    status = "completed"
    early_reason = None
    for i in range(start_index, len(series)):
        bar = series.get_bar(i)
        engine._cb("on_bar_start", bar, i)
        for order in engine.orders:
            if order.status == "pending" and order.active_from_bar_index <= i:
                order.status = "active"
                engine._event(
                    "ORDER_ACTIVATED",
                    f"order {order.id} activated",
                    i,
                    bar.time,
                    order.id,
                )
                engine._cb("on_order_activated", order)
        runtime.begin_bar(bar, i)
        engine._process_bar_fills(strategy, ctx, bar, i, open_only=True)
        engine._process_bar_fills(strategy, ctx, bar, i, skip_open=True)
        engine._update_open_profit(bar.close)
        engine._update_state()
        engine._call_strategy(strategy, bar, i)
        engine._flush(ctx, bar, i)
        if engine.config.process_orders_on_close or engine.config.calc_on_order_fills:
            engine._process_bar_fills(strategy, ctx, bar, i, skip_open=True)
        else:
            engine._process_bar_fills(
                strategy,
                ctx,
                bar,
                i,
                skip_open=True,
                close_activation_only=True,
            )
        engine._update_intrabar_drawdown(bar)
        engine._update_open_profit(bar.close)
        engine._update_trade_excursions(bar)
        engine._update_state()
        extremes = engine._update_equity_extremes(engine.equity)
        engine._update_state()
        if equity_curve is not None:
            point = EquityPoint(
                i,
                bar.time,
                engine.equity,
                engine.cash,
                engine.position.size,
                (
                    engine.position.avg_price
                    if engine.position.direction != "flat"
                    else None
                ),
                engine.position.open_profit,
                engine.position.realized_profit,
                extremes.drawdown,
                extremes.drawdown_percent,
                extremes.runup,
                extremes.runup_percent,
            )
            equity_curve.append(point)
            if engine._score_mode and i >= engine._score_start_index:
                engine._score_equity_points.append(point)
            engine._cb("on_equity", point)
        stop_now, status, early_reason = _early_stop_state(engine, i, extremes)
        runtime.end_bar()
        engine._cb("on_bar_end", bar, i, engine.state)
        if stop_now:
            break

    finalize = getattr(strategy, "_finalize", None)
    if callable(finalize):
        finalize()
    if (
        engine.config.force_close_on_end
        and engine.position.direction != "flat"
        and len(series)
    ):
        engine._force_close(series.get_bar(len(series) - 1), len(series) - 1)
    return engine._result(
        series,
        equity_curve,
        status,
        early_reason,
        (time.perf_counter() - t0) * 1000,
        strategy,
        runtime,
    )


def _early_stop_state(
    engine: NativeRunEngine, bar_index: int, extremes: EquityExtremes
) -> tuple[bool, str, str | None]:
    if not engine._early_stop_enabled:
        return False, "completed", None
    if engine._min_equity_stop is not None and engine.equity <= engine._min_equity_stop:
        return True, "early_stopped", "min_equity_stop"
    if (
        engine._max_drawdown_stop_percent is not None
        and extremes.drawdown_percent >= engine._max_drawdown_stop_percent
    ):
        return True, "early_stopped", "max_drawdown_stop_percent"
    if (
        engine._max_drawdown_stop_cash is not None
        and extremes.drawdown >= engine._max_drawdown_stop_cash
    ):
        return True, "early_stopped", "max_drawdown_stop_cash"
    if (
        engine._max_bars_without_trade is not None
        and engine.last_trade_bar is not None
        and bar_index - engine.last_trade_bar >= engine._max_bars_without_trade
    ):
        return True, "early_stopped", "max_bars_without_trade"
    return False, "completed", None
