from __future__ import annotations

import time
from typing import Any, cast

from backtest_engine.config import BacktestConfig
from backtest_engine.core.backend_result import trade_from_backend_trade
from backtest_engine.errors import ConfigError
from backtest_engine.execution_backends.base import (
    BackendExecutionResult,
    ExecutionBackend,
)
from backtest_engine.models import BarSeries, Diagnostic, EquityPoint, Trade
from backtest_engine.results import equity_point, summarize_equity_curve
from backtest_engine.results.result import BacktestResult


def resolve_execution_backend(
    execution_backend: ExecutionBackend | str,
) -> ExecutionBackend:
    if isinstance(execution_backend, str):
        if execution_backend != "pine_runtime":
            raise ConfigError(f"unknown execution backend: {execution_backend}")
        from backtest_engine.execution_backends import PineRuntimeBackend

        return PineRuntimeBackend()
    return execution_backend


def ensure_executable_backend(
    execution_backend: ExecutionBackend | str,
) -> ExecutionBackend:
    backend = resolve_execution_backend(execution_backend)
    execute = getattr(backend, "execute", None)
    if not callable(execute):
        raise ConfigError("execution_backend must provide execute(...)")
    return cast(ExecutionBackend, backend)


def backend_equity_curve(
    backend_result: BackendExecutionResult,
    *,
    initial_capital: float,
) -> tuple[list[EquityPoint], list[EquityPoint]]:
    equity_curve: list[EquityPoint] = []
    score_equity_points: list[EquityPoint] = []
    peak = initial_capital
    trough = initial_capital
    for idx, item in enumerate(backend_result.bar_results):
        equity = item.equity
        if equity is None:
            continue
        equity = float(equity)
        peak = max(peak, equity)
        trough = min(trough, equity)
        open_profit = float(item.openprofit or 0.0)
        netprofit = float(item.netprofit or 0.0)
        point = equity_point(
            bar_index=idx,
            time=int(item.time),
            equity=equity,
            cash=equity - open_profit,
            position_size=float(item.position_size or 0.0),
            position_avg_price=item.position_avg_price,
            open_profit=open_profit,
            realized_profit=netprofit,
            peak=peak,
            trough=trough,
        )
        equity_curve.append(point)
        if item.phase == "score":
            score_equity_points.append(point)
    return equity_curve, score_equity_points


def backend_trades(backend_result: BackendExecutionResult) -> list[Trade]:
    return [
        trade_from_backend_trade(trade, idx)
        for idx, trade in enumerate(backend_result.trades)
    ]


def backend_runtime_warnings(
    backend_result: BackendExecutionResult,
) -> list[Diagnostic]:
    warnings: list[Diagnostic] = []
    diagnostics = backend_result.diagnostics
    for raw in diagnostics.get("runtime_diagnostics", []) or []:
        if isinstance(raw, dict):
            warnings.append(
                Diagnostic(
                    str(raw.get("code", "PINELIB_RUNTIME_DIAGNOSTIC")),
                    str(raw.get("message", raw)),
                    "warning",
                    context=dict(raw),
                )
            )
    return warnings


def apply_backend_result(
    engine: Any,
    backend_result: BackendExecutionResult,
    config: BacktestConfig,
) -> None:
    engine.closed_trades = backend_trades(backend_result)
    engine.open_trades = []
    equity_curve, score_points = backend_equity_curve(
        backend_result,
        initial_capital=config.initial_capital,
    )
    engine._backend_equity_curve = equity_curve
    engine._score_equity_points.extend(score_points)
    if equity_curve:
        summary = summarize_equity_curve(equity_curve)
        engine.equity = summary.final_equity
        engine.cash = summary.final_cash
        engine.max_drawdown = summary.max_drawdown
        engine.max_drawdown_percent = summary.max_drawdown_percent
        engine.trough_equity = summary.trough_equity
        engine.max_runup = summary.max_runup
        engine.max_runup_percent = summary.max_runup_percent
    engine.warnings.extend(backend_runtime_warnings(backend_result))


def run_execution_backend(
    engine: Any,
    execution_backend: ExecutionBackend | str,
    strategy_class: type,
    params: dict[str, Any],
    series: BarSeries,
    t0: float,
    effective_pre_bars: int,
    runtime_kwargs: dict[str, Any] | None = None,
) -> BacktestResult:
    backend = ensure_executable_backend(execution_backend)
    bars = [series.get_bar(i) for i in range(len(series))]
    backend_result = backend.execute(
        strategy_class,
        bars,
        config=engine.config,
        execution_window=None,
        effective_pre_bars=effective_pre_bars,
        runtime_kwargs=runtime_kwargs,
        params=params,
    )
    apply_backend_result(engine, backend_result, engine.config)
    result = engine._result(
        series,
        engine._backend_equity_curve,
        "completed",
        None,
        (time.perf_counter() - t0) * 1000,
        backend_result.raw_context,
        backend_result.raw_result,
    )
    result.plots = backend_result.plots
    if result.plots is not None:
        result.available_outputs.add("plots")
    result.bar_results = backend_result.bar_results
    result.performance["execution_backend"] = backend.name
    result.performance["backend_diagnostics"] = backend_result.diagnostics
    return result
