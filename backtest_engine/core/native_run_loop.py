"""Native strategy bar-loop orchestration for BacktestEngine."""

from __future__ import annotations

import time
from typing import Any

from backtest_engine.context import StrategyContext
from backtest_engine.models import BacktestResumeState, Bar, BarSeries, EquityPoint
from backtest_engine.results import BacktestResult


class NoopRuntime:
    def begin_bar(self, bar: Bar, bar_index: int) -> None:
        pass

    def end_bar(self) -> None:
        pass


def run_native_strategy(
    engine: Any,
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
        [] if engine._want("equity_curve") or engine.config.collect_equity_curve else None
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
                    "ORDER_ACTIVATED", f"order {order.id} activated", i, bar.time, order.id
                )
                engine._cb("on_order_activated", order)
        runtime.begin_bar(bar, i)
        engine._process_bar_fills(strategy, ctx, bar, i, open_only=True)
        engine._update_open_profit(bar.open)
        engine._update_state()
        engine._call_strategy(strategy, bar, i)
        engine._flush(ctx, bar, i)
        engine._process_bar_fills(strategy, ctx, bar, i, skip_open=True)
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
                engine.position.avg_price if engine.position.direction != "flat" else None,
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
    if engine.config.force_close_on_end and engine.position.direction != "flat" and len(series):
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


def _early_stop_state(engine: Any, bar_index: int, extremes: Any) -> tuple[bool, str, str | None]:
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
